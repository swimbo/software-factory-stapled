import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('admission_rules', Path(__file__).with_name('admission_rules.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_rules_reject_ambiguous_cgroups_and_unsafe_names():
    with pytest.raises(ValueError):
        module.render_install('invalid;id', '/user/probe', '/user/ingress', '/system/restate')
    with pytest.raises(ValueError):
        module.render_install('0123456789abcdef', '/user', '/user/ingress', '/system/restate')
    with pytest.raises(ValueError):
        module.render_install('0123456789abcdef', '/user/probe', '/user/../ingress', '/system/restate')


def test_rebind_never_adds_a_second_output_jump():
    install = module.render_install('0123456789abcdef', '/user/probe', '/user/ingress', '/system/restate')
    rebind = module.render_rebind('0123456789abcdef', '/user/probe', '/user/new-ingress', '/system/restate')
    assert install.count('-I OUTPUT 1 -j FCT_H_0123456789abcdef') == 1
    assert '-I OUTPUT' not in rebind
    assert 'user/new-ingress' in rebind


def test_firewall_readback_requires_first_jump_and_exact_rules():
    snapshot = '''*filter
:INPUT ACCEPT [0:0]
:OUTPUT ACCEPT [0:0]
:FCT_H_0123456789abcdef - [0:0]
-A OUTPUT -d 127.0.0.1/32 -p tcp -m multiport --dports 5122,18080,19070,19080,19081 -j FCT_H_0123456789abcdef
-A FCT_H_0123456789abcdef -m cgroup --path "user/probe" -j RETURN
-A FCT_H_0123456789abcdef -p tcp -m tcp --dport 18080 -m cgroup --path "user/ingress" -j RETURN
-A FCT_H_0123456789abcdef -p tcp -m multiport --dports 5122,19080 -m cgroup --path "system/restate" -j RETURN
-A FCT_H_0123456789abcdef -p tcp -j REJECT --reject-with tcp-reset
COMMIT
'''
    args = ('0123456789abcdef', '/user/probe', '/user/ingress', '/system/restate')
    module.verify_snapshot(snapshot, *args)
    with pytest.raises(ValueError):
        module.verify_snapshot(snapshot.replace('-A OUTPUT -d', '-A OUTPUT -j ACCEPT\n-A OUTPUT -d'), *args)
    with pytest.raises(ValueError):
        module.verify_snapshot(snapshot.replace('--dport 18080', '--dport 19070'), *args)
