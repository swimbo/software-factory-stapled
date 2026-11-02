import hashlib
import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location('admission_install', HERE/'admission_install.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture(tmp_path):
    control=tmp_path/'control';control.mkdir(mode=0o700)
    units=tmp_path/'units';units.mkdir(mode=0o700)
    for name in ('factory-restate','factory-endpoint','factory-ingress'):
        (units/(name+'.service')).write_text('[Service]\nExecStart=/usr/bin/true\n')
    guard=HERE/'admission_marker.py';digest=hashlib.sha256(guard.read_bytes()).hexdigest()
    return control,units,guard,digest


def test_preparation_creates_only_new_guard_and_dropins(tmp_path):
    control,units,guard,digest=fixture(tmp_path)
    receipt=module.prepare_install(control,units,guard,digest)
    assert receipt['guard_sha256']==digest
    assert (control/'admission-fence/hold').stat().st_mode & 0o777 == 0o700
    for unit in ('factory-restate','factory-endpoint','factory-ingress'):
        text=(units/(unit+'.service.d')/'90-factory-admission.conf').read_text()
        assert 'ExecStartPre=' in text and unit in text
    with pytest.raises(FileExistsError):
        module.prepare_install(control,units,guard,digest)


def test_preparation_never_overwrites_existing_dropin(tmp_path):
    control,units,guard,digest=fixture(tmp_path)
    directory=units/'factory-ingress.service.d';directory.mkdir()
    existing=directory/'90-factory-admission.conf';existing.write_text('original')
    with pytest.raises(FileExistsError):module.prepare_install(control,units,guard,digest)
    assert existing.read_text()=='original' and not (control/'admission-fence').exists()
