import importlib.util,json,sys
from pathlib import Path
import pytest
SRC=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(SRC/'worker'),str(SRC/'deployment/nuc')]
import evaluation_trials as mod

def fixture(tmp_path,monkeypatch):
 import image_approval,operator_cli
 monkeypatch.setattr(image_approval,'verify_approval',lambda *a:None)
 monkeypatch.setattr(operator_cli,'_private_file',lambda *a:b'private')
 cfg={'root':str(tmp_path/'root'),'setup':str(tmp_path/'setup'),'approval':'a','signature':'b','trust_root':'c','release':'/releases/frozen'}
 root=Path(cfg['root']);setup=Path(cfg['setup']);root.mkdir();setup.mkdir();trial=root/'dispatch/u';trial.mkdir(parents=True);(setup/'results').mkdir()
 (setup/'preregister.json').write_text(json.dumps({'schedule':[{'unit':'u','task_id':'t','profile':'baseline','runtime_sha256':'h'}]}))
 monkeypatch.setattr(mod,'config_file',lambda *a:{'provision':{},'repo':'/workspace','task_file':'/task'});monkeypatch.setattr(mod,'digest',lambda *a:'h')
 home=tmp_path/'home';service=home/'.config/systemd/user/factory-endpoint.service';service.parent.mkdir(parents=True);service.write_text('Environment=FACTORY_RUNTIME_CONFIG=/releases/frozen/default.json\n')
 monkeypatch.setattr(Path,'home',classmethod(lambda cls:home))
 return cfg,root,setup,trial,service

def test_recover_after_restore_before_final_output(tmp_path,monkeypatch):
 cfg,root,setup,trial,service=fixture(tmp_path,monkeypatch)
 (trial/'baseline.previous.service').write_text(service.read_text());(trial/'baseline.desired.service').write_text('desired\n');(trial/'baseline.dispatch-intent.json').write_text('{}')
 (trial/'evaluation.collected.json').write_text(json.dumps({'accepted':True}))
 value=mod.run(cfg,'u')
 assert value['binding_restored'] is True and (setup/'results/u.json').exists()

def test_poll_failure_contains_original_without_second_dispatch(tmp_path,monkeypatch):
 cfg,root,setup,trial,service=fixture(tmp_path,monkeypatch);calls=[];reads=[0]
 monkeypatch.setattr(mod,'command',lambda *a,**k:'')
 def post(*a):
  reads[0]+=1
  if reads[0]>2:raise TimeoutError('state read unavailable')
  return {'stage':'unclaimed'}
 monkeypatch.setattr(mod,'post',post)
 def dispatch(intent,*a):
  mod.write_new(intent,{'started_at_unix':__import__('time').time()});calls.append('dispatch');return {'stage':'building'}
 monkeypatch.setattr(mod,'dispatch_once',dispatch);monkeypatch.setattr(mod,'trial_binding',lambda *a:{'worker_id':'w','proxy_id':'p'})
 monkeypatch.setattr(mod,'_stop_original',lambda *a:calls.append('stop-original'))
 with pytest.raises(TimeoutError):mod.run(cfg,'u')
 assert calls==['dispatch','stop-original']


def bound(fix):
 cfg,root,setup,trial,service=fix
 old=service.read_text()
 (trial/'baseline.previous.service').write_text(old)
 (trial/'baseline.desired.service').write_text('desired\n')
 (trial/'baseline.dispatch-intent.json').write_text(json.dumps({'started_at_unix':1}))
 (root/'operator-active.json').write_text(json.dumps({'unit':'u','owner':'held-out-evaluation'}))
 return fix


def test_existing_intent_default_refuses_without_dispatch_or_service_writes(tmp_path,monkeypatch):
 cfg,root,setup,trial,service=bound(fixture(tmp_path,monkeypatch));before=service.read_bytes();calls=[]
 monkeypatch.setattr(mod,'command',lambda *a,**k:calls.append(('command',a)))
 monkeypatch.setattr(mod,'dispatch_once',lambda *a:calls.append(('dispatch',a)))
 monkeypatch.setattr(mod,'post',lambda *a:calls.append(('post',a)))
 with pytest.raises(ValueError,match='original dispatch'):mod.run(cfg,'u')
 assert calls==[] and service.read_bytes()==before
 assert not (setup/'results/u.json').exists()


def test_foreign_binding_is_never_restored(tmp_path,monkeypatch):
 cfg,root,setup,trial,service=bound(fixture(tmp_path,monkeypatch));service.write_text('foreign\n')
 (trial/'evaluation.collected.json').write_text(json.dumps({'accepted':True}))
 calls=[];monkeypatch.setattr(mod,'command',lambda *a,**k:calls.append(a))
 with pytest.raises(ValueError,match='service changed'):mod.run(cfg,'u')
 assert service.read_text()=='foreign\n' and calls==[]
 assert (root/'operator-active.json').exists() and not (setup/'results/u.json').exists()


