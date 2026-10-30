import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from guarded_store import GuardedAttemptStore, StoreQuarantined
from abandon_attempt import operation


@pytest.fixture
def abandoned_fixture(tmp_path):
    (tmp_path/'db').mkdir();(tmp_path/'anchor').mkdir()
    store=GuardedAttemptStore.initialize(tmp_path/'db/attempts.db',tmp_path/'anchor/a.json')
    store.reserve('unit:0','r'*64,'conversation')
    _,alloc=store.reserve_allocation('unit:0','r'*64,'conversation','s'*64)
    binding={'worker_id':'a'*64,'proxy_id':'b'*64,'worker_lifecycle':{'StartedAt':'original'},
             'proxy_lifecycle':{'StartedAt':'original'},'volumes':{'budget':{'Name':'budget','CreatedAt':'t','Driver':'local','Labels':{'factory.allocation':alloc,'factory.role':'budget'}}}}
    store.bind_allocation('unit:0','r'*64,'conversation','s'*64,alloc,binding)
    def inspect(args,**kwargs):
        if args[1]=='volume':return json.dumps([binding['volumes']['budget']])
        cid=args[-1];role='worker' if cid=='a'*64 else 'proxy'
        return json.dumps([{'Id':cid,'Config':{'Labels':{'factory.allocation':alloc,'factory.role':role}},
                           'HostConfig':{'RestartPolicy':{'Name':'no'}},'State':{'Running':False,'Restarting':False,'Paused':False,'StartedAt':'original'}}])
    return store,inspect


def test_abandon_is_terminal_idempotent_and_does_not_fabricate_result(abandoned_fixture):
    store,inspect=abandoned_fixture
    with patch('abandon_attempt.subprocess.check_output',side_effect=inspect):
        payload=operation(store,'unit','explicit operator closure')
        before=json.loads(store.anchor_path.read_text())
        operation(store,'unit','explicit operator closure',payload)
        once=store.db_path.read_bytes(),store.anchor_path.read_bytes()
        assert operation(store,'unit','explicit operator closure')==payload
        assert operation(store,'unit','explicit operator closure',payload)==payload
        assert once==(store.db_path.read_bytes(),store.anchor_path.read_bytes())
    assert json.loads(store.anchor_path.read_text())['generation']==before['generation']+1
    with sqlite3.connect(store.db_path) as db:
        assert db.execute('select status,result from attempts').fetchone()==('abandoned',None)
        assert db.execute('select payload from abandonments').fetchone()[0]==payload
    with pytest.raises(StoreQuarantined,match='fenced'):store.complete('unit:0','result')
    with pytest.raises(StoreQuarantined):store.reserve_allocation('unit:0','r'*64,'conversation','s'*64)
    assert store.reserve('unit:0','r'*64,'conversation')==('unknown',None)


@pytest.mark.parametrize('change',['running','restarted','restart-policy','volume'])
def test_abandon_refuses_live_or_changed_allocation_without_writes(abandoned_fixture,change):
    store,inspect=abandoned_fixture
    before=store.db_path.read_bytes(),store.anchor_path.read_bytes()
    def changed(args,**kwargs):
        data=json.loads(inspect(args))
        if change=='volume' and args[1]=='volume':data[0]['CreatedAt']='substituted'
        elif args[1]!='volume':
            if change=='running':data[0]['State']['Running']=True
            if change=='restarted':data[0]['State']['StartedAt']='later'
            if change=='restart-policy':data[0]['HostConfig']['RestartPolicy']['Name']='always'
        return json.dumps(data)
    with patch('abandon_attempt.subprocess.check_output',side_effect=changed),pytest.raises(StoreQuarantined):
        operation(store,'unit','reason')
    assert before==(store.db_path.read_bytes(),store.anchor_path.read_bytes())


def test_stale_generation_wrong_store_successor_and_conflicting_duplicate_refused(abandoned_fixture):
    store,inspect=abandoned_fixture
    with patch('abandon_attempt.subprocess.check_output',side_effect=inspect):
        payload=operation(store,'unit','reason')
        store.reserve('other','x','other-conversation')
        with pytest.raises(StoreQuarantined):operation(store,'unit','reason',payload)
        payload=operation(store,'unit','reason');bad=json.loads(payload);bad['store_id']='another-store'
        with pytest.raises(StoreQuarantined):operation(store,'unit','reason',json.dumps(bad))
        operation(store,'unit','reason',payload)
        with pytest.raises(StoreQuarantined):operation(store,'unit','different reason')
        with pytest.raises(StoreQuarantined):operation(store,'unit','reason',payload+' ')


def test_completed_and_successor_attempts_cannot_be_abandoned(abandoned_fixture):
    store,inspect=abandoned_fixture
    with store._transaction() as (db,state):
        db.execute('CREATE TABLE restart_reservations (source_attempt TEXT)')
        db.execute("INSERT INTO restart_reservations VALUES ('unit:0')");state['changed']=True
    with patch('abandon_attempt.subprocess.check_output',side_effect=inspect),pytest.raises(StoreQuarantined):
        operation(store,'unit','reason')
    store.complete('unit:0','completed')
    with pytest.raises(StoreQuarantined):operation(store,'unit','reason')
