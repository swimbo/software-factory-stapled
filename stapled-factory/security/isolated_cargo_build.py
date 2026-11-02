"""Opt-in offline Cargo OCI image builder; publication remains gated.

The local Docker daemon, BuildKit image, operator account and build inputs are
trusted. Pinned identities do not establish publisher authenticity or prove
resilience to a malicious build escaping BuildKit's sandbox. The source must be
independently screened for secrets before calling this runner.
"""
import hashlib
import gzip
import json
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import tarfile
import time
import tomllib

from controlled_cargo import _BoundedTarInput, verify_frozen_cargo

MAX_SOURCE_BYTES = 2 * 1024 * 1024 * 1024
MAX_SOURCE_FILES = 100000
MAX_LOCK_BYTES = 16 * 1024 * 1024
MAX_INPUT_BYTES = 16 * 1024 * 1024 * 1024


def _digest(value, label):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value):
        raise ValueError('invalid ' + label + ' SHA-256')
    return value


def _pinned_copy(source, dest, digest):
    _digest(digest, 'input')
    try:
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise ValueError('unsafe input archive') from exc
    h = hashlib.sha256(); total = 0
    with os.fdopen(fd, 'rb') as inp, dest.open('xb') as out:
        info = os.fstat(inp.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_INPUT_BYTES:
            raise ValueError('unsafe input archive')
        while chunk := inp.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_INPUT_BYTES:
                raise ValueError('input archive too large')
            h.update(chunk); out.write(chunk)
        if total != info.st_size or h.hexdigest() != digest:
            raise ValueError('input archive digest differs')
    dest.chmod(0o600)


def _component(value):
    if not isinstance(value, str) or (value and (len(value) > 512 or
            any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', part) or part in ('.', '..', 'target')
                for part in value.split('/')))):
        raise ValueError('invalid Cargo source component')
    return value


