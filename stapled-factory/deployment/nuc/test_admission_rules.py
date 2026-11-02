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
