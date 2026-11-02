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