def _source_lock_digest(source, binary_name=None, source_component=''):
    source_component = _component(source_component)
    prefix = source_component + '/' if source_component else ''
    if source.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError('source archive too large')
    names = set(); regular = set(); lock = None; manifest = None; total = 0
    try:
        stream = source.open('rb')
        with stream, tarfile.open(fileobj=_BoundedTarInput(stream), mode='r:') as tar:
            for count, member in enumerate(tar, 1):
                name = member.name; parts = name.rstrip('/').split('/')
                canonical = name.rstrip('/')
                if (count > MAX_SOURCE_FILES or canonical in names or name.startswith('/') or '\\' in name or
                        any(part in ('', '.', '..', '.git', '.cargo') for part in parts) or
                        not (member.isdir() or member.isfile()) or member.mode & 0o7000 or
                        member.size < 0 or member.size > MAX_SOURCE_BYTES):
                    raise ValueError('unsafe source archive member')
                # This rejects common credential files, not all possible secrets.
                if any(re.fullmatch(r'(?:\.env(?:\..*)?|\.npmrc|id_rsa|id_ed25519|[^/]+\.(?:key|pem|p12|pfx))', part, re.I) for part in parts):
                    raise ValueError('source contains a restricted credential path')
                names.add(canonical); total += member.size
                if member.isfile():regular.add(canonical)
                if total > MAX_SOURCE_BYTES:
                    raise ValueError('source archive too large')
                if name == prefix + 'Cargo.toml' and member.isfile():
                    if member.size > 1024 * 1024:raise ValueError('Cargo manifest too large')
                    manifest = tar.extractfile(member).read()
                if name == prefix + 'Cargo.lock':
                    if not member.isfile() or member.size > MAX_LOCK_BYTES:
                        raise ValueError('invalid source Cargo.lock')
                    lock = hashlib.file_digest(tar.extractfile(member), 'sha256').hexdigest()
    except tarfile.TarError as exc:
        raise ValueError('invalid source archive') from exc
    if lock is None or manifest is None:
        raise ValueError('root Cargo.toml and Cargo.lock required')
    if source_component:
        parts = source_component.split('/')
        if any('/'.join(parts[:index] + ['Cargo.toml']) in regular for index in range(len(parts))):
            raise ValueError('parent Cargo workspace or package is unsupported')
    if binary_name is not None:
        if not isinstance(binary_name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', binary_name):
            raise ValueError('invalid Cargo binary name')
        try:document = tomllib.loads(manifest.decode('utf-8'))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise ValueError('invalid Cargo manifest') from exc
        package = document.get('package')
        if not isinstance(package, dict) or 'workspace' in document:
            raise ValueError('only a single root Cargo package is supported')
        binaries = document.get('bin', [])
        if not isinstance(binaries, list) or any(not isinstance(item, dict) for item in binaries):
            raise ValueError('invalid Cargo binary declarations')
        matches = [item for item in binaries if item.get('name') == binary_name]
        if len(matches) > 1:
            raise ValueError('ambiguous Cargo binary')
        paths = []
        if matches and 'path' in matches[0]:
            path = matches[0]['path']
            if (not isinstance(path, str) or not re.fullmatch(r'[A-Za-z0-9._/-]+', path) or
                    path.startswith('/') or any(p in ('', '.', '..') for p in path.split('/'))):
                raise ValueError('unsafe Cargo binary path')
            paths.append(path)
        elif matches or package.get('autobins', True) is True:
            if package.get('name') == binary_name:paths.append('src/main.rs')
            paths.extend(['src/bin/' + binary_name + '.rs', 'src/bin/' + binary_name + '/main.rs'])
        if not any(prefix + path in regular for path in paths):
            raise ValueError('selected Cargo binary source unavailable')
    return lock


def cargo_source_preflight(source_archive, binary_name, source_component=''):
    """Validate a narrow root-package binary and report its lock digest."""
    result = {'lock_sha256': _source_lock_digest(Path(source_archive), binary_name, source_component),
              'cargo_binary': binary_name}
    if source_component:result['source_component'] = source_component
    return result


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate Docker config key')
        result[key] = value
    return result


def _docker_env(docker_host, docker_config):
    if not isinstance(docker_host, str) or not docker_host.startswith('unix:///') or '\n' in docker_host or '\x00' in docker_host:
        raise ValueError('local Docker socket required')
    config = Path(docker_config)
    st = config.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise ValueError('private Docker config required')
    file = config / 'config.json'
    try:
        fd = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
                raise ValueError('unsafe Docker config')
            value = json.load(stream, object_pairs_hook=_unique)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError('invalid Docker config') from exc
    if not isinstance(value, dict) or set(value) - {'auths'} or value.get('auths', {}) != {}:
        raise ValueError('anonymous Docker config required')
    return {'DOCKER_HOST': docker_host, 'DOCKER_CONFIG': str(config.absolute()),
            'PATH': '/usr/bin:/bin:/usr/sbin:/sbin'}


def _run(args, env, timeout=30):
    return subprocess.run(args, env=env, check=True, capture_output=True, text=True, timeout=timeout).stdout


def _inspect_builder(docker, env, name, expected_container_id, expected_image_id):
    if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,70}', name):
        raise ValueError('invalid builder name')
    if not isinstance(expected_container_id, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_container_id):
        raise ValueError('pinned builder container required')
    if not isinstance(expected_image_id, str) or not re.fullmatch(r'sha256:[a-f0-9]{64}', expected_image_id):
        raise ValueError('pinned builder image required')
    text = _run([docker, 'buildx', 'inspect', name], env)
    def fields(key):
        return re.findall(r'^[ \t]*' + re.escape(key) + r':[ \t]*(.*?)[ \t]*$', text, re.M)
    if (fields('Name') != [name, name + '0'] or fields('Driver') != ['docker-container'] or
            fields('Endpoint') != [env['DOCKER_HOST']] or fields('Status') != ['running']):
        raise ValueError('unexpected Buildx builder topology')
    container_name = 'buildx_buildkit_' + name + '0'
    values = json.loads(_run([docker, 'inspect', container_name], env))
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict):
        raise ValueError('ambiguous builder container')
    value = values[0]; host = value.get('HostConfig') or {}; config = value.get('Config') or {}
    args = value.get('Args')
    if (value.get('Id') != expected_container_id or value.get('Image') != expected_image_id or
            (value.get('State') or {}).get('Running') is not True or host.get('NetworkMode') != 'none' or
            host.get('PidMode') == 'host' or host.get('IpcMode') == 'host' or
            not isinstance(args, list) or any(not isinstance(arg, str) for arg in args)):
        raise ValueError('builder identity or isolation differs')
    for arg in args:
        if (arg.startswith('--allow-insecure-entitlement') and arg != '--allow-insecure-entitlement=network.host' or
                arg.startswith(('--oci-worker-no-process-sandbox', '--oci-worker-net', '--containerd-worker-net', '--config'))):
            raise ValueError('unsupported builder daemon entitlement')
    mounts = value.get('Mounts')
    if (not isinstance(mounts, list) or len(mounts) != 1 or mounts[0].get('Type') != 'volume' or
            mounts[0].get('Destination') != '/var/lib/buildkit' or mounts[0].get('RW') is not True or
            mounts[0].get('Name') != container_name + '_state'):
        raise ValueError('unexpected builder mount')
    variables = config.get('Env') or []
    if (not isinstance(variables, list) or any(not isinstance(v, str) or '=' not in v or
            v.split('=', 1)[0] not in ('PATH', 'BUILDKIT_SETUP_CGROUPV2_ROOT') for v in variables)):
        raise ValueError('unexpected builder environment')
    return {'container_id': expected_container_id, 'image_id': expected_image_id,
            'network_mode': 'none', 'daemon_network_host_allowed': '--allow-insecure-entitlement=network.host' in args}


