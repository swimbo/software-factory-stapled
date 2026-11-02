import importlib.util
from pathlib import Path

import pytest

spec=importlib.util.spec_from_file_location('admission_nft',Path(__file__).with_name('admission_nft.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_nft_transaction_rejects_overlapping_or_unsafe_cgroups():
    with pytest.raises(ValueError):module.render_install('bad;id','/a/probe','/a/ingress','/system/restate')
    with pytest.raises(ValueError):module.render_install('0123456789abcdef','/a','/a/ingress','/system/restate')
    with pytest.raises(ValueError):module.render_install('0123456789abcdef','/a/probe','/a/../ingress','/system/restate')


def test_nft_rebind_replaces_only_owned_table():
    args=('0123456789abcdef','/user/probe','/user/ingress','/system/restate')
    install=module.render_install(*args);rebind=module.render_rebind(*args)
    assert rebind == 'delete table inet factory_hold_0123456789abcdef\n'+install
    assert 'flush ruleset' not in rebind


def test_nft_readback_rejects_changed_cgroup_and_extra_rules():
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
    args=('0123456789abcdef','/user/probe','/user/ingress','/system/restate')
    module.verify_snapshot(snapshot,*args)
    with pytest.raises(ValueError):module.verify_snapshot(snapshot.replace('level 2 "user/ingress"','level 1 "user/ingress"'),*args)
    with pytest.raises(ValueError):module.verify_snapshot(snapshot.replace('chain guard {','chain guard {\n  accept'),*args)
    import json
    identities={name:{'pid':index,'start_ticks':index*10,'cgroup':path,'inode':inode}
                for name,index,path,inode in (('probe',1,'/user/probe',17),('ingress',2,'/user/ingress',34),('restate',3,'/system/restate',51))}
    debug=''.join(f'[ socket load cgroupv2 => reg 1 ]\n[ cmp eq reg 1 0x{value:08x} 0x00000000 ]\n' for value in (17,34,51))
    hooks=json.dumps({'nftables':[{'chain':{'family':'inet','table':'factory_hold_0123456789abcdef','name':'output','hook':'output','prio':-151}}]})
    assert module.verify_live_evidence('0123456789abcdef',identities,identities,snapshot,debug,hooks)['verified'] is True
    changed=json.loads(json.dumps(identities));changed['ingress']['inode']=35
    with pytest.raises(ValueError):
        module.verify_live_evidence('0123456789abcdef',identities,changed,snapshot,debug,hooks)


def test_kernel_readback_requires_exact_current_cgroup_ids():
    debug='''[ socket load cgroupv2 => reg 1 ]
[ cmp eq reg 1 0x00000011 0x00000000 ]
[ socket load cgroupv2 => reg 1 ]
[ cmp eq reg 1 0x00000022 0x00000000 ]
[ socket load cgroupv2 => reg 1 ]
[ cmp eq reg 1 0x00000033 0x00000000 ]'''
    module.verify_kernel_ids(debug,[17,34,51])
    with pytest.raises(ValueError):module.verify_kernel_ids(debug,[17,35,51])
    with pytest.raises(ValueError):module.verify_kernel_ids(debug+'\n'+debug,[17,34,51])


def test_other_output_hook_cannot_precede_owned_fence():
    import json
    owned={'chain':{'family':'inet','table':'factory_hold_0123456789abcdef','name':'output','hook':'output','prio':-151}}
    later={'chain':{'family':'ip','table':'mangle','name':'OUTPUT','hook':'output','prio':-150}}
    module.verify_hook_order(json.dumps({'nftables':[owned,later]}),'0123456789abcdef')
    earlier={'chain':{'family':'ip','table':'raw','name':'OUTPUT','hook':'output','prio':-300}}
    with pytest.raises(ValueError):
        module.verify_hook_order(json.dumps({'nftables':[owned,earlier]}),'0123456789abcdef')
