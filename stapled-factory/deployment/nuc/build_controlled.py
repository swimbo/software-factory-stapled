"""One-shot, operator-signed wrapper for opt-in controlled build profiles.

The signature binds the selected source, exact image and runner-reported inputs.
This does not establish publisher authenticity or complete provenance, and it
never changes the application publication gate. Failures are retained.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import select
import stat
import subprocess
import sys
import time

SECURITY = Path(__file__).resolve().parents[2] / 'security'
sys.path.insert(0, str(SECURITY))
from isolated_npm_build import POPULATE, RECIPE, isolated_npm_build
from isolated_cargo_build import build_cargo_candidate, verify_docker_image_archive

MAX_SOURCE = 2 * 1024 * 1024 * 1024
MAX_DIFF = 128 * 1024 * 1024
GIT_ENV = {'PATH':'/usr/bin:/bin:/usr/sbin:/sbin', 'HOME':'/nonexistent',
           'GIT_CONFIG_NOSYSTEM':'1', 'GIT_NO_REPLACE_OBJECTS':'1', 'LC_ALL':'C'}


def _digest_file(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise ValueError('regular artifact required')
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _private_directory(path):
    info = Path(path).lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('owner-only output parent required')


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)


def _write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data);stream.flush();os.fsync(stream.fileno())


def _git(repo, *args):
    return subprocess.run(['/usr/bin/git','-C',str(repo),*args], env=GIT_ENV, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30).stdout


def _git_artifact(repo, args, target, limit):
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    proc = subprocess.Popen(['/usr/bin/git','-C',str(repo),*args], env=GIT_ENV,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    digest=hashlib.sha256();total=0;deadline=time.monotonic()+120
    try:
        with os.fdopen(fd,'wb') as stream:
            while True:
                remaining=deadline-time.monotonic()
                if remaining<=0:raise ValueError('Git artifact timed out')
                ready,_,_=select.select([proc.stdout],[],[],min(1,remaining))
                if not ready:continue
                chunk=os.read(proc.stdout.fileno(),1024*1024)
                if not chunk:break
                total+=len(chunk)
                if total>limit:raise ValueError('Git artifact exceeds limit')
                digest.update(chunk);stream.write(chunk)
            if proc.wait(timeout=max(deadline-time.monotonic(),0.001)) != 0:
                raise ValueError('Git artifact failed')
            stream.flush();os.fsync(stream.fileno())
    finally:
        if proc.poll() is None:proc.kill();proc.wait()
        proc.stdout.close()
    return digest.hexdigest()


def build_controlled(*, profile, repo, revision, baseline, unit, materials_archive,
                     materials_sha256, base_layout, base_manifest, builder_name,
                     builder_container_id, builder_image_id, docker_host, docker_config,
                     signing_key, output_dir, docker_bin='/usr/local/bin/docker'):
    """Build and sign one candidate, retaining any partial original operation."""
    if profile not in ('npm', 'cargo'):raise ValueError('unsupported controlled build profile')
    if (not isinstance(revision,str) or not re.fullmatch(r'[a-f0-9]{40}',revision) or
            not isinstance(baseline,str) or not re.fullmatch(r'[a-f0-9]{40}',baseline) or
            not isinstance(unit,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',unit) or
            not isinstance(materials_sha256,str) or not re.fullmatch(r'[a-f0-9]{64}',materials_sha256) or
            not isinstance(base_manifest,str) or not re.fullmatch(r'sha256:[a-f0-9]{64}',base_manifest) or
            not isinstance(builder_container_id,str) or not re.fullmatch(r'[a-f0-9]{64}',builder_container_id) or
            not isinstance(builder_image_id,str) or not re.fullmatch(r'sha256:[a-f0-9]{64}',builder_image_id)):
        raise ValueError('invalid controlled build identity')
    repo = Path(repo).absolute();out = Path(output_dir).absolute();key = Path(signing_key)
    _private_directory(out.parent)
    st = key.lstat()
    if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077 or
            st.st_size > 65536):
        raise ValueError('owner-only operator signing key required')
    key_path = key.resolve()
    forbidden = [repo.resolve(),out.resolve(),Path(base_layout).resolve(),Path(docker_config).resolve()]
    if (any(key_path.is_relative_to(path) for path in forbidden) or
            key_path == Path(materials_archive).resolve()):
        raise ValueError('signing key must remain outside build inputs and contexts')
    if (_git(repo,'rev-parse',revision+'^{commit}').decode().strip() != revision or
            _git(repo,'rev-parse',baseline+'^{commit}').decode().strip() != baseline):
        raise ValueError('exact source commits required')
    _git(repo,'merge-base','--is-ancestor',baseline,revision)
    # Exclusive creation is the durable one-shot fence; never replay a failed build.
    out.mkdir(mode=0o700);_sync_dir(out.parent)
    archive_sha = _git_artifact(repo, ['archive',revision], out/'source.tar', MAX_SOURCE)
    diff_sha = _git_artifact(repo, ['diff','--no-ext-diff','--no-textconv',baseline,revision], out/'diff', MAX_DIFF)
    if diff_sha == hashlib.sha256(b'').hexdigest():raise ValueError('candidate diff is empty')
    tag = 'factory-candidate-'+unit.lower()+':'+revision
    _write(out/'build-intent.json',json.dumps({'profile':profile,'unit':unit,'revision':revision,
        'baseline':baseline,'archive_sha256':archive_sha,'diff_sha256':diff_sha,
        'materials_sha256':materials_sha256,'tag':tag},sort_keys=True,separators=(',',':')).encode())
    _sync_dir(out)

    if profile == 'npm':
        result = isolated_npm_build(repo=repo, source_tar=out/'source.tar', source_sha256=archive_sha,
            revision=revision, materials_tar=materials_archive, materials_sha256=materials_sha256,
            base_layout=base_layout, base_manifest=base_manifest, docker=docker_bin,
            docker_config=docker_config, docker_host=docker_host, builder=builder_name,
            builder_container_id=builder_container_id, builder_image_id=builder_image_id,
            tag=tag, output_tar=out/'image.tar')
        recipe = RECIPE
        if (result.get('status') != 'isolated-npm-ci-image' or result.get('revision') != revision or
                result.get('source_archive_sha256') != archive_sha or result.get('materials_archive_sha256') != materials_sha256 or
                result.get('base_manifest') != base_manifest or result.get('builder_container_id') != builder_container_id or
                result.get('builder_image_id') != builder_image_id):
            raise ValueError('npm runner evidence differs from original intent')
        controlled = {'profile':'npm', 'coverage':'incomplete', 'source_sha256':archive_sha,
            'materials_sha256':materials_sha256, 'lock_sha256':result.get('lock_sha256'),
            'materials_manifest_sha256':result.get('materials_manifest_sha256'), 'base_manifest':base_manifest,
            'base_config':result.get('base_config'), 'builder_container_id':builder_container_id,
            'builder_image_id':builder_image_id, 'builder_config_sha256':result.get('builder_config_sha256'),
            'recipe_sha256':result.get('recipe_sha256'), 'cache_recipe_sha256':result.get('cache_recipe_sha256')}
        runner_image_sha = result.get('image_tar_sha256');runner_config = result.get('config_digest')
    else:
        result = build_cargo_candidate(source_repo=repo, revision=revision, unit=unit,
            source_archive=out/'source.tar', source_sha256=archive_sha,
            cargo_archive=materials_archive, cargo_sha256=materials_sha256,
            base_layout=base_layout, base_manifest=base_manifest, builder_name=builder_name,
            builder_container_id=builder_container_id, builder_image_id=builder_image_id,
            docker_host=docker_host, docker_config=docker_config, output_dir=out/'runner', docker_bin=docker_bin)
        builder = result.get('builder') or {}
        if (result.get('status') != 'isolated-cargo-image-built' or result.get('revision') != revision or
                result.get('tag') != tag or result.get('source_sha256') != archive_sha or
                result.get('cargo_sha256') != materials_sha256 or result.get('base_manifest_digest') != base_manifest or
                builder.get('container_id') != builder_container_id or builder.get('image_id') != builder_image_id or
                builder.get('network_mode') != 'none' or Path(result.get('image_archive','')) != out/'runner/image.tar'):
            raise ValueError('Cargo runner evidence differs from original intent')
        recipe = (out/'runner/context/Dockerfile').read_bytes()
        os.link(out/'runner/image.tar',out/'image.tar',follow_symlinks=False)
        os.unlink(out/'runner/image.tar')
        controlled = {'profile':'cargo', 'coverage':'incomplete', 'source_sha256':archive_sha,
            'materials_sha256':materials_sha256, 'lock_sha256':result.get('cargo_lock_sha256'),
            'base_manifest':base_manifest, 'base_config':result.get('base_config_digest'),
            'builder_container_id':builder_container_id, 'builder_image_id':builder_image_id,
            'builder_config_sha256':result.get('builder_config_sha256'), 'recipe_sha256':result.get('recipe_sha256')}
        runner_image_sha = result.get('image_archive_sha256');runner_config = result.get('image_config_digest')
    if result.get('coverage') != 'incomplete':raise ValueError('controlled coverage must remain incomplete')
    controlled.update(unit=unit, tag=tag)
    for name in ('source_sha256','materials_sha256','lock_sha256','builder_config_sha256','recipe_sha256'):
        if not isinstance(controlled[name],str) or not re.fullmatch(r'[a-f0-9]{64}',controlled[name]):
            raise ValueError('missing controlled input digest')
    for name in ('materials_manifest_sha256','cache_recipe_sha256') if profile == 'npm' else ():
        if not isinstance(controlled[name],str) or not re.fullmatch(r'[a-f0-9]{64}',controlled[name]):
            raise ValueError('missing npm input digest')
    if not isinstance(controlled['base_config'],str) or not re.fullmatch(r'sha256:[a-f0-9]{64}',controlled['base_config']):
        raise ValueError('missing base image digest')
    if hashlib.sha256(recipe).hexdigest() != controlled['recipe_sha256']:
        raise ValueError('controlled recipe differs')
    if profile == 'npm' and controlled['cache_recipe_sha256'] != hashlib.sha256(POPULATE).hexdigest():
        raise ValueError('npm cache recipe differs')
    image = verify_docker_image_archive(out/'image.tar',tag,revision,archive_sha)
    if image['archive_sha256'] != runner_image_sha or image['config_digest'] != runner_config:
        raise ValueError('runner image binding differs')
    if _digest_file(out/'source.tar') != archive_sha or _digest_file(out/'diff') != diff_sha:
        raise ValueError('source artifacts changed during build')
    _write(out/'Dockerfile',recipe)
    manifest = {'schema':'factory.build.v2','revision':revision,'baseline':baseline,
        'archive_sha256':archive_sha,'diff_sha256':diff_sha,'dockerfile_sha256':controlled['recipe_sha256'],
        'image_tar_sha256':image['archive_sha256'],'config_digest':image['config_digest'],'controlled':controlled}
    _write(out/'build.json',json.dumps(manifest,sort_keys=True,separators=(',',':')).encode())
    # Signing happens only after the runner has exited; the key is never passed to it.
    signature = subprocess.run(['openssl','pkeyutl','-sign','-inkey',str(key_path),'-rawin',
        '-in',str(out/'build.json')],check=True,capture_output=True,timeout=30).stdout
    if not signature or len(signature)>65536:raise ValueError('invalid operator signature')
    _write(out/'build.sig',signature);_sync_dir(out)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('profile','repo','revision','baseline','unit','materials-archive','materials-sha256','base-layout',
                 'base-manifest','builder-name','builder-container-id','builder-image-id','docker-host','docker-config',
                 'signing-key','output-dir'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--docker-bin',default='/usr/local/bin/docker')
    print(json.dumps(build_controlled(**vars(parser.parse_args())),sort_keys=True))


if __name__ == '__main__':main()
