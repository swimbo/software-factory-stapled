import importlib.util
import json
from pathlib import Path

import pytest


def load():
    path = Path(__file__).with_name('baseline_trials.py')
    spec = importlib.util.spec_from_file_location('baseline_trials', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_lost_dispatch_response_is_observed_without_another_post(tmp_path):
    mod = load(); calls=[]
    def post(route, value):
        calls.append(route)
        if route.startswith('orchestrate/'):
            raise TimeoutError('accepted but response lost')
        return {'stage':'needs_reconciliation' if any(x.startswith('orchestrate/') for x in calls) else 'unclaimed'}
    intent=tmp_path/'intent.json'
    mod.dispatch_once(intent, 'trial', {'unitId':'trial'}, post)
    assert intent.exists()
    mod.dispatch_once(intent, 'trial', {'unitId':'trial'}, post)
    assert sum(x.startswith('orchestrate/') for x in calls) == 1


def test_frozen_schedule_has_three_independent_attempts_per_task():
    mod=load()
    tasks=[{'id':'a'}, {'id':'b'}]
    schedule=mod.schedule(tasks, 'baseline')
    assert [x['task_id'] for x in schedule] == ['a','b','a','b','a','b']
    assert len({x['unit'] for x in schedule}) == 6
    assert all(x['repeat'] in (1,2,3) for x in schedule)


def test_scope_check_rejects_extra_and_empty_changes():
    mod=load()
    assert mod.in_scope(['catalog.py'], ['catalog.py'])
    assert not mod.in_scope([], ['catalog.py'])
    assert not mod.in_scope(['catalog.py','check.py'], ['catalog.py'])


def test_frozen_candidate_rejects_late_workspace_changes(tmp_path):
    import subprocess
    mod=load();repo=tmp_path/'repo';repo.mkdir()
    def git(*args):return subprocess.check_output(['git','-C',str(repo),*args],text=True).strip()
    git('init','-q');git('config','user.name','test');git('config','user.email','test@example.invalid')
    (repo/'catalog.py').write_text('value=1\n');git('add','.');git('commit','-qm','baseline');base=git('rev-parse','HEAD')
    (repo/'catalog.py').write_text('value=2\n');git('add','.');git('commit','-qm','candidate');revision=git('rev-parse','HEAD')
    digest=mod.freeze_candidate(str(repo),base,revision,tmp_path/'frozen')
    assert len(digest)==64 and (tmp_path/'frozen/catalog.py').read_text()=='value=2\n'
    (repo/'catalog.py').write_text('value=3\n')
    with pytest.raises(ValueError,match='exact clean result'):
        mod.freeze_candidate(str(repo),base,revision,tmp_path/'late')


def test_checker_timeout_cleans_the_owned_container(tmp_path,monkeypatch):
    import subprocess
    mod=load();calls=[];created=[False]
    entry={'unit':'baseline-one','task_id':'small-edit'}
    runtime={'provision':{'helper_image':'sha256:'+'a'*64}}
    def command(*args,**kwargs):
        calls.append(args)
        if args[:2]==('docker','inspect'):
            if not created[0]:raise subprocess.CalledProcessError(1,args)
            return json.dumps([{'Id':'b'*64,'Image':runtime['provision']['helper_image'],
                 'Config':{'Labels':{'factory.baseline.unit':'baseline-one'}}}])
        return ''
    def timeout(*args,**kwargs):
        created[0]=True;raise subprocess.TimeoutExpired(args[0],30)
    monkeypatch.setattr(mod,'command',command);monkeypatch.setattr(mod.subprocess,'run',timeout)
    result=mod.check_candidate(entry,runtime,tmp_path)
    assert result['timed_out'] and not result['passed']
    assert ('docker','rm','-f','b'*64) in calls
