import base64
import hashlib
import json
from pathlib import Path
import sys
import tarfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from controlled_npm import freeze_npm, verify_frozen_npm


def inputs(tmp_path):
    material = b'fixture package archive'
    digest = hashlib.sha512(material).digest()
    store = tmp_path / 'store';store.mkdir(mode=0o700)
    (store / digest.hex()).write_bytes(material)
    lock = json.dumps({'lockfileVersion': 3, 'packages': {'': {'name': 'app'},
        'node_modules/example': {'version': '1.0.0',
            'resolved': 'https://registry.npmjs.org/example/-/example-1.0.0.tgz',
            'integrity': 'sha512-' + base64.b64encode(digest).decode()}}}).encode()
    outdir = tmp_path / 'out';outdir.mkdir(mode=0o700)
    return lock, store, outdir / 'materials.tar'


def test_freeze_verify_and_reject_changed_archive(tmp_path):
    lock, store, output = inputs(tmp_path)
    frozen = freeze_npm(lock, store, output)
    verified = verify_frozen_npm(output, frozen['archive_sha256'])
    assert frozen['coverage'] == verified['coverage'] == 'incomplete'
    assert frozen['lock_sha256'] == verified['lock_sha256']
    assert frozen['manifest_sha256'] == verified['manifest_sha256']
    with pytest.raises(FileExistsError):
        freeze_npm(lock, store, output)
    with tarfile.open(output) as tar:
        material = next(item for item in tar if item.name.startswith('materials/'))
    with output.open('r+b') as stream:
        stream.seek(material.offset_data);stream.write(b'X')
    changed = hashlib.sha256(output.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='material digest'):
        verify_frozen_npm(output, changed)
    with pytest.raises(ValueError, match='archive digest'):
        verify_frozen_npm(output, frozen['archive_sha256'])


def test_reject_alias_and_untrusted_material(tmp_path):
    lock, store, output = inputs(tmp_path)
    bad = json.loads(lock)
    bad['packages']['node_modules/example']['resolved'] = 'https://registry.npmjs.org/other/-/other-1.0.0.tgz'
    with pytest.raises(ValueError, match='alias'):
        freeze_npm(json.dumps(bad).encode(), store, output)
    archive = next(store.iterdir());archive.write_bytes(b'changed')
    with pytest.raises(ValueError, match='digest mismatch'):
        freeze_npm(lock, store, output)


def test_reject_oversized_pax_header_before_read(tmp_path):
    output = tmp_path / 'oversized.tar'
    header = tarfile.TarInfo('pax');header.type = tarfile.XHDTYPE
    header.size = 32 * 1024 * 1024
    output.write_bytes(header.tobuf(format=tarfile.USTAR_FORMAT) + bytes(1024))
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='read exceeds limit'):
        verify_frozen_npm(output, digest)


def test_frozen_signature_evidence_requires_independently_pinned_key(tmp_path):
    import subprocess
    lock, store, output = inputs(tmp_path)
    private = tmp_path / 'key.pem'; public = tmp_path / 'key.der'; sig = tmp_path / 'sig.der'
    subprocess.run(['openssl', 'ecparam', '-name', 'prime256v1', '-genkey', '-noout', '-out', str(private)], check=True, capture_output=True)
    subprocess.run(['openssl', 'pkey', '-in', str(private), '-pubout', '-outform', 'DER', '-out', str(public)], check=True, capture_output=True)
    key = public.read_bytes()
    def field(value): return len(value).to_bytes(4, 'big') + value
    ssh = field(b'ecdsa-sha2-nistp256') + field(b'nistp256') + field(key[-65:])
    keyid = 'SHA256:' + base64.b64encode(hashlib.sha256(ssh).digest()).decode().rstrip('=')
    entry = json.loads(lock)['packages']['node_modules/example']
    message = f"example@{entry['version']}:{entry['integrity']}".encode()
    subprocess.run(['openssl', 'dgst', '-sha256', '-sign', str(private), '-out', str(sig)], input=message, check=True, capture_output=True)
    evidence = {'node_modules/example': {'keyid': keyid, 'sig': base64.b64encode(sig.read_bytes()).decode()}}
    frozen = freeze_npm(lock, store, output, registry_evidence=evidence, registry_keys={keyid: key})
    with pytest.raises(ValueError, match='pinned'):
        verify_frozen_npm(output, frozen['archive_sha256'])
    checked = verify_frozen_npm(output, frozen['archive_sha256'], registry_keys={keyid: key})
    assert checked['registry_evidence_sha256'] == frozen['registry_evidence_sha256']
    assert checked['coverage'] == 'incomplete'
    with pytest.raises(ValueError, match='pinned'):
        verify_frozen_npm(output, frozen['archive_sha256'], registry_keys={})
