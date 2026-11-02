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
