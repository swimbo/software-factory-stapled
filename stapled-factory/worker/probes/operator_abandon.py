"""Local real Restate/Docker proof; no NUC state or paid calls. Build spine first.
Uses only preloaded restatedev/restate:1.8.0 and python:3.13-slim images.
Creates uniquely named fixtures, retains file evidence, removes owned Docker fixtures.
"""
import argparse
import base64
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import urllib.request
import urllib.error
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from guarded_store import GuardedAttemptStore
from abandon_attempt import operation


def cmd(*args):return subprocess.check_output(list(map(str,args)),text=True).strip()
def post(url,value):
    req=urllib.request.Request(url,json.dumps(value).encode(),headers={'Content-Type':'application/json'})
    return json.load(urllib.request.urlopen(req,timeout=45))


def main(out):
    out=out.resolve();out.mkdir(mode=0o700,parents=True,exist_ok=False)
    root=Path(__file__).resolve().parents[2];spine=root/'spine'
    suffix=uuid.uuid4().hex;unit='abandon-'+suffix;attempt=unit+':0';cid=str(uuid.uuid5(uuid.NAMESPACE_URL,"stapled-factory:"+attempt))
    workers=[];volumes=[];restate_name='factory-abandon-restate-'+suffix;endpoint=None
    # Probe keys are newly generated, never production credentials.
    key=out/'operator.pem';public=out/'operator-public.pem'
    cmd('openssl','genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:2048','-out',key);key.chmod(0o600)
    cmd('openssl','pkey','-in',key,'-pubout','-out',public);public.chmod(0o600)
    (out/'db').mkdir();(out/'anchor').mkdir();db=out/'db/attempts.db';anchor=out/'anchor/watermark.json'
    store=GuardedAttemptStore.initialize(db,anchor);store.reserve(attempt,'a'*64,cid)
    _,allocation=store.reserve_allocation(attempt,'a'*64,cid,'b'*64)
    image='python:3.13-slim';budget='factory-'+allocation+'-budget'
    try:
        cmd('docker','volume','create','--label','factory.allocation='+allocation,'--label','factory.role=budget',budget);volumes.append(budget)
        for role in ('worker','proxy'):
            args=['docker','run','-d','--pull','never','--network','none','--restart','no','--label','factory.allocation='+allocation,'--label','factory.role='+role,'--mount','type=volume,src='+budget+',dst=/budget',image,'python3','-c',"import time;time.sleep(600)"]
            workers.append(cmd(*args))
        def binding():
            return {'worker_id':workers[0],'proxy_id':workers[1],
                    'worker_lifecycle':json.loads(cmd('docker','inspect',workers[0]))[0]['State'],
                    'proxy_lifecycle':json.loads(cmd('docker','inspect',workers[1]))[0]['State'],
                    'volumes':{budget:json.loads(cmd('docker','volume','inspect',budget))[0]}}
        bound=binding();store.bind_allocation(attempt,'a'*64,cid,'b'*64,allocation,bound)
        # Live allocation must fail before changing the store.
        before=db.read_bytes(),anchor.read_bytes()
        try:operation(store,unit,'--operator closure')
        except Exception:pass
        else:raise AssertionError('running worker accepted')
        assert before==(db.read_bytes(),anchor.read_bytes())
        cmd('docker','exec',workers[1],'python3','-c',"from pathlib import Path;Path('/budget/ledger.txt').write_text('unchanged reservation fixture')")
        cmd('docker','stop',*workers)
        payload=operation(store,unit,'--operator closure')
        (out/'payload.json').write_text(payload)
        signature=out/'payload.sig';cmd('openssl','dgst','-sha256','-sign',key,'-out',signature,out/'payload.json')
        envelope={'payload':payload,'signature':base64.b64encode(signature.read_bytes()).decode()}
        seed={'stage':'needs_reconciliation','build':{'attempt_id':attempt,'conversation_id':cid,'status':'unknown'},'claim':None,'receipt':None,'attempts':0,
              'recovery':{'request':{'unitId':unit},'attemptDb':str(db),'attemptAnchor':str(anchor)}}
        (out/'seed.json').write_text(json.dumps(seed))
        env=dict(os.environ,FACTORY_IMAGE_APPROVAL_KEY=str(public),FACTORY_ABANDON_TEST_SEED=str(out/'seed.json'))
        endpoint=subprocess.Popen(['node','tests/abandonment_fixture.mjs'],cwd=spine,env=env,stdout=subprocess.PIPE,stderr=(out/'endpoint.log').open('w'),text=True)
        port=None
        for _ in range(100):
            line=endpoint.stdout.readline()
            try:port=json.loads(line)['port'];break
            except (json.JSONDecodeError,KeyError):pass
            if endpoint.poll() is not None:raise RuntimeError('fixture endpoint exited')
        assert port
        data_volume='factory-abandon-data-'+suffix
        cmd('docker','volume','create','--label','factory.abandon.probe='+suffix,data_volume);volumes.append(data_volume)
        cmd('docker','run','-d','--pull','never','--name',restate_name,'--mount','type=volume,src='+data_volume+',dst=/factory-state','-p','127.0.0.1::8080','-p','127.0.0.1::9070',
            '-e','RESTATE_ADMIN__BIND_ADDRESS=0.0.0.0:9070','-e','RESTATE_INGRESS__BIND_ADDRESS=0.0.0.0:8080',
            'restatedev/restate:1.8.0','--base-dir','/factory-state','--listen-mode','tcp','--no-logo')
        ports=json.loads(cmd('docker','inspect',restate_name))[0]['NetworkSettings']['Ports']
        ingress='http://127.0.0.1:'+ports['8080/tcp'][0]['HostPort'];admin='http://127.0.0.1:'+ports['9070/tcp'][0]['HostPort']
        for _ in range(60):
            try:urllib.request.urlopen(admin+'/health',timeout=2);break
            except OSError:time.sleep(.5)
        post(admin+'/deployments',{'uri':f'http://host.docker.internal:{port}'})
        route=ingress+'/WorkUnit/'+unit
        assert post(route+'/seed',{}) is True
        invalid={**envelope,'signature':base64.b64encode(b'invalid').decode()}
        try:post(route+'/abandon',invalid)
        except urllib.error.HTTPError:pass
        else:raise AssertionError('invalid signature accepted')
        assert post(route+'/getState',{})['stage']=='needs_reconciliation'
        spec=importlib.util.spec_from_file_location('probe_operator_cli',root/'operator/cli.py')
        cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli);cli.SIGNER=key
        run=out/'operator-run';run.mkdir();(run/'operator.json').write_text(json.dumps({'unit':unit}))
        (run/'status.json').write_text(json.dumps({'status':'NEEDS_INTERVENTION','budget':{'retained':True}}))
        def transport(action,data):
            assert data['unit']==unit
            if action=='prepare_abandon':return {'payload':operation(store,unit,data['reason'])}
            assert action=='abandon' and post(route+'/abandon',data['envelope']) is True
            return {'status':'ABANDONED','completed':False,'state':post(route+'/getState',{})}
        cli.remote=transport
        assert cli.abandon(run,'--operator closure')['completed'] is False
        assert json.loads((run/'status.json').read_text())['budget']=={'retained':True}
        state=post(route+'/getState',{});assert state['stage']=='abandoned' and state['build']==seed['build']
        once=db.read_bytes(),anchor.read_bytes()
        assert post(route+'/abandon',envelope) is True
        assert once==(db.read_bytes(),anchor.read_bytes())
        cmd('docker','restart',restate_name)
        ports=json.loads(cmd('docker','inspect',restate_name))[0]['NetworkSettings']['Ports']
        ingress='http://127.0.0.1:'+ports['8080/tcp'][0]['HostPort']
        route=ingress+'/WorkUnit/'+unit
        last=None
        for _ in range(120):
            try:
                last=post(route+'/getState',{})
                if last==state:break
            except OSError as error:last=str(error)
            time.sleep(.5)
        else:
            (out/'restart-failure.json').write_text(json.dumps({'before':state,'after':last},indent=2))
            raise AssertionError('state lost after real Restate restart')
        assert post(route+'/abandon',envelope) is True
        assert once==(db.read_bytes(),anchor.read_bytes())
        with sqlite3.connect(db) as d:assert d.execute('SELECT status,result FROM attempts').fetchone()==('abandoned',None)
        # A privileged restart after abandonment is detected even on duplicates.
        cmd('docker','start',workers[0])
        try:post(route+'/abandon',envelope)
        except urllib.error.HTTPError:pass
        else:raise AssertionError('restarted worker accepted on duplicate')
        assert post(route+'/getState',{})==state
        cmd('docker','stop',workers[0])
        ledger=cmd('docker','run','--rm','--pull','never','--network','none','--mount','type=volume,src='+budget+',dst=/budget,readonly',image,'python3','-c',"from pathlib import Path;print(Path('/budget/ledger.txt').read_text())")
        assert ledger=='unchanged reservation fixture'
        proof={'real_restate_and_docker':True,'unit':unit,'stage':'abandoned','guarded_attempt_status':'abandoned','running_allocation_rejected':True,'invalid_signature_rejected':True,
               'duplicate_no_ledger_or_anchor_writes':True,'real_restate_restart_preserved_state':True,'restarted_worker_duplicate_rejected':True,'result_not_fabricated':True,'budget_fixture_unchanged':True,
               'production_nuc_untouched':True,'paid_calls':0,'real_cli_signature_and_status':True,'ssh_transport':'local fixture adapter; production SSH not exercised'}
        (out/'result.json').write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps(proof))
    finally:
        logs=subprocess.run(['docker','logs',restate_name],capture_output=True,text=True)
        (out/'restate.log').write_text(logs.stdout+logs.stderr)
        if endpoint: endpoint.terminate();endpoint.wait(timeout=10)
        for container in workers+[restate_name]:subprocess.run(['docker','rm','-f',container],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for volume in volumes:subprocess.run(['docker','volume','rm',volume],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);main(p.parse_args().out)
