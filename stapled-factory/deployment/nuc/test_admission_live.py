import importlib.util
from pathlib import Path
import sys

import pytest

HERE=Path(__file__).parent
sys.path.insert(0,str(HERE))
spec=importlib.util.spec_from_file_location('admission_live',HERE/'admission_live.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_live_readback_rejects_unpinned_inputs_before_any_command(monkeypatch):
    monkeypatch.setattr(module,'_run',lambda args:pytest.fail('unexpected host command'))
    with pytest.raises(ValueError,match='pinned admission identity'):
        module.verify_live('bad',{},helper_image='latest',docker_sha256='a'*64,nft_sha256='b'*64)
    identities={name:{'pid':index} for name,index in (('probe',1),('ingress',2),('restate',3))}
    with pytest.raises(ValueError,match='pinned tool digest'):
        module.verify_live('0123456789abcdef',identities,helper_image='sha256:'+'a'*64,
                           docker_sha256='invalid',nft_sha256='b'*64)
