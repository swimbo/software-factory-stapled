"""Opt-in, limited offline npm-ci image builder; no release/provenance verdict.

A trusted Docker daemon, BuildKit image, local OCI base and host are required.
Daemon network.host may be enabled by Buildx, but this runner never requests an
insecure entitlement and the fixed recipe uses network=none. The privileged
BuildKit daemon's resistance to sandbox escape is not established here.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import select
import stat
import subprocess
import tarfile
import tempfile
import time

from controlled_npm import _BoundedTarInput, verify_frozen_npm

MAX_SOURCE = 256 * 1024 * 1024
MAX_META = 8 * 1024 * 1024
MAX_FILES = 50000
OCI_MANIFEST = 'application/vnd.oci.image.manifest.v1+json'
OCI_CONFIG = 'application/vnd.oci.image.config.v1+json'
LAYERS = {'application/vnd.oci.image.layer.v1.tar', 'application/vnd.oci.image.layer.v1.tar+gzip'}

# No frontend directive, candidate Dockerfile, host mount, secrets, SSH, cache
# mounts, or requested BuildKit insecure entitlements. Inputs are local only.
RECIPE = b'''FROM base
RUN --network=none --mount=type=bind,source=source.tar,target=/factory-input/source.tar \\
    --mount=type=bind,source=materials.tar,target=/factory-input/materials.tar \\
    --mount=type=bind,source=populate.cjs,target=/factory-input/populate.cjs \\
    mkdir -p /workspace /factory-work/materials /factory-work/cache && \\
    tar -xf /factory-input/source.tar -C /workspace && \\
    tar -xf /factory-input/materials.tar -C /factory-work/materials && \\
    node /factory-input/populate.cjs && \\
    cd /workspace && NODE_OPTIONS='' npm ci --offline --ignore-scripts --no-audit --no-fund --cache /factory-work/cache && \\
    rm -rf /factory-work
WORKDIR /workspace
'''

# npm's own cacache implementation writes the expected offline registry entries.
# No lifecycle script or application code is invoked by cache construction.
POPULATE = b'''const fs=require('fs');
const path=require('path');
const {pipeline}=require('stream/promises');
const cacache=require('/usr/local/lib/node_modules/npm/node_modules/cacache');
(async()=>{const root='/factory-work/materials';
 const manifest=JSON.parse(fs.readFileSync(path.join(root,'manifest.json'),'utf8'));
 for(const p of manifest.packages){
  const key='make-fetch-happen:request-cache:'+p.resolved;
  const metadata={time:Date.now(),url:p.resolved,reqHeaders:{},resHeaders:{'cache-control':'public, max-age=31536000'},options:{compress:true}};
  await pipeline(fs.createReadStream(path.join(root,'materials',p.sha512)),cacache.put.stream('/factory-work/cache/_cacache',key,{metadata}));
 }
})().catch(e=>{console.error(e);process.exit(1)});
'''


def _hash_file(path, expected, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError('unsafe input file')
        if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
            raise ValueError('input digest differs')


def _copy_checked(path, dest, expected, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        st = os.fstat(source.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
            raise ValueError('unsafe input file')
        total = 0; digest = hashlib.sha256()
        with open(dest, 'xb') as target:
            while chunk := source.read(1024 * 1024):
                total += len(chunk)
                if total > limit:raise ValueError('input exceeds size limit')
                digest.update(chunk);target.write(chunk)
        if digest.hexdigest() != expected:
            raise ValueError('input digest differs')


def _source_lock(source, revision):
    with open(source, 'rb') as stream, tarfile.open(fileobj=_BoundedTarInput(stream), mode='r:') as tar:
        members = {};total = 0
        for member in tar:
            name = member.name.rstrip('/')
            parts = name.split('/')
            if (len(members) >= MAX_FILES or name in members or not name or name.startswith('/') or
                    '\\' in name or any(p in ('', '.', '..') for p in parts) or
                    any(p in ('.npmrc', '.git', 'node_modules') for p in parts) or
                    member.mode & 0o7000 or member.uid != 0 or member.gid != 0 or
                    set(member.pax_headers) - {'path', 'mtime', 'comment'} or
                    ('comment' in member.pax_headers and member.pax_headers['comment'] != revision) or
                    not (member.isfile() or member.isdir())):
                raise ValueError('unsupported or unsafe source archive')
            total += member.size
            if total > MAX_SOURCE:raise ValueError('source archive too large')
            members[name] = member
        for name in ('package.json', 'package-lock.json'):
            member = members.get(name)
            if member is None or not member.isfile() or member.size > MAX_META:
                raise ValueError('root npm package files required')
        try:
            package = json.loads(tar.extractfile(members['package.json']).read())
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError('invalid package manifest') from exc
        if not isinstance(package, dict):raise ValueError('invalid package manifest')
        return tar.extractfile(members['package-lock.json']).read()


def _layout_file(root, name):
    # Reject symlinks in the small, fixed OCI directory hierarchy.
    current = Path(root)
    for part in name.split('/'):
        current = current / part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode):raise ValueError('unsafe OCI layout path')
    return current


def _read_json_file(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_META:
            raise ValueError('unsafe OCI metadata')
        obj = json.loads(stream.read())
    if not isinstance(obj, dict):raise ValueError('invalid OCI metadata')
    return obj


def _freeze_base(root, expected, dest):
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', expected):raise ValueError('pinned base manifest required')
    if Path(root).is_symlink() or not Path(root).is_dir():raise ValueError('unsafe OCI layout')
    index = _read_json_file(_layout_file(root, 'index.json'))
    if _read_json_file(_layout_file(root, 'oci-layout')).get('imageLayoutVersion') != '1.0.0':
        raise ValueError('unsupported OCI layout')
    descriptors = index.get('manifests')
    if not isinstance(descriptors, list):raise ValueError('invalid OCI index')
    matches = [x for x in descriptors if isinstance(x, dict) and x.get('digest') == expected]
    if len(matches) != 1 or matches[0].get('mediaType') != OCI_MANIFEST:
        raise ValueError('pinned base missing or ambiguous')
    blobs = dest / 'blobs/sha256' if dest is not None else None
    if blobs is not None:blobs.mkdir(parents=True)

    def copy_descriptor(descriptor, allowed):
        digest, size, media = descriptor.get('digest'), descriptor.get('size'), descriptor.get('mediaType')
        if (not isinstance(digest, str) or not re.fullmatch(r'sha256:[a-f0-9]{64}', digest) or
                type(size) is not int or size < 0 or size > 4 * 1024**3 or media not in allowed):
            raise ValueError('unsupported OCI descriptor')
        source = _layout_file(root, 'blobs/sha256/' + digest[7:])
        if blobs is None:
            _hash_file(source, digest[7:], 4 * 1024**3)
            target = source
        else:
            target = blobs / digest[7:]
            if not target.exists():_copy_checked(source, target, digest[7:], 4 * 1024**3)
        if target.stat().st_size != size:raise ValueError('OCI blob size differs')
        return target

    manifest_path = copy_descriptor(matches[0], {OCI_MANIFEST})
    manifest = _read_json_file(manifest_path)
    if manifest.get('schemaVersion') != 2 or manifest.get('mediaType') != OCI_MANIFEST:
        raise ValueError('unsupported OCI manifest')
    config = manifest.get('config');layers = manifest.get('layers')
    if not isinstance(config, dict) or not isinstance(layers, list) or not layers:
        raise ValueError('invalid OCI manifest')
    config_path = copy_descriptor(config, {OCI_CONFIG})
    obj = _read_json_file(config_path)
    if obj.get('os') != 'linux' or obj.get('architecture') != 'amd64':
        raise ValueError('base must target linux/amd64')
    for layer in layers:
        if not isinstance(layer, dict):raise ValueError('invalid OCI layer')
        copy_descriptor(layer, LAYERS)
    if dest is not None:
        (dest/'oci-layout').write_text('{"imageLayoutVersion":"1.0.0"}')
        (dest/'index.json').write_text(json.dumps({'schemaVersion':2,'manifests':[matches[0]]}, separators=(',',':')))
    return config['digest']


def _builder_config(config, builder, host):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,50}', builder) or not host.startswith('unix:///'):
        raise ValueError('named local builder required')
    root = Path(config)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('private Docker config required')
    cfg = _read_json_file(root / 'config.json')
    if cfg:raise ValueError('anonymous Docker config required')
    for relative in ('buildx', 'buildx/instances'):
        path = _layout_file(root, relative)
        if not path.is_dir() or path.stat().st_uid != os.getuid():raise ValueError('unsafe builder config')
    p = _layout_file(root, 'buildx/instances/' + builder)
    raw = p.read_bytes()
    if len(raw) > MAX_META:raise ValueError('builder config too large')
    obj = json.loads(raw)
    nodes = obj.get('Nodes') if isinstance(obj, dict) else None
    if (obj.get('Name') != builder or obj.get('Driver') != 'docker-container' or obj.get('Dynamic') or
            not isinstance(nodes, list) or len(nodes) != 1 or not isinstance(nodes[0], dict)):
        raise ValueError('one local docker-container builder required')
    node = nodes[0]; opts = node.get('DriverOpts'); flags = node.get('Flags') or []
    if (node.get('Name') != builder + '0' or node.get('Endpoint') != host or node.get('Files') not in (None, {}) or
            not isinstance(opts, dict) or set(opts) - {'image','network'} or opts.get('network') != 'none' or
            not isinstance(flags, list) or any(x not in ('--allow-insecure-entitlement=network.host', '--debug=false') for x in flags)):
        raise ValueError('unsafe builder configuration')
    return hashlib.sha256(raw).hexdigest(), flags


def _run(args, env, timeout=30):
    return subprocess.run(args, env=env, check=True, capture_output=True, timeout=timeout).stdout


def _inspect_builder(docker, builder, expected_id, expected_image, flags, env):
    name = 'buildx_buildkit_' + builder + '0'
    obj = json.loads(_run([docker, 'inspect', name], env))
    if not isinstance(obj, list) or len(obj) != 1:raise ValueError('builder inspection ambiguous')
    value = obj[0];config = value.get('Config') or {}; host = value.get('HostConfig') or {}
    networks = (value.get('NetworkSettings') or {}).get('Networks') or {}
    mounts = value.get('Mounts') or []
    if (value.get('Id') != expected_id or value.get('Image') != expected_image or
            (value.get('State') or {}).get('Running') is not True or host.get('NetworkMode') != 'none' or
            set(networks) != {'none'} or any((networks['none'].get(x) or '') for x in ('IPAddress','GlobalIPv6Address')) or
            config.get('Entrypoint') != ['/usr/bin/buildkitd-entrypoint'] or (config.get('Cmd') or []) != flags or
            len(mounts) != 1 or mounts[0].get('Type') != 'volume' or mounts[0].get('Destination') != '/var/lib/buildkit' or
            host.get('Binds') not in (None, [])):
        raise ValueError('builder container differs from isolated identity')
    _run([docker, 'exec', name, '/bin/sh', '-c',
          'test ! -e /etc/buildkit/buildkitd.toml && test ! -e /root/.config/buildkit/buildkitd.toml'], env)
    def cmdline(path):
        return [part for part in _run([docker, 'exec', name, 'cat', path], env).decode().split('\0') if part]
    process = cmdline('/proc/1/cmdline')
    if process == ['/sbin/docker-init', '--', '/usr/bin/buildkitd-entrypoint', *flags]:
        children = _run([docker, 'exec', name, 'cat', '/proc/1/task/1/children'], env).decode().split()
        if len(children) != 1 or not re.fullmatch(r'[1-9][0-9]*', children[0]):
            raise ValueError('unexpected BuildKit init children')
        process = cmdline('/proc/' + children[0] + '/cmdline')
    if not process or process[0] != '/usr/bin/buildkitd' or process[1:] != flags:
        raise ValueError('running BuildKit flags differ')


def _verify_source_revision(repo, revision, expected):
    """Independently derive the bounded git archive of the selected commit."""
    env = {'PATH':'/usr/bin:/bin:/usr/sbin:/sbin', 'HOME':'/nonexistent',
           'GIT_CONFIG_NOSYSTEM':'1', 'GIT_NO_REPLACE_OBJECTS':'1'}
    actual = _run(['/usr/bin/git', '-C', str(repo), 'rev-parse', revision + '^{commit}'], env).decode().strip()
    if actual != revision:raise ValueError('exact source revision required')
    process = subprocess.Popen(['/usr/bin/git', '-C', str(repo), 'archive', revision],
                               env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    digest = hashlib.sha256();total = 0;deadline = time.monotonic() + 60
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:raise ValueError('git archive timed out')
            ready, _, _ = select.select([process.stdout], [], [], min(remaining, 1))
            if not ready:continue
            chunk = os.read(process.stdout.fileno(), 1024 * 1024)
            if not chunk:break
            total += len(chunk)
            if total > MAX_SOURCE:raise ValueError('git archive exceeds size limit')
            digest.update(chunk)
        if process.wait(timeout=max(deadline - time.monotonic(), 0.001)) != 0:
            raise ValueError('git archive failed')
        if digest.hexdigest() != expected:
            raise ValueError('source archive differs from selected revision')
    finally:
        if process.poll() is None:
            process.kill();process.wait()
        process.stdout.close()



def isolated_npm_build(*, repo, source_tar, source_sha256, revision, materials_tar, materials_sha256,
                       base_layout, base_manifest, docker, docker_config, docker_host,
                       builder, builder_container_id, builder_image_id, tag, output_tar):
    """Produce an npm-ci image; the caller signs the returned binding separately."""
    for digest in (source_sha256, materials_sha256):
        if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):raise ValueError('pinned input digest required')
    if (not re.fullmatch(r'[a-f0-9]{40}', revision) or not re.fullmatch(r'[a-f0-9]{64}', builder_container_id) or
            not re.fullmatch(r'sha256:[a-f0-9]{64}', builder_image_id) or
            not re.fullmatch(r'factory-candidate-[a-z0-9_-]+:[a-f0-9]{40}', tag) or not tag.endswith(':'+revision)):
        raise ValueError('invalid build identity')
    if not Path(docker).is_absolute():raise ValueError('absolute Docker executable required')
    output = Path(output_tar)
    if any(c in str(output.absolute()) for c in (',', '\n', '\r')):
        raise ValueError('unsafe Docker output path')
    parent = output.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or parent.st_mode & 0o077:
        raise ValueError('private output directory required')
    env = {'PATH':'/usr/bin:/bin:/usr/sbin:/sbin', 'DOCKER_CONFIG':str(Path(docker_config).absolute()), 'DOCKER_HOST':docker_host}
    _verify_source_revision(repo, revision, source_sha256)
    before, flags = _builder_config(docker_config, builder, docker_host)
    _inspect_builder(docker, builder, builder_container_id, builder_image_id, flags, env)
    with tempfile.TemporaryDirectory(prefix='factory-offline-npm-') as temp:
        root = Path(temp);context = root/'context';context.mkdir()
        _copy_checked(source_tar, context/'source.tar', source_sha256, MAX_SOURCE)
        _copy_checked(materials_tar, context/'materials.tar', materials_sha256, 2 * 1024**3 + 64 * 1024**2)
        frozen = verify_frozen_npm(context/'materials.tar', materials_sha256)
        with tarfile.open(context/'materials.tar', mode='r:') as tar:
            lock = tar.extractfile('package-lock.json').read()
        if _source_lock(context/'source.tar', revision) != lock:
            raise ValueError('source and frozen npm lock differ')
        base = root/'base';base.mkdir()
        base_config = _freeze_base(base_layout, base_manifest, base)
        (context/'Dockerfile').write_bytes(RECIPE);(context/'populate.cjs').write_bytes(POPULATE)
        # The output is reserved once; a failed or uncertain build remains evidence.
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600);os.close(fd)
        args = [docker,'buildx','build','--builder',builder,'--platform','linux/amd64','--network','none','--no-cache',
                '--build-context','base=oci-layout://' + str(base) + '@' + base_manifest,
                '--file',str(context/'Dockerfile'),'--label','factory.git-revision='+revision,
                '--label','factory.source-archive-sha256='+source_sha256,'--tag',tag,
                '--provenance=false','--output','type=docker,dest='+str(output),str(context)]
        _run(args, env, timeout=1800)
        after, next_flags = _builder_config(docker_config, builder, docker_host)
        if after != before or next_flags != flags:raise ValueError('builder configuration changed')
        _inspect_builder(docker, builder, builder_container_id, builder_image_id, flags, env)
        # The shared verifier checks archive/config labels and streams every
        # raw or gzip layer against the uncompressed rootfs diff IDs.
        from isolated_cargo_build import verify_docker_image_archive
        image = verify_docker_image_archive(output, tag, revision, source_sha256)
        return {'status':'isolated-npm-ci-image', 'coverage':'incomplete', 'revision':revision,
                'source_archive_sha256':source_sha256, 'materials_archive_sha256':materials_sha256,
                'lock_sha256':frozen['lock_sha256'], 'materials_manifest_sha256':frozen['manifest_sha256'],
                'base_manifest':base_manifest, 'base_config':base_config,
                'builder_container_id':builder_container_id, 'builder_image_id':builder_image_id,
                'builder_config_sha256':before, 'recipe_sha256':hashlib.sha256(RECIPE).hexdigest(),
                'cache_recipe_sha256':hashlib.sha256(POPULATE).hexdigest(), 'image_tar_sha256':image['archive_sha256'],
                'config_digest':image['config_digest']}
