import importlib.util
import os
from pathlib import Path

import pytest

spec=importlib.util.spec_from_file_location('admission_identity',Path(__file__).with_name('admission_identity.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_identity_binds_pid_start_and_real_cgroup_inode(tmp_path):
    proc=tmp_path/'proc';group=tmp_path/'cgroup';proc.mkdir();group.mkdir()
    (group/'cgroup.controllers').write_text('cpu')
    pid=proc/'321';pid.mkdir()
    # Fields after the process name start at field 3; start time is field 22.
    (pid/'stat').write_text('321 (test process) '+' '.join(['S']+['0']*18+['456'])+'\n')
    (pid/'status').write_text(f'Uid:\t{os.getuid()}\t{os.getuid()}\t{os.getuid()}\t{os.getuid()}\n')
    (pid/'cgroup').write_text('0::/user/worker\n')
    target=group/'user/worker';target.mkdir(parents=True)
    value=module.read_identity(321,os.getuid(),proc,group)
    assert value=={'pid':321,'start_ticks':456,'cgroup':'/user/worker','inode':target.stat().st_ino}
    target.rmdir();target.symlink_to(group/'user',target_is_directory=True)
    with pytest.raises((ValueError,OSError)):
        module.read_identity(321,os.getuid(),proc,group)
