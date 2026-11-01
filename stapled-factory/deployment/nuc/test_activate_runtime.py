from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

spec=importlib.util.spec_from_file_location('activate_runtime',Path(__file__).with_name('activate_runtime.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def fixture(tmp_path):
    root=tmp_path/'control';root.mkdir(mode=0o700);(root/'operator.lock').write_text('');(root/'operator.lock').chmod(0o600)
    (root/'rollouts').mkdir(mode=0o700);(root/'releases').mkdir(mode=0o700)
    old,new='a'*40,'b'*40
    def release(rev):
        d=root/'releases'/rev;d.mkdir(mode=0o700)
        ci=json.dumps({'revision':rev,'passed':True}).encode();(d/'local-ci.json').write_bytes(ci)
        files={'local-ci.json':hashlib.sha256(ci).hexdigest()}
        for n in ('stapled-factory/spine/dist/main.js','stapled-factory/spine/dist/ingress_main.js',
                  'stapled-factory/spine/node_modules/@restatedev/restate-sdk/package.json'):
            p=d/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'fixture');files[n]=hashlib.sha256(b'fixture').hexdigest()
        doc=json.dumps({'revision':rev,'files':files},sort_keys=True).encode()
        (d/'release-manifest.json').write_bytes(doc)
        return hashlib.sha256(doc).hexdigest()
    oldhash,newhash=release(old),release(new)
    units=tmp_path/'units';units.mkdir(mode=0o700)
    original={}
    for n in m.SERVICES:
        b=('[Service]\nWorkingDirectory=/factory/releases/'+old+'/stapled-factory/spine\n'
           'Environment=FACTORY_RUNTIME_CONFIG=/private/original.json\n').encode()
        (units/(n+'.service')).write_bytes(b);original[n]=b
    class Host:
        running=old;calls=0;busy=False;fail=False;crash=False;bad_candidate=False;runtime_config=b'/private/original.json'
        @contextmanager
        def admission_fence(self):
            self.fenced=True
            try: yield
            finally: self.fenced=False
        def capture(self):
            assert self.fenced
            config=b'/private/wrong.json' if self.bad_candidate and self.running==new else self.runtime_config
            return {'authenticated':True,'inventory_complete':True,'operator_active':self.busy,
                'workunits':{'u':{'stage':'validated'}},'namespaces':[],
                'guarded_store':{'store_id':'fixture','generation':1,'anchor_sha256':'c'*64,'tables_sha256':'d'*64},
                'services':{n:{'active':True,'pid':123,'release':self.running,
                    **({'runtime_config_sha256':hashlib.sha256(config).hexdigest()} if n=='factory-endpoint' else {})} for n in m.SERVICES}}
        def restart_services(self,names):
            assert self.fenced and names==m.SERVICES;self.calls+=1
            if self.crash:raise SystemExit('simulated crash')
            if self.fail:self.fail=False;raise RuntimeError('restart failure')
            text=(units/'factory-endpoint.service').read_text()
            self.running=new if '/releases/'+new+'/' in text else old
    h=Host();journal=root/'rollouts/unit-1'
    args=dict(control=root,service_dir=units,journal=journal,source_revision=old,source_manifest_sha256=oldhash,
              candidate_revision=new,candidate_manifest_sha256=newhash,host=h)
    return args,h,original


def test_activation_preserves_binding_and_original_intent(tmp_path):
    args,h,original=fixture(tmp_path)
    assert m.activate(**args)['activated'] is True
    assert h.calls==1 and h.running==args['candidate_revision']
    for n in m.SERVICES:
        text=(args['service_dir']/(n+'.service')).read_text()
        assert 'Environment=FACTORY_RUNTIME_CONFIG=/private/original.json' in text
    assert m.observe(args['control'],args['service_dir'],args['journal'])['state']=='activation-recorded'
    with pytest.raises(ValueError,match='original activation exists'):m.activate(**args)


def test_restart_failure_restores_exact_original(tmp_path):
    args,h,original=fixture(tmp_path);h.fail=True
    with pytest.raises(RuntimeError,match='restart failure'):m.activate(**args)
    assert h.calls==2 and h.running==args['source_revision']
    for n in m.SERVICES:assert (args['service_dir']/(n+'.service')).read_bytes()==original[n]
    assert m.observe(args['control'],args['service_dir'],args['journal'])['state']=='rollback-recorded'


def test_crash_requires_explicit_recovery_without_second_activation(tmp_path):
    args,h,original=fixture(tmp_path);h.crash=True
    with pytest.raises(SystemExit):m.activate(**args)
    assert m.observe(args['control'],args['service_dir'],args['journal'])['state']=='interrupted-or-unresolved'
    h.crash=False
    assert m.recover(control=args['control'],service_dir=args['service_dir'],journal=args['journal'],host=h)['restored']
    assert h.calls==2 and h.running==args['source_revision']
    for n in m.SERVICES:assert (args['service_dir']/(n+'.service')).read_bytes()==original[n]


def test_recovery_refuses_foreign_unit_without_mutation(tmp_path):
    args,h,_=fixture(tmp_path);h.crash=True
    with pytest.raises(SystemExit):m.activate(**args)
    victim=args['service_dir']/'factory-ingress.service';victim.write_text('foreign\n');h.crash=False
    with pytest.raises(m.ManualRecoveryRequired):
        m.recover(control=args['control'],service_dir=args['service_dir'],journal=args['journal'],host=h)
    assert victim.read_text()=='foreign\n' and h.calls==1


def test_active_work_fails_before_rollout_created(tmp_path):
    args,h,_=fixture(tmp_path);h.busy=True
    with pytest.raises(ValueError,match='complete authenticated'):m.activate(**args)
    assert not args['journal'].exists() and h.calls==0


def test_running_endpoint_binding_mismatch_fails_before_journal(tmp_path):
    args,h,_=fixture(tmp_path);h.runtime_config=b'/private/wrong.json'
    with pytest.raises(ValueError,match='runtime configuration differs'):
        m.activate(**args)
    assert not args['journal'].exists() and h.calls==0


def test_changed_running_binding_after_switch_restores_original(tmp_path):
    args,h,original=fixture(tmp_path);h.bad_candidate=True
    with pytest.raises(ValueError,match='runtime configuration differs'):
        m.activate(**args)
    assert h.calls==2 and h.running==args['source_revision']
    for n in m.SERVICES:assert (args['service_dir']/(n+'.service')).read_bytes()==original[n]


def test_missing_admission_fence_fails_before_changes(tmp_path):
    args,h,original=fixture(tmp_path)
    def unavailable(): raise RuntimeError('no verified admission fence')
    h.admission_fence=unavailable
    with pytest.raises(RuntimeError,match='no verified admission fence'):m.activate(**args)
    assert not args['journal'].exists() and h.calls==0
    for n in m.SERVICES:assert (args['service_dir']/(n+'.service')).read_bytes()==original[n]
