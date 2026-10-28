"""Seal a quiesced factory snapshot; verify signature before decrypting/extracting.

Keys live outside snapshot/repository. Callers must stop dedicated factory writers
and include Restate data, attempt stores/anchors, budgets and workspaces together.
Never apply this tool to running databases or shared Hermes services.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import stat
import tarfile
import tempfile


def private_file(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError('owner-only regular key/passphrase file required')


def openssl(*args):
    subprocess.run(['openssl', *map(str, args)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def manifest(root):
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('snapshot symlink unsupported')
        if path.is_file():
            result[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif not path.is_dir():
            raise ValueError('snapshot special file unsupported')
    return result


def seal(source, output, password, signing_key):
    if output.exists() or Path(str(output) + '.sig').exists():
        raise ValueError('refusing backup overwrite')
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / 'snapshot.tar'
        before = manifest(source)
        with tarfile.open(archive, 'w') as tar:
            tar.add(source, arcname='snapshot', recursive=True)
            data = json.dumps(before, sort_keys=True).encode()
            import io
            info = tarfile.TarInfo('manifest.json'); info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        if manifest(source) != before:
            raise ValueError('snapshot changed during backup')
        openssl('enc', '-aes-256-cbc', '-pbkdf2', '-iter', '200000', '-salt', '-in', archive,
                '-out', output, '-pass', 'file:' + str(password))
        output.chmod(0o600)
        openssl('dgst', '-sha256', '-sign', signing_key, '-out', str(output) + '.sig', output)


def restore(archive, signature, password, public_key, destination):
    if destination.exists():
        raise ValueError('restore destination already exists')
    # Authenticate the encrypted bytes before exposing a decryption oracle or
    # interpreting archive paths. A separately retained key is the trust root.
    openssl('dgst', '-sha256', '-verify', public_key, '-signature', signature, archive)
    with tempfile.TemporaryDirectory(dir=destination.parent) as tmp:
        root = Path(tmp)
        plain = root / 'plain.tar'
        openssl('enc', '-d', '-aes-256-cbc', '-pbkdf2', '-iter', '200000', '-in', archive,
                '-out', plain, '-pass', 'file:' + str(password))
        extract = root / 'extracted'; extract.mkdir(mode=0o700)
        with tarfile.open(plain) as tar:
            for member in tar.getmembers():
                name = Path(member.name)
                if name.is_absolute() or '..' in name.parts or not (member.isfile() or member.isdir()):
                    raise ValueError('unsafe snapshot archive')
                if member.name != 'manifest.json' and (not name.parts or name.parts[0] != 'snapshot'):
                    raise ValueError('unexpected snapshot member')
            tar.extractall(extract, filter='data')
        expected = json.loads((extract / 'manifest.json').read_text())
        if manifest(extract / 'snapshot') != expected:
            raise ValueError('mixed or altered recovery generation')
        os.rename(extract / 'snapshot', destination)
        destination.chmod(0o700)
        return {'files': len(expected), 'authenticated': True, 'generation_consistent': True}


if __name__ == '__main__':
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['seal', 'restore'])
    p.add_argument('--source', type=Path)
    p.add_argument('--archive', required=True, type=Path)
    p.add_argument('--password-file', required=True, type=Path)
    p.add_argument('--key', required=True, type=Path)
    p.add_argument('--destination', type=Path)
    p.add_argument('--quiesced', action='store_true')
    a = p.parse_args()
    private_file(a.password_file)
    private_file(a.key)
    if a.mode == 'seal':
        if not a.quiesced or a.source is None:
            p.error('explicit quiesced source required')
        seal(a.source, a.archive, a.password_file, a.key)
        print(json.dumps({'sealed': True}))
    else:
        print(json.dumps(restore(a.archive, Path(str(a.archive) + '.sig'), a.password_file, a.key, a.destination)))