def test_runtime_hash_mismatch_precedes_binding_or_dispatch_effects(tmp_path,monkeypatch):
 cfg,root,setup,trial,service=fixture(tmp_path,monkeypatch);before=service.read_bytes();calls=[]
 monkeypatch.setattr(mod,'digest',lambda *a:'changed')
 monkeypatch.setattr(mod,'post',lambda *a:calls.append(a))
 monkeypatch.setattr(mod,'command',lambda *a,**k:calls.append(a))
 with pytest.raises(ValueError,match='frozen runtime'):mod.run(cfg,'u')
 assert calls==[] and service.read_bytes()==before and not (root/'operator-active.json').exists()
 assert not (trial/'baseline.previous.service').exists()


def test_cli_observe_only_collects_original_without_orchestrate_or_service_restart(tmp_path,monkeypatch,capsys):
 cfg,root,setup,trial,service=bound(fixture(tmp_path,monkeypatch));calls=[]
 original_config=mod.config_file
 monkeypatch.setattr(mod,'config_file',lambda path:cfg if str(path)=='config.json' else original_config(path))
 monkeypatch.setattr(mod,'post',lambda *a:(_ for _ in ()).throw(AssertionError('no HTTP execution in observe control path')))
 monkeypatch.setattr(mod,'dispatch_once',lambda *a:(_ for _ in ()).throw(AssertionError('no redispatch')))
 monkeypatch.setattr(mod,'command',lambda *a,**k:(_ for _ in ()).throw(AssertionError('no service restart')))
 def collect(config,entry,runtime,started,finished):
  calls.append((entry['unit'],started));return {'unit':'u','accepted':False,'requests':1,'accounting_complete':True,'resources_stopped':True}
 monkeypatch.setattr(mod,'collect',collect)
 monkeypatch.setattr(sys,'argv',['evaluation_trials.py','--config','config.json','--unit','u','--observe-only'])
 mod.main()
 result=json.loads(capsys.readouterr().out)
 assert calls==[('u',1)] and result['binding_restored'] is True
 assert (trial/'evaluation.collected.json').exists() and (setup/'results/u.json').exists()
 assert not (root/'operator-active.json').exists()


def test_observe_only_without_original_intent_creates_no_binding_or_dispatch(tmp_path,monkeypatch):
 cfg,root,setup,trial,service=fixture(tmp_path,monkeypatch);before=service.read_bytes();calls=[]
 monkeypatch.setattr(mod,'post',lambda *a:calls.append(a) or {'stage':'unclaimed'})
 monkeypatch.setattr(mod,'dispatch_once',lambda *a:(_ for _ in ()).throw(AssertionError('no dispatch')))
 monkeypatch.setattr(mod,'command',lambda *a,**k:(_ for _ in ()).throw(AssertionError('no service restart')))
 with pytest.raises(ValueError,match='original dispatch intent'):mod.run(cfg,'u',observe_only=True)
 assert service.read_bytes()==before and not (root/'operator-active.json').exists()
 assert not (trial/'baseline.previous.service').exists() and not (trial/'baseline.desired.service').exists()
 assert not (setup/'results/u.json').exists()


def test_reconciliation_is_not_terminal_while_original_sdk_is_running(tmp_path,monkeypatch):
 import baseline_trials
 monkeypatch.setattr(baseline_trials,'command',lambda *a,**k:'')
 cfg,root,setup,trial,service=fixture(tmp_path,monkeypatch);stages=[];calls=[]
 monkeypatch.setattr(mod,'command',lambda *a,**k:'')
 def post(*args):
  route=args[1]
  if not (trial/'baseline.dispatch-intent.json').exists():return {'stage':'unclaimed'}
  stage='needs_reconciliation' if not stages else 'needs_validation';stages.append(stage);return {'stage':stage}
 monkeypatch.setattr(mod,'post',post)
 def dispatch(intent,*args):
  mod.write_new(intent,{'started_at_unix':__import__('time').time()});calls.append('dispatch');return {'stage':'needs_reconciliation'}
 monkeypatch.setattr(mod,'dispatch_once',dispatch);monkeypatch.setattr(mod.time,'sleep',lambda *a:None)
 def collect(*args):
  assert stages==['needs_reconciliation','needs_validation'],'active SDK was cancelled before read-only reconciliation completed'
  return {'accepted':True}
 monkeypatch.setattr(mod,'collect',collect)
 assert mod.run(cfg,'u')['accepted'] is True
 assert calls==['dispatch']
