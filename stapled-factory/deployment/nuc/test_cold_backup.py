import importlib.util
from pathlib import Path
import subprocess
import pytest
spec = importlib.util.spec_from_file_location('cold_backup', Path(__file__).with_name('cold_backup.py'))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def test_real_encryption_signature_restore_and_tamper(tmp_path):
    private, public, password = [tmp_path / n for n in ('private', 'public', 'password')]
    password.write_text('disposable-test-password')
    subprocess.run(['openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048', '-out', str(private)], check=True, capture_output=True)
    subprocess.run(['openssl', 'pkey', '-in', str(private), '-pubout', '-out', str(public)], check=True, capture_output=True)
    source = tmp_path / 'source'; source.mkdir(); (source / 'db').write_bytes(b'nonempty SQLite fixture')
    archive = tmp_path / 'backup.enc'
    m.seal(source, archive, password, private)
    target = tmp_path / 'restore'
    assert m.restore(archive, Path(str(archive) + '.sig'), password, public, target)['generation_consistent']
    assert (target / 'db').read_bytes() == (source / 'db').read_bytes()
    with pytest.raises(ValueError, match='already exists'):
        m.restore(archive, Path(str(archive) + '.sig'), password, public, target)
    archive.write_bytes(archive.read_bytes() + b'tamper')
    with pytest.raises(subprocess.CalledProcessError):
        m.restore(archive, Path(str(archive) + '.sig'), password, public, tmp_path / 'bad')
    assert not (tmp_path / 'bad').exists()


def test_symlink_snapshot_rejected(tmp_path):
    source = tmp_path / 'source'; source.mkdir(); (source / 'link').symlink_to('/etc/passwd')
    with pytest.raises(ValueError, match='symlink'):
        m.manifest(source)


def test_authenticated_mixed_generation_rejected(tmp_path):
    import io
    import json
    import tarfile
    private, public, password = [tmp_path / n for n in ('private', 'public', 'password')]
    password.write_text('disposable-password')
    subprocess.run(['openssl','genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:2048','-out',str(private)],check=True,capture_output=True)
    subprocess.run(['openssl','pkey','-in',str(private),'-pubout','-out',str(public)],check=True,capture_output=True)
    plain = tmp_path / 'mixed.tar'
    with tarfile.open(plain, 'w') as tar:
        for name, content in [('snapshot/anchor', b'older generation'), ('manifest.json', json.dumps({'anchor': 'a' * 64}).encode())]:
            info = tarfile.TarInfo(name); info.size = len(content); tar.addfile(info, io.BytesIO(content))
    archive = tmp_path / 'mixed.enc'; sig = Path(str(archive) + '.sig')
    m.openssl('enc','-aes-256-cbc','-pbkdf2','-iter','200000','-salt','-in',plain,'-out',archive,'-pass','file:' + str(password))
    m.openssl('dgst','-sha256','-sign',private,'-out',sig,archive)
    with pytest.raises(ValueError, match='mixed or altered'):
        m.restore(archive,sig,password,public,tmp_path / 'bad')
    assert not (tmp_path / 'bad').exists()
