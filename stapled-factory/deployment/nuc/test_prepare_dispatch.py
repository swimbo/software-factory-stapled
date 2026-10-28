import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

spec = importlib.util.spec_from_file_location('prepare_dispatch', Path(__file__).with_name('prepare_dispatch.py'))
prep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prep)


def fixture(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    release = tmp_path/'release'; worker = release/'stapled-factory/worker'
    worker.mkdir(parents=True); (worker/'worker.py').write_text('')
    manifest = tmp_path/'images.json'
    manifest.write_text(json.dumps({**{k:'sha256:'+str(i)*64 for i,k in enumerate(
        ('worker_image','proxy_image','helper_image','firewall_image'),1)},'worker_environment':['PATH=/usr/bin']}))
    manifest.chmod(0o600)
    files = {}
    for name in ('approval','signature','trust_root'):
        path = tmp_path/name; path.write_text('test'); path.chmod(0o600); files[name]=path
    monkeypatch.setattr(prep,'verify_approval',lambda *a: None)
    return dict(root=tmp_path/'tasks',unit='bounded-unit',manifest=manifest,release=release,
        attempt_db=tmp_path/'attempt-store/attempts.db',attempt_anchor=tmp_path/'attempt-anchor/watermark.json',
        task_text='Create and commit one hello.txt file.\n',**files)


def test_preparation_matches_operator_schema_and_refuses_reuse(tmp_path, monkeypatch):
    args = fixture(tmp_path,monkeypatch)
    result = prep.prepare(**args)
    config_path = Path(result['environment']['FACTORY_RUNTIME_CONFIG'])
    config = json.loads(config_path.read_text())
    from types import SimpleNamespace
    from operator_cli import load_config
    monkeypatch.setenv('FACTORY_RUNTIME_CONFIG',str(config_path))
    monkeypatch.setenv('FACTORY_IMAGE_APPROVAL_KEY',str(args['trust_root']))
    import image_approval
    monkeypatch.setattr(image_approval,'verify_approval',lambda *a: None)
    actual, payload = load_config(SimpleNamespace(attempt_id='bounded-unit:0',
        task_file=config['task_file'],repo=config['repo']))
    assert actual['deployment_scope']=='sandbox'
    assert payload['agent_settings']['llm']['max_output_tokens']==1024
    assert '/opt/factory/egress_proxy.py' in actual['provision']['proxy_command']
    assert result['status']=='prepared_not_activated'
    assert config_path.stat().st_mode & 0o777 == 0o600
    assert Path(config['repo']).stat().st_mode & 0o777 == 0o700
    assert prep.git('-C',config['repo'],'ls-tree','--name-only','HEAD')==''
    assert prep.git('-C',config['successor_workspace'],'rev-parse','HEAD')==config['baseline']
    with pytest.raises(FileExistsError): prep.prepare(**args)


def test_invalid_approval_has_no_preparation_effects(tmp_path,monkeypatch):
    args=fixture(tmp_path,monkeypatch)
    def reject(*_): raise ValueError('signature invalid')
    monkeypatch.setattr(prep,'verify_approval',reject)
    with pytest.raises(ValueError,match='signature'): prep.prepare(**args)
    assert not args['root'].exists()
    assert not args['attempt_db'].exists()


def test_partial_store_is_preserved_and_quarantined(tmp_path,monkeypatch):
    args=fixture(tmp_path,monkeypatch)
    args['attempt_db'].parent.mkdir(mode=0o700)
    args['attempt_db'].write_text('preserve')
    with pytest.raises(prep.StoreQuarantined): prep.prepare(**args)
    assert args['attempt_db'].read_text()=='preserve'
    assert not (args['root']/args['unit']).exists()


def test_changed_store_watermark_is_not_reset(tmp_path,monkeypatch):
    args=fixture(tmp_path,monkeypatch); prep.prepare(**args)
    mark=json.loads(args['attempt_anchor'].read_text()); mark['generation']+=1
    args['attempt_anchor'].write_text(json.dumps(mark))
    args['unit']='another-unit'
    with pytest.raises(prep.StoreQuarantined): prep.prepare(**args)
    assert not (args['root']/args['unit']).exists()
