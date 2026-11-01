import importlib.util
import json
from pathlib import Path

import pytest

spec=importlib.util.spec_from_file_location('activation_host',Path(__file__).with_name('activation_host.py'))
host_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(host_module)


def fixture(tmp_path,monkeypatch):
    control=tmp_path/'control';control.mkdir(mode=0o700)
    token=control/'ingress.token';token.write_text('a'*43);token.chmod(0o600)
    host=object.__new__(host_module.NucHost);host.control=control
    state={'stage':'validated'};queries=[];calls=[]
    def query(sql):
        queries.append(sql)
        if 'sys_invocation' in sql:return [{'n':0}]
        if 'COUNT(*)' in sql:return [{'n':1}]
        return [{'service_key':'unit-1','value_utf8':json.dumps(state)}]
    def request(url,body=None,token=None,expect_unauthorized=False):
        calls.append((url,token is not None,expect_unauthorized))
        if url.endswith('/health'):return {'admin_status':'Ready'}
        if expect_unauthorized:return None
        assert token=='a'*43
        return dict(state)
    monkeypatch.setattr(host,'_check_docker',lambda:None)
    monkeypatch.setattr(host,'_query',query)
    monkeypatch.setattr(host_module,'_request',request)
    monkeypatch.setattr(host,'_services',lambda:{n:{'active':True,'pid':1,'release':'a'*40, **({'runtime_config_sha256':'d'*64} if n=='factory-endpoint' else {})} for n in host_module.SERVICES})
    monkeypatch.setattr(host,'_store_and_namespaces',lambda:({'store_id':'fixture','generation':1,
        'anchor_sha256':'b'*64,'tables_sha256':'c'*64},[]))
    return host,state,queries,calls


def test_read_only_capture_cross_checks_inventory_and_authentication(tmp_path,monkeypatch):
    host,state,queries,calls=fixture(tmp_path,monkeypatch)
    out=host.capture()
    assert out['workunits']=={'unit-1':state} and out['inventory_complete'] is True
    assert sum('sys_invocation' in q for q in queries)==2
    assert sum(q[2] for q in calls)==1
    assert sum(q[1] for q in calls)==1
    assert not (host.control/'operator-active.json').exists()


def test_capture_rejects_truncated_inventory(tmp_path,monkeypatch):
    host,_,_,_=fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(host,'_query',lambda q:[{'n':2}] if 'COUNT(*)' in q else
                        [{'service_key':'unit-1','value_utf8':'{"stage":"validated"}'}])
    with pytest.raises(ValueError,match='truncated'):host._workunits()


def test_capture_fails_closed_for_unresolved_invocations(tmp_path,monkeypatch):
    host,_,_,_=fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(host,'_query',lambda q:[{'n':1}])
    with pytest.raises(ValueError,match='unresolved'):host.capture()


def test_guarded_snapshot_is_read_only_and_rejects_anchor_drift(tmp_path,monkeypatch):
    import hashlib
    import sqlite3
    control=tmp_path/'control';control.mkdir(mode=0o700)
    (control/'attempt-store').mkdir();(control/'attempt-anchor').mkdir()
    db=control/'attempt-store/attempts.db';anchor=control/'attempt-anchor/watermark.json';lock=anchor.with_name(anchor.name+'.lock')
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE metadata (store_id TEXT, generation INTEGER)')
        conn.execute("INSERT INTO metadata VALUES ('fixture',1)")
    anchor.write_text(json.dumps({'store_id':'fixture','generation':1}));anchor.chmod(0o600)
    lock.write_text('');lock.chmod(0o600)
    before=hashlib.sha256(db.read_bytes()).hexdigest();anchor_before=anchor.read_bytes()
    host=object.__new__(host_module.NucHost);host.control=control;host.docker='/fixture/docker'
    monkeypatch.setattr(host,'_command',lambda *args,**kwargs:b'')
    guarded,namespaces=host._store_and_namespaces()
    assert guarded['store_id']=='fixture' and namespaces==[]
    assert hashlib.sha256(db.read_bytes()).hexdigest()==before and anchor.read_bytes()==anchor_before
    anchor.write_text(json.dumps({'store_id':'fixture','generation':0}));anchor.chmod(0o600)
    with pytest.raises(ValueError,match='watermark'):host._store_and_namespaces()


def test_nuc_adapter_fails_closed_without_admission_fence():
    host=object.__new__(host_module.NucHost)
    with pytest.raises(RuntimeError,match='no verified admission fence'):host.admission_fence()
