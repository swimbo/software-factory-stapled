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


def test_restart_readback_checks_retained_ingress_rule_without_requiring_dead_pid(monkeypatch):
    import json
    identities={name:{'pid':index,'start_ticks':index*10,'cgroup':path,'inode':inode}
                for name,index,path,inode in (('probe',1,'/user/probe',17),('ingress',2,'/user/ingress',34),('restate',3,'/system/restate',51))}
    snapshot='''table inet factory_hold_0123456789abcdef { # handle 1
 chain output { # handle 1
  type filter hook output priority mangle - 1; policy accept;
  ip daddr 127.0.0.0/8 tcp dport { 5122, 18080, 19070, 19080, 19081 } jump guard # handle 4
  ip6 daddr ::1 tcp dport { 5122, 18080, 19070, 19080, 19081 } jump guard # handle 6
 }
 chain guard { # handle 2
  socket cgroupv2 level 2 "user/probe" return # handle 7
  tcp dport 18080 socket cgroupv2 level 2 "user/ingress" return # handle 8
  tcp dport { 5122, 19080 } socket cgroupv2 level 2 "system/restate" return # handle 10
  meta l4proto tcp reject with tcp reset # handle 11
 }
}
'''
    debug=''.join(f'[ socket load cgroupv2 => reg 1 ]\n[ cmp eq reg 1 0x{value:08x} 0x00000000 ]\n' for value in (17,34,51))
    hooks=json.dumps({'nftables':[{'chain':{'family':'inet','table':'factory_hold_0123456789abcdef','name':'output','hook':'output','prio':-151}}]})
    image='sha256:'+'a'*64
    monkeypatch.setattr(module,'_tool',lambda path,digest:str(path))
    def identity(pid, uid):
        if pid==2:pytest.fail('old ingress process must not be required during restart')
        return next(value for value in identities.values() if value['pid']==pid)
    monkeypatch.setattr(module,'read_identity',identity)
    monkeypatch.setattr(module,'require_protected_cgroup',lambda path,pid:None)
    def run(args):
        if args[1:3]==['image','inspect']:return image+'\n'
        if '--debug' in args:return debug
        if '-j' in args:return hooks
        return snapshot
    monkeypatch.setattr(module,'_run',run)
    result=module.verify_restart_fence('0123456789abcdef',identities,helper_image=image,docker_sha256='a'*64,nft_sha256='b'*64)
    assert result['verified'] is True and result['ingress_observed'] is False
