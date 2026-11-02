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


def test_preparation_pins_complete_guard_module_bundle(tmp_path):
    control,units,guard,digest=fixture(tmp_path)
    names=('admission_live.py','admission_identity.py','admission_nft.py','admission_effective.py','admission_install.py')
    sources={name:{'path':HERE/name,'sha256':hashlib.sha256((HERE/name).read_bytes()).hexdigest()} for name in names}
    receipt=module.prepare_install(control,units,guard,digest,module_sources=sources)
    assert set(receipt['modules'])==set(names)
    for name in names:
        assert hashlib.sha256((control/'admission-fence'/name).read_bytes()).hexdigest()==receipt['modules'][name]


def test_loaded_guard_check_rejects_stale_systemd_state(tmp_path, monkeypatch):
    control,units,guard,digest=fixture(tmp_path)
    module.prepare_install(control,units,guard,digest)
    def show(command, **kwargs):
        name=command[3]
        script=control/'admission-fence/guard.py';hold=control/'admission-fence/hold'
        argv=f'/usr/bin/python3 {script} prestart {hold} {name} {units/name}.service'
        class Result:stdout=(f'ExecStartPre={{ path=/usr/bin/python3 ; argv[]={argv} ; ignore_errors=no ; }}\n'
            f'FragmentPath={units/name}.service\nDropInPaths={units/name}.service.d/90-factory-admission.conf\n'
            'NeedDaemonReload=no\nActiveState=active\nMainPID=123\n')
        return Result()
    monkeypatch.setattr(module.subprocess,'run',show)
    assert module.verify_loaded(control,units,digest)['loaded'] is True
    target=units/'factory-ingress.service.d/90-factory-admission.conf'
    target.write_text(target.read_text()+'# changed\n')
    with pytest.raises(ValueError,match='drop-in'):
        module.verify_loaded(control,units,digest)
