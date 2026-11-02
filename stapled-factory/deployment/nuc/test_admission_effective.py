import importlib.util
from pathlib import Path
import sys

import pytest

HERE=Path(__file__).parent;sys.path.insert(0,str(HERE))
spec=importlib.util.spec_from_file_location('admission_effective',HERE/'admission_effective.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_effective_configuration_is_bound_without_exposing_environment(tmp_path,monkeypatch):
    units=tmp_path/'units';units.mkdir(mode=0o700)
    unit=units/'factory-ingress.service';unit.write_text('[Service]\nExecStart=/usr/bin/true\n')
    drop=units/'factory-ingress.service.d';drop.mkdir();path=drop/'90-factory-admission.conf'
    path.write_text('[Service]\nExecStartPre=/usr/bin/true\n')
    state={'environment':'SENSITIVE_EXAMPLE=value\n','reload':'no'}
    def command(args):
        if 'show-environment' in args:return state['environment']
        return (f'FragmentPath={unit}\nDropInPaths={path}\nNeedDaemonReload={state["reload"]}\n'
                'WorkingDirectory=/fixture\nUser=\nGroup=\nEnvironment=FIXTURE=value\nEnvironmentFiles=\n'
                'ExecStart={ path=/usr/bin/true ; argv[]=/usr/bin/true ; ignore_errors=no ; start_time=[n/a] ; }\n'
                'ExecStartPre={ path=/usr/bin/true ; argv[]=/usr/bin/true ; ignore_errors=no ; start_time=[n/a] ; }\n')
    monkeypatch.setattr(module,'_command',command)
    original=module.capture_effective('factory-ingress',units)
    assert 'SENSITIVE_EXAMPLE' not in str(original)
    state['environment']='SENSITIVE_EXAMPLE=changed\n'
    assert module.capture_effective('factory-ingress',units)['config_sha256'] != original['config_sha256']
    state['reload']='yes'
    with pytest.raises(ValueError,match='reload'):
        module.capture_effective('factory-ingress',units)