def _verify_git_source(repo, revision, source, timeout=120):
    """Compare the retained tar to the exact selected Git archive stream."""
    if not isinstance(revision, str) or not re.fullmatch(r'[a-f0-9]{40}', revision):
        raise ValueError('exact Git revision required')
    env = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'GIT_CONFIG_GLOBAL': '/dev/null',
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_ATTR_NOSYSTEM': '1',
           'GIT_NO_REPLACE_OBJECTS': '1', 'LC_ALL': 'C'}
    actual = _run(['/usr/bin/git', '-C', str(repo), 'rev-parse', '--verify', revision + '^{commit}'], env)
    if actual.strip() != revision:
        raise ValueError('source revision differs')
    process = subprocess.Popen(['/usr/bin/git', '-C', str(repo), 'archive', revision],
                               env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + timeout
    try:
        with source.open('rb') as expected, selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError('Git archive timed out')
                chunk = os.read(process.stdout.fileno(), 1024 * 1024)
                if not chunk:
                    break
                if expected.read(len(chunk)) != chunk:
                    raise ValueError('source archive differs from exact Git revision')
            if expected.read(1):
                raise ValueError('source archive differs from exact Git revision')
        if process.wait(timeout=max(1, deadline - time.monotonic())) != 0:
            raise ValueError('Git archive failed')
    finally:
        if process.poll() is None:
            process.kill(); process.wait()
        process.stdout.close()


def verify_docker_image_archive(archive, tag, revision, source_sha256, *, expected_cmd=None):
    """Verify Docker-save labels, config and gzip or raw layer diff IDs."""
    try:
        fd = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_INPUT_BYTES:
                raise ValueError('image archive too large')
            archive_sha = hashlib.file_digest(stream, 'sha256').hexdigest(); stream.seek(0)
            with tarfile.open(fileobj=_BoundedTarInput(stream), mode='r:') as tar:
                members = {}; names = set()
                for count, member in enumerate(tar, 1):
                    if count > 100000 or member.name in names:
                        raise ValueError('ambiguous image archive')
                    names.add(member.name)
                    if member.isdir():continue
                    if not member.isfile() or member.size < 0 or member.size > MAX_INPUT_BYTES:
                        raise ValueError('unsafe image member')
                    members[member.name] = member

                def read(name, limit):
                    member = members.get(name)
                    if member is None or member.size > limit:
                        raise ValueError('missing image metadata')
                    return tar.extractfile(member).read()

                images = json.loads(read('manifest.json', 1024 * 1024))
                if (not isinstance(images, list) or len(images) != 1 or not isinstance(images[0], dict) or
                        images[0].get('RepoTags') != [tag] or not isinstance(images[0].get('Config'), str) or
                        not isinstance(images[0].get('Layers'), list)):
                    raise ValueError('image manifest or tag differs')
                image = images[0]; raw = read(image['Config'], 8 * 1024 * 1024); config = json.loads(raw)
                labels = (config.get('config') or {}).get('Labels') or {}
                if (config.get('os') != 'linux' or config.get('architecture') != 'amd64' or
                        labels.get('factory.git-revision') != revision or
                        labels.get('factory.source-archive-sha256') != source_sha256):
                    raise ValueError('image platform or source labels differ')
                if expected_cmd is not None and ((config.get('config') or {}).get('Cmd') != expected_cmd or
                        (config.get('config') or {}).get('Entrypoint') not in (None, [])):
                    raise ValueError('image runtime command differs')
                rootfs = config.get('rootfs') or {}; diffs = rootfs.get('diff_ids')
                if (rootfs.get('type') != 'layers' or not isinstance(diffs, list) or
                        len(diffs) != len(image['Layers']) or
                        any(not isinstance(name, str) for name in image['Layers']) or
                        len(set(image['Layers'])) != len(image['Layers'])):
                    raise ValueError('image layer inventory differs')
                for name, digest in zip(image['Layers'], diffs):
                    if (not isinstance(name, str) or name not in members or not isinstance(digest, str) or
                            not re.fullmatch(r'sha256:[a-f0-9]{64}', digest)):
                        raise ValueError('invalid image layer')
                    layer = tar.extractfile(members[name])
                    raw_digest = hashlib.file_digest(layer, 'sha256').hexdigest()
                    if re.fullmatch(r'blobs/sha256/[a-f0-9]{64}', name) and raw_digest != name.rsplit('/', 1)[-1]:
                        raise ValueError('image layer blob digest differs')
                    layer.seek(0); compressed = layer.read(2) == b'\x1f\x8b'; layer.seek(0)
                    content = gzip.GzipFile(fileobj=layer) if compressed else layer
                    uncompressed = hashlib.sha256(); expanded = 0
                    while chunk := content.read(1024 * 1024):
                        expanded += len(chunk)
                        if expanded > 4 * 1024 * 1024 * 1024:
                            raise ValueError('image layer expands beyond limit')
                        uncompressed.update(chunk)
                    if 'sha256:' + uncompressed.hexdigest() != digest:
                        raise ValueError('image layer digest differs')
                config_digest = 'sha256:' + hashlib.sha256(raw).hexdigest()
            stream.seek(0)
            if hashlib.file_digest(stream, 'sha256').hexdigest() != archive_sha:
                raise ValueError('image archive changed during verification')
            return {'archive_sha256': archive_sha, 'config_digest': config_digest}
    except (tarfile.TarError, json.JSONDecodeError, EOFError, OSError) as exc:
        raise ValueError('invalid Docker image archive') from exc


def _fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _recipe(source_sha, cargo_sha, binary_name, source_component=''):
    source_component = _component(source_component)
    if not isinstance(binary_name,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}',binary_name):
        raise ValueError('invalid Cargo binary name')
    workdir = '/factory/source' + ('/' + source_component if source_component else '')
    return f'''FROM base
COPY source.tar /factory-input/source.tar
COPY cargo-inputs.tar /factory-input/cargo-inputs.tar
RUN --network=none printf '%s  %s\\n' '{source_sha}' /factory-input/source.tar '{cargo_sha}' /factory-input/cargo-inputs.tar | sha256sum -c - && mkdir -p /factory/source /factory/cargo /factory/cargo-home && tar -xf /factory-input/source.tar -C /factory/source && tar -xf /factory-input/cargo-inputs.tar -C /factory/cargo && printf '%s\\n' '[source.crates-io]' 'replace-with = "vendored-sources"' '[source.vendored-sources]' 'directory = "/factory/cargo/vendor"' '[net]' 'offline = true' > /factory/cargo-home/config.toml
ENV CARGO_HOME=/factory/cargo-home CARGO_NET_OFFLINE=true
WORKDIR {workdir}
RUN --network=none cargo build --release --locked --offline --bin {binary_name}
ENTRYPOINT []
CMD ["{workdir}/target/release/{binary_name}"]
'''.encode()


def build_cargo_candidate(*, source_repo, revision, unit, source_archive, source_sha256,
                          cargo_archive, cargo_sha256, base_layout, base_manifest,
                          builder_name, builder_container_id, builder_image_id,
                          docker_host, docker_config, output_dir, binary_name,
                          docker_bin_sha256, buildx_bin_sha256,
                          docker_bin='/usr/local/bin/docker', timeout=900, source_component=''):
    """Build once from private copies and return incomplete evidence for signing.

    No factory credential, Docker socket or caller Dockerfile enters the build
    context. Candidate source still needs independent secret scanning. The
    caller must bind its source to the selected revision and sign the result.
    Failures retain the output directory and never trigger an automatic retry.
    """
    # Deferred to avoid a cycle when the npm runner imports the image verifier.
    from isolated_npm_build import (_builder_config, _freeze_base, verify_docker_tools,
                                    _inspect_builder as _inspect_actual_builder)
    for value, label in ((source_sha256, 'source'), (cargo_sha256, 'Cargo'),
                         (docker_bin_sha256, 'Docker executable'), (buildx_bin_sha256, 'Buildx executable')):
        _digest(value, label)
    if not isinstance(base_manifest, str) or not re.fullmatch(r'sha256:[a-f0-9]{64}', base_manifest):
        raise ValueError('pinned base manifest required')
    if not isinstance(docker_bin, str) or not os.path.isabs(docker_bin) or type(timeout) is not int or not 1 <= timeout <= 3600:
        raise ValueError('invalid Docker executable or timeout')
    if not isinstance(unit, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', unit):
        raise ValueError('invalid candidate unit')
    if not isinstance(binary_name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', binary_name):
        raise ValueError('invalid Cargo binary name')
    if not isinstance(revision, str) or not re.fullmatch(r'[a-f0-9]{40}', revision):
        raise ValueError('exact Git revision required')
    source_component = _component(source_component)
    tag = 'factory-candidate-' + unit.lower() + ':' + revision
    original_docker = docker_bin
    forbidden = [source_repo, output_dir, Path(__file__).resolve().parents[2]]
    resolved_docker, plugin = verify_docker_tools(original_docker, docker_config,
        docker_bin_sha256, buildx_bin_sha256, forbidden)
    env = _docker_env(docker_host, docker_config)
    builder_config, flags = _builder_config(docker_config, builder_name, docker_host)
    _inspect_actual_builder(docker_bin, builder_name, builder_container_id, builder_image_id, flags, env)
    builder = _inspect_builder(docker_bin, env, builder_name, builder_container_id, builder_image_id)
    output = Path(output_dir).absolute()
    if not re.fullmatch(r'[A-Za-z0-9/_.-]+', str(output)):
        raise ValueError('unsupported private output path')
    output.mkdir(mode=0o700)
    _fsync_directory(output.parent)
    context = output / 'context'; context.mkdir(mode=0o700)
    source = context / 'source.tar'; cargo = context / 'cargo-inputs.tar'
    _pinned_copy(source_archive, source, source_sha256)
    _pinned_copy(cargo_archive, cargo, cargo_sha256)
    _verify_git_source(source_repo, revision, source)
    cargo_report = verify_frozen_cargo(cargo, cargo_sha256)
    if cargo_source_preflight(source, binary_name, source_component)['lock_sha256'] != cargo_report['lock_sha256']:
        raise ValueError('source and frozen Cargo lock differ')
    layout = output / 'base-layout'; layout.mkdir(mode=0o700)
    base_config = _freeze_base(base_layout, base_manifest, layout)
    recipe = _recipe(source_sha256, cargo_sha256, binary_name, source_component)
    (context / 'Dockerfile').write_bytes(recipe)
    recipe_sha256 = hashlib.sha256(recipe).hexdigest()
    image = output / 'image.tar'; metadata = output / 'build-metadata.json'
    intent = {'revision': revision, 'tag': tag, 'source_sha256': source_sha256, 'cargo_sha256': cargo_sha256,
              'base_manifest_digest': base_manifest, 'base_config_digest': base_config,
              'builder_config_sha256': builder_config,
              'recipe_sha256': recipe_sha256, 'builder': builder,
              'cargo_binary': binary_name, 'docker_bin_sha256': docker_bin_sha256,
              'buildx_bin_sha256': buildx_bin_sha256}
    if source_component:intent['source_component'] = source_component
    with (output / 'build-intent.json').open('x') as stream:
        json.dump(intent, stream, sort_keys=True, separators=(',', ':')); stream.flush(); os.fsync(stream.fileno())
    _fsync_directory(output)
    args = [docker_bin, 'buildx', 'build', '--builder', builder_name, '--platform', 'linux/amd64',
            '--network', 'none', '--no-cache', '--build-context',
            'base=oci-layout://' + str(layout.absolute()) + '@' + base_manifest,
            '--label', 'factory.git-revision=' + revision,
            '--label', 'factory.source-archive-sha256=' + source_sha256,
            '--provenance=false', '--metadata-file', str(metadata),
            '--output', 'type=docker,name=' + tag + ',dest=' + str(image), str(context)]
    # Never buffer potentially unbounded candidate build output in memory.
    subprocess.run(args, env=env, check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=timeout)
    after_config, after_flags = _builder_config(docker_config, builder_name, docker_host)
    if (after_config, after_flags) != (builder_config, flags):
        raise ValueError('builder configuration changed during build')
    _inspect_actual_builder(docker_bin, builder_name, builder_container_id, builder_image_id, flags, env)
    if _inspect_builder(docker_bin, env, builder_name, builder_container_id, builder_image_id) != builder:
        raise ValueError('builder changed during build')
    if verify_docker_tools(original_docker, docker_config, docker_bin_sha256,
                           buildx_bin_sha256, forbidden) != (resolved_docker, plugin):
        raise ValueError('Docker executables changed during build')
    result = verify_docker_image_archive(image, tag, revision, source_sha256,
        expected_cmd=['/factory/source/' + (source_component + '/' if source_component else '') + 'target/release/' + binary_name])
    # Recheck exact copied inputs after the build; concurrent same-owner writers
    # remain outside this function's trust boundary.
    for path, expected in ((source, source_sha256), (cargo, cargo_sha256)):
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                raise ValueError('build input changed')
    if hashlib.sha256((context / 'Dockerfile').read_bytes()).hexdigest() != recipe_sha256:
        raise ValueError('factory recipe changed')
    return {'status': 'isolated-cargo-image-built', 'coverage': 'incomplete', **intent,
            'cargo_lock_sha256': cargo_report['lock_sha256'], 'image_archive_sha256': result['archive_sha256'],
            'image_config_digest': result['config_digest'],
            'image_archive': str(image)}
