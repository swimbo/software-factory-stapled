import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest

PATH = Path(__file__).with_name('admission_marker.py')


def load():
    spec = importlib.util.spec_from_file_location('admission_marker', PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_guard_blocks_every_existing_marker_and_unavailable_directory(tmp_path):
    control = tmp_path / 'admission'
    control.mkdir(mode=0o700)
    def guard():
        return subprocess.run([sys.executable, str(PATH), 'prestart', str(control)],
                              capture_output=True).returncode
    assert guard() == 0
    (control / 'transition').write_text('incomplete')
    assert guard() != 0
    (control / 'transition').unlink()
    (control / 'transition').symlink_to('missing')
    assert guard() != 0
    (control / 'transition').unlink()
    control.chmod(0o755)
    assert guard() != 0
    control.chmod(0o700)
    control.rmdir()
    assert guard() != 0


def test_arm_preserves_original_marker_and_fsyncs_creation(tmp_path, monkeypatch):
    module = load()
    control = tmp_path / 'admission'
    control.mkdir(mode=0o700)
    real_fsync = os.fsync
    synced = []
    def fsync(fd):
        synced.append(os.fstat(fd).st_mode)
        return real_fsync(fd)
    monkeypatch.setattr(module.os, 'fsync', fsync)
    module.arm(control, 'a' * 64)
    marker = control / 'transition'
    before = marker.read_bytes()
    assert marker.stat().st_mode & 0o777 == 0o600
    assert b'a' * 64 in before
    assert len(synced) == 2
    with pytest.raises(FileExistsError):
        module.arm(control, 'b' * 64)
    assert marker.read_bytes() == before


def test_restart_permit_is_bound_to_hold_boot_and_unit_file(tmp_path, monkeypatch):
    import hashlib
    module = load()
    control = tmp_path / 'admission'; control.mkdir(mode=0o700)
    unit = tmp_path / 'factory-endpoint.service'; unit.write_text('candidate'); unit.chmod(0o600)
    monkeypatch.setattr(module, '_boot_id', lambda: '00000000-0000-4000-8000-000000000001')
    module.arm(control, 'a' * 64)
    hashes = {'factory-endpoint': [hashlib.sha256(unit.read_bytes()).hexdigest()]}
    module.authorize_restarts(control, 'a' * 64, hashes)
    with pytest.raises(RuntimeError, match='independently verified'):
        module.prestart(control, 'factory-endpoint', unit)
    unit.write_text('different')
    with pytest.raises((ValueError, RuntimeError)):
        module.prestart(control, 'factory-endpoint', unit)
    unit.write_text('candidate')
    monkeypatch.setattr(module, '_boot_id', lambda: '00000000-0000-4000-8000-000000000002')
    with pytest.raises((ValueError, RuntimeError)):
        module.prestart(control, 'factory-endpoint', unit)
    with pytest.raises((ValueError, RuntimeError)):
        module.prestart(control, 'factory-restate', unit)


def test_verified_permit_rechecks_network_and_effective_configuration(tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace
    module=load();control=tmp_path/'admission';control.mkdir(mode=0o700)
    units=tmp_path/'units';units.mkdir(mode=0o700)
    for name in ('factory-endpoint','factory-ingress'):
        path=units/(name+'.service');path.write_text(name);path.chmod(0o600)
    monkeypatch.setattr(module,'_boot_id',lambda:'00000000-0000-4000-8000-000000000001')
    state={'fence':True,'listeners':True,'config':'b'*64,'active':'0123456789abcdef'}
    def live(*args,**kwargs):
        if not state['fence']:raise ValueError('fence unavailable')
        return {'verified':True,'ingress_observed':True,'identities_sha256':('1' if kwargs['identifier'].startswith('0') else '3')*64,'nft_snapshot_sha256':'2'*64}
    def restart(*args,**kwargs):
        assert kwargs['stopping_unit']=='factory-ingress'
        if not state['fence'] or not state['listeners'] or state['active']!=kwargs['identifier']:raise ValueError('fence unavailable')
        return {'verified':True,'ingress_observed':False,'identities_sha256':('1' if kwargs['identifier'].startswith('0') else '3')*64}
    def effective(name,service_dir):
        unit=Path(service_dir)/(name+'.service')
        return {'unit_sha256':hashlib.sha256(unit.read_bytes()).hexdigest(),
                'dropin_sha256':'c'*64,'config_sha256':state['config']}
    monkeypatch.setattr(module,'_verified_modules',lambda control:(SimpleNamespace(verify_live=live,verify_restart_fence=restart),SimpleNamespace(capture_effective=effective)))
    network={'identifier':'0123456789abcdef','expected_identities':{},'expected_endpoint':{},'helper_image':'sha256:'+'a'*64,'docker_sha256':'d'*64,'nft_sha256':'e'*64}
    allowed={name:[{'unit_sha256':hashlib.sha256((units/(name+'.service')).read_bytes()).hexdigest(),'config_sha256':'b'*64}]
             for name in ('factory-endpoint','factory-ingress')}
    module.arm(control,'a'*64)
    module.authorize_verified_restarts(control,'a'*64,allowed,network,units)
    module.prestart(control,'factory-ingress',units/'factory-ingress.service')
    state['listeners']=False
    with pytest.raises(ValueError,match='verified permit'):
        module.prestart(control,'factory-ingress',units/'factory-ingress.service')
    state['listeners']=True
    replacement={**network,'identifier':'1123456789abcdef'}
    module.authorize_verified_restarts(control,'a'*64,allowed,replacement,units)
    state['active']=replacement['identifier']
    assert len(list(control.glob('restart-permit-*')))==2
    module.prestart(control,'factory-ingress',units/'factory-ingress.service')
    state['fence']=False
    with pytest.raises(ValueError,match='verified permit'):
        module.prestart(control,'factory-ingress',units/'factory-ingress.service')
    state['fence']=True;state['config']='f'*64
    with pytest.raises(ValueError,match='verified permit'):
        module.prestart(control,'factory-ingress',units/'factory-ingress.service')
