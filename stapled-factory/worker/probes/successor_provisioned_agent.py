"""Disposable experimental successor and proxy-firewall integration probe using a dummy credential.

Requires a locally cached python:3.11-slim image and Docker. No inference
credential is used. Provisioning failures can retain budget evidence volumes.
"""
import json, os, subprocess, sys, tempfile, time, uuid
from contextlib import nullcontext
from pathlib import Path
ROOT = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, ROOT)
from guarded_store import GuardedAttemptStore
from provision_runtime import provision_once, inspect_bound_allocation, ProvisionError
from crash_fence import fence_bound_worker
from successor_runtime import provision_successor, inspect_successor
import hashlib
from openhands_adapter import OpenHandsAdapter
from docker_transport import DockerTransport
from worker import execute_bound_successor
import provision_runtime as provision_module
_provision = provision_module.provision
owned_allocation = None
def record_provision(**kwargs):
    global owned_allocation
    owned_allocation = kwargs['allocation_id']
    return _provision(**kwargs)
provision_module.provision = record_provision


def docker(*args):
    return subprocess.run(['docker', *args], check=True, capture_output=True, text=True).stdout.strip()

base = docker('image', 'inspect', 'python:3.11-slim', '--format', '{{.Id}}')
base_env = ['PATH=/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin']
scratch = tempfile.TemporaryDirectory(prefix='factory-egress-probe-')
directory = Path(scratch.name)
for name in ('egress_proxy.py', 'guarded_store.py', 'pinned_https.py', 'budget_authority.py'):
    (directory/name).write_bytes((Path(ROOT)/name).read_bytes())
for name in ('egress_bridge.py', 'agent_entrypoint.py'):
    (directory/name).write_bytes((Path(ROOT)/name).read_bytes())
(directory/'fake_proxy.py').write_text('import http.client, io, json, time, sys, runpy\nfrom pathlib import Path\nsys.path.insert(0, \'/tmp\')\nclass Response:\n status=200\n def __init__(self, body):\n  self.body=io.BytesIO(body)\n def getheader(self,n,default=None): return \'text/event-stream\'\n def read1(self,n): return self.body.read(n)\nclass Conn:\n def __init__(self,*a,**kw): pass\n def request(self,method,path,raw,headers):\n  x=json.loads(raw)\n  with open(\'/tmp/requests\',\'a\') as f: f.write(json.dumps({\'tools\':[t.get(\'name\') for t in x.get(\'tools\',[])], \'stream\':x.get(\'stream\')})+\'\\n\')\n  tools=x.get(\'tools\',[])\n  if tools and not Path(\'/tmp/first-inflight\').exists():\n   Path(\'/tmp/first-inflight\').touch(); time.sleep(20)\n  finish=next((t[\'name\'] for t in tools if \'finish\' in t.get(\'name\',\'\').lower()), None)\n  if finish:\n   block={\'type\':\'tool_use\',\'id\':\'toolu_1\',\'name\':finish,\'input\':{}}\n   delta={\'type\':\'input_json_delta\',\'partial_json\':\'{"message":"done"}\'}\n   reason=\'tool_use\'\n  else:\n   block={\'type\':\'text\',\'text\':\'\'}; delta={\'type\':\'text_delta\',\'text\':\'done\'}; reason=\'end_turn\'\n  events=[(\'message_start\',{\'type\':\'message_start\',\'message\':{\'id\':\'msg_1\',\'type\':\'message\',\'role\':\'assistant\',\'content\':[],\'model\':x[\'model\'],\'stop_reason\':None,\'stop_sequence\':None,\'usage\':{\'input_tokens\':1,\'output_tokens\':1}}}),(\'content_block_start\',{\'type\':\'content_block_start\',\'index\':0,\'content_block\':block}),(\'content_block_delta\',{\'type\':\'content_block_delta\',\'index\':0,\'delta\':delta}),(\'content_block_stop\',{\'type\':\'content_block_stop\',\'index\':0}),(\'message_delta\',{\'type\':\'message_delta\',\'delta\':{\'stop_reason\':reason,\'stop_sequence\':None},\'usage\':{\'output_tokens\':1}}),(\'message_stop\',{\'type\':\'message_stop\'})]\n  if x.get(\'stream\'):\n   data=\'\'.join(\'event: \'+n+\'\\ndata: \'+json.dumps(y)+\'\\n\\n\' for n,y in events).encode(); self.response=Response(data)\n  else:\n   content=({\'type\':\'tool_use\',\'id\':\'toolu_1\',\'name\':finish,\'input\':{\'message\':\'done\'}} if finish else {\'type\':\'text\',\'text\':\'done\'})\n   self.response=Response(json.dumps({\'id\':\'msg_1\',\'type\':\'message\',\'role\':\'assistant\',\'content\':[content],\'model\':x[\'model\'],\'stop_reason\':reason,\'stop_sequence\':None,\'usage\':{\'input_tokens\':1,\'output_tokens\':1}}).encode()); self.response.getheader=lambda n,default=None:\'application/json\'\n def getresponse(self): return self.response\n def close(self): pass\nhttp.client.HTTPSConnection=Conn\n\nimport pinned_https\npinned_https.PinnedHTTPSConnection=Conn\nrunpy.run_path(\'/tmp/egress_proxy.py\', run_name=\'__main__\')\n')
if os.environ.get('FACTORY_PROBE_TOOL'):
    fake_path = directory / 'fake_proxy.py'
    code = fake_path.read_text()
    start, end = code.index('  finish=next('), code.index('  events=', code.index('  finish=next('))
    code = code[:start] + '''  finish=next((t['name'] for t in tools if 'finish' in t.get('name','').lower()), None)
  terminal=next((t for t in tools if 'terminal' in t.get('name','').lower() or 'bash' in t.get('name','').lower()), None)
  if terminal and not any(b.get('type') == 'tool_result' for m in x.get('messages', []) for b in (m.get('content', []) if isinstance(m.get('content'), list) else [])):
   props=terminal.get('input_schema',{}).get('properties',{})
   key=next((k for k in ('command','cmd') if k in props),None)
   if not key: raise RuntimeError('terminal schema missing command')
   arg={key:'cd /workspace && printf "agent effect\\n" > agent-effect.txt && git -c safe.directory=/workspace -c user.name=Probe -c user.email=probe@example.invalid add agent-effect.txt && git -c safe.directory=/workspace -c user.name=Probe -c user.email=probe@example.invalid commit -m agent-effect'}
   block={'type':'tool_use','id':'toolu_1','name':terminal['name'],'input':{}}
   delta={'type':'input_json_delta','partial_json':json.dumps(arg)}
   reason='tool_use'
  elif finish:
   block={'type':'tool_use','id':'toolu_2','name':finish,'input':{}}
   delta={'type':'input_json_delta','partial_json':'{"message":"done"}'}
   reason='tool_use'
  else:
   block={'type':'text','text':''}; delta={'type':'text_delta','text':'done'}; reason='end_turn'
''' + code[end:]
    old = "   content=({'type':'tool_use','id':'toolu_1','name':finish,'input':{'message':'done'}} if finish else {'type':'text','text':'done'})"
    assert old in code
    code = code.replace(old, "   content=({**block, 'input':json.loads(delta['partial_json'])} if block['type']=='tool_use' else {'type':'text','text':'done'})")
    fake_path.write_text(code)
live = bool(os.environ.get('FACTORY_PROBE_LIVE'))
if live:
    proxy_path = directory / 'egress_proxy.py'
    code = proxy_path.read_text()
    code = code.replace("            response = conn.getresponse()", "            open('/tmp/live-inflight', 'w').close()\n            response = conn.getresponse()")
    code = code.replace("            conn.close()", "            __import__('pathlib').Path('/tmp/live-inflight').unlink(missing_ok=True)\n            conn.close()")
    proxy_path.write_text(code)
suffix = uuid.uuid4().hex
tag = 'factory-egress-probe-' + suffix
worker_tag = 'factory-worker-probe-' + suffix
firewall_tag = 'factory-firewall-probe-' + suffix
agent_tag = 'factory-agent-uid-probe-' + suffix
allocation = None
successor_id = None
try:
    (directory/'Dockerfile').write_text('FROM python:3.11-slim\nRUN apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq iptables && rm -rf /var/lib/apt/lists/*\n')
    docker('build', '-t', firewall_tag, str(directory))
    firewall_image = docker('image', 'inspect', firewall_tag, '--format', '{{.Id}}')
    builder = docker('create', '--network', 'none', base, 'true')
    try:
        for name in ('egress_proxy.py', 'guarded_store.py', 'pinned_https.py', 'budget_authority.py', 'fake_proxy.py'):
            docker('cp', str(directory/name), builder+':/tmp/'+name)
        docker('commit', builder, tag)
        exporter = subprocess.Popen(['docker','export',builder], stdout=subprocess.PIPE)
        importer = subprocess.run(['docker','import','--change','ENV '+base_env[0],'-',worker_tag], stdin=exporter.stdout, capture_output=True, check=True)
        exporter.stdout.close()
        if exporter.wait() != 0: raise RuntimeError('export failed')
    finally:
        docker('rm', '-f', builder)
    image = docker('image', 'inspect', tag, '--format', '{{.Id}}')
    (directory/'Dockerfile').write_bytes((Path(ROOT)/'Dockerfile.agent').read_bytes())
    docker('build', '--network', 'none', '--build-arg', 'WORKER_UID=' + str(os.getuid()), '-t', agent_tag, str(directory))
    worker_image = docker('image', 'inspect', agent_tag, '--format', '{{.Id}}')
    worker_env = json.loads(docker('image', 'inspect', agent_tag, '--format', '{{json .Config.Env}}'))
    sync_root = Path(os.environ['FACTORY_PROBE_SYNC']).resolve() if os.environ.get('FACTORY_PROBE_REAL_CLI') else None
    if sync_root:
        (sync_root / 'workspace').mkdir()
    workspace_context = nullcontext(str(sync_root / 'workspace')) if sync_root else tempfile.TemporaryDirectory(prefix='factory-workspace-')
    with workspace_context as workspace:
        workspace = os.path.realpath(workspace)
        os.chmod(workspace, 0o700)
        subprocess.run(['git', 'init', '-q', workspace], check=True)
        subprocess.run(['git', '-C', workspace, '-c', 'user.name=Probe', '-c', 'user.email=probe@example.invalid',
                        'commit', '-q', '--allow-empty', '-m', 'baseline'], check=True)
        baseline = subprocess.run(['git', '-C', workspace, 'rev-parse', 'HEAD'], check=True,
                                  capture_output=True, text=True).stdout.strip()
        (directory / 'attempt-anchor').mkdir()
        db_path = sync_root / 'db' if sync_root else directory / 'attempt.db'
        anchor_path = sync_root / 'anchor' / 'file' if sync_root else directory / 'attempt-anchor' / 'watermark'
        store = GuardedAttemptStore.initialize(db_path, anchor_path)
        source_conversation = str(uuid.uuid5(uuid.NAMESPACE_URL, 'stapled-factory:probe'))
        restart_conversation = str(uuid.uuid5(uuid.NAMESPACE_URL, 'stapled-factory:probe:successor'))
        payload={'agent_settings':{'llm':{'usage_id':'probe','model':'anthropic/deepseek-v4.1-flash:1017','base_url':'http://127.0.0.1:8123','api_key':'dummy','max_output_tokens':128}},'workspace':{'working_dir':'/tmp'},'initial_message':{'role':'user','content':[{'type':'text','text':'Say hello.'}],'run':True},'max_iterations':3}
        request_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if not sync_root:
            store.reserve('probe', request_hash, source_conversation)
        config = dict(worker_image=worker_image, proxy_image=image, helper_image=base,
            worker_command=['tini','--','python','/opt/factory/agent_entrypoint.py'],
            proxy_command=['python3','/tmp/fake_proxy.py','--socket','/gateway/api.sock',
                '--model','deepseek-v4.1-flash:1017','--gateway-ip','100.92.236.42','--max-requests','5',
                '--budget-db','/budget/b.db','--budget-anchor','/anchor/b.json',
                '--initialize-budget-store','--attempt-id','probe','--attempt-fingerprint','a'*64,
                '--ceiling-microdollars','2000000','--worst-case-request-microdollars',('400000' if os.environ.get('FACTORY_PROBE_TOOL') else '500000'),
                '--pricing-policy-id','synthetic-test','--pricing-policy-expires-at',str(int(time.time())+3600)],
            workspace=workspace, uid=os.getuid(), worker_environment=worker_env,
            firewall={'helper_image':firewall_image, 'gateway_ip':'100.92.236.42'})
        if sync_root:
            from pricing_policy import POLICY_ID
            live_model = 'ollama-cloud/deepseek-v4.1-flash:1017'
            config['proxy_command'][config['proxy_command'].index('--model') + 1] = live_model
            if live:
                from pricing_policy import reservation_microdollars
                import socket
                gateway_ip = socket.gethostbyname('hermes-control.tail4296e6.ts.net')
                config['proxy_command'][1] = '/tmp/egress_proxy.py'
                config['proxy_command'][config['proxy_command'].index('--max-requests') + 1] = '6'
                config['proxy_command'][config['proxy_command'].index('--gateway-ip') + 1] = gateway_ip
                config['proxy_command'][config['proxy_command'].index('--worst-case-request-microdollars') + 1] = str(reservation_microdollars(live_model))
                config['firewall']['gateway_ip'] = gateway_ip
                payload['agent_settings']['llm']['max_output_tokens'] = 1024
                payload['workspace']['working_dir'] = '/workspace'
                payload['max_iterations'] = 6
                payload.pop('initial_message', None)
            payload['agent_settings']['llm']['model'] = 'anthropic/' + live_model
            config['proxy_command'][config['proxy_command'].index('--attempt-id') + 1] = 'unit:0'
            config['proxy_command'][config['proxy_command'].index('--pricing-policy-id') + 1] = POLICY_ID
            successor_workspace = str(sync_root / 'successor-workspace')
            subprocess.run(['git', 'clone', '--no-hardlinks', '-q', workspace, successor_workspace], check=True)
            os.chmod(successor_workspace, 0o700)
            if live:
                subprocess.run(['git', '-C', successor_workspace, 'config', 'user.name', 'Factory Probe'], check=True)
                subprocess.run(['git', '-C', successor_workspace, 'config', 'user.email', 'factory-probe@example.invalid'], check=True)
            task_file = sync_root / 'task'
            task_file.write_text('In /workspace, use one terminal command to create agent-effect.txt containing exactly agent effect followed by a newline, stage it, and commit it with git. Then finish. Use git -c safe.directory=/workspace -c user.name=Probe -c user.email=probe@example.invalid for git commands. Do not change anything else. Do not split creation, staging and commit across separate tool calls.')
            runtime = {'attempt_id':'unit:0', 'task_file':str(task_file), 'repo':workspace,
                'baseline':baseline, 'successor_workspace':successor_workspace,
                'payload':payload, 'provision':config}
            runtime_file = sync_root / 'runtime.json'
            runtime_file.write_text(json.dumps(runtime))
            runtime_file.chmod(0o600)
            (sync_root / 'configured').touch()
            for _ in range(360):
                if (sync_root / 'done').exists(): break
                time.sleep(1)
            else: raise RuntimeError('Restate probe timed out')
        else:
            allocation = provision_once(store, 'probe', request_hash, source_conversation, workspace_baseline=baseline,
                                        gateway_key=b'invalid-probe-key', **config)
            bound = inspect_bound_allocation(store, 'probe', request_hash, source_conversation,
                                             workspace_baseline=baseline, **config)
            if bound['allocation_id'] != allocation['allocation_id']:
                raise RuntimeError('reinspected allocation identity differs')
            try:
                provision_once(store, 'probe', request_hash, source_conversation, workspace_baseline=baseline,
                               gateway_key=b'invalid-probe-key', **config)
            except ProvisionError:
                print('bound allocation reinspection passed; repeat allocation rejected')
            else:
                raise RuntimeError('repeat allocation unexpectedly succeeded')
            worker=allocation['worker']
            def prepare_adapter(container, verifier=lambda *_: None):
                for _ in range(90):
                    result=subprocess.run(['docker','exec',container,'python','-c','import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/openapi.json",timeout=2)'],capture_output=True)
                    if result.returncode == 0: break
                    time.sleep(1)
                else: raise RuntimeError('server unavailable')
                full_id=docker('inspect','--format','{{.Id}}',container)
                return OpenHandsAdapter('http://127.0.0.1:8000','dummy',verifier, transport=DockerTransport(full_id,'dummy'))
            prepare_adapter(worker).create(source_conversation,payload)
            for _ in range(90):
                if subprocess.run(['docker','exec',allocation['proxy'],'test','-f','/tmp/first-inflight']).returncode == 0:
                    ledger=docker('exec',allocation['proxy'],'python3','-c',"import sqlite3; d=sqlite3.connect('/budget/b.db'); print(*d.execute('select used, spent from budgets join monetary_budgets using (attempt)').fetchone())")
                    if ledger==('2 800000' if os.environ.get('FACTORY_PROBE_TOOL') else '2 1000000'): break
                time.sleep(1)
            else: raise RuntimeError('old in-flight requests not observed')
            # Optional coordination used only by the disposable Restate integration probe.
            sync = os.environ.get('FACTORY_PROBE_SYNC')
            if sync:
                syncdir = Path(sync)
                (syncdir / 'inflight').write_text('reserved and model request in flight')
                for _ in range(120):
                    if (syncdir / 'restart').exists(): break
                    time.sleep(1)
                else: raise RuntimeError('Restate did not trigger restart')
            spec = {**config, 'workspace_baseline': baseline}
            digest = hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
            successor_workspace = os.path.realpath(directory / 'successor-workspace')
            subprocess.run(['git', 'clone', '--no-hardlinks', '-q', workspace, successor_workspace], check=True)
            os.chmod(successor_workspace, 0o700)
            adapter_box = {}
            def factory(container):
                if container not in adapter_box:
                    from revision import verify_revision
                    adapter_box[container] = prepare_adapter(container, lambda *_: verify_revision(successor_workspace, baseline))
                return adapter_box[container]
            import crash_fence, successor_runtime
            for module, name in ((crash_fence, 'fence_bound_worker'), (successor_runtime, 'provision_successor'), (successor_runtime, 'inspect_successor')):
                original = getattr(module, name)
                def diagnostic(*args, _original=original, _name=name, **kwargs):
                    try: return _original(*args, **kwargs)
                    except Exception as exc:
                        print('synthetic probe', _name, type(exc).__name__, str(exc), flush=True)
                        raise
                setattr(module, name, diagnostic)
            # This exercises the real fenced coordinator. Its prerequisites remain
            # test-only and do not authorize the production CLI.
            first = execute_bound_successor(store, 'probe', payload, config, factory,
                                            workspace=successor_workspace, baseline=baseline)
            assert first['status'] == 'unknown', first
            saved = store.lookup_restart_worker('probe', 'probe:successor')
            successor_id = saved['worker_id']
            successor = inspect_successor(store, 'probe', request_hash, source_conversation, 'probe:successor',
                                          configuration=config, baseline=baseline)
            assert successor['worker_id'] == successor_id
            assert docker('inspect', '--format', '{{.State.Running}}', allocation['worker']) == 'false'
            assert successor['proxy_id'] == bound['proxy_id']
            adapter = adapter_box[successor_id]
            for _ in range(90):
                ledger=docker('exec',allocation['proxy'],'python3','-c',"import sqlite3; d=sqlite3.connect('/budget/b.db'); print(*d.execute('select used, spent from budgets join monetary_budgets using (attempt)').fetchone())")
                if ledger==('5 2000000' if os.environ.get('FACTORY_PROBE_TOOL') else '4 2000000'): break
                time.sleep(1)
            else: raise RuntimeError('successor did not share ledger: '+ledger)
            for _ in range(45):
                info=adapter._request('GET','/api/conversations/'+restart_conversation)
                if info.get('execution_status') == 'finished': break
                time.sleep(1)
            else: raise RuntimeError('successor conversation did not finish')
            if os.environ.get('FACTORY_PROBE_TOOL'):
                recovered = execute_bound_successor(store, 'probe', payload, config, factory,
                        workspace=successor_workspace, baseline=baseline, reconcile_only=True)
                assert recovered['status'] == 'completed', recovered
                assert recovered['conversation_id'] == restart_conversation
                assert execute_bound_successor(store, 'probe', payload, config, factory,
                        workspace=successor_workspace, baseline=baseline, reconcile_only=True) == recovered
                print('coordinator reconciled the verified successor revision without redispatch')
            print('successor real Agent Server finished with shared synthetic reservation ledger')
            if sync:
                from revision import verify_revision
                revision = verify_revision(successor_workspace, baseline)
                if not revision:
                    status=subprocess.run(['git','-C',successor_workspace,'status','--short'],capture_output=True,text=True)
                    print('revision failure, status:', status.stdout, status.stderr, flush=True)
                    print('effect file:', (Path(successor_workspace)/'agent-effect.txt').exists(), flush=True)
                    raise RuntimeError('successor did not produce a clean changed commit')
                assert (Path(successor_workspace) / 'agent-effect.txt').read_text() == 'agent effect\n'
                (Path(sync) / 'revision').write_text(revision)
            duplicate_workspace = os.path.realpath(directory / 'duplicate-workspace')
            subprocess.run(['git', 'clone', '--no-hardlinks', '-q', workspace, duplicate_workspace], check=True)
            os.chmod(duplicate_workspace, 0o700)
            try:
                provision_successor(store, 'probe', request_hash, source_conversation,
                                    'probe:successor', request_hash, restart_conversation, configuration=config,
                                    workspace=duplicate_workspace, baseline=baseline)
            except Exception as exc:
                if 'already claimed' not in str(exc): raise
            else:
                raise RuntimeError('duplicate successor unexpectedly created')
            print('successor used original proxy and ledger; old worker stopped; repeat rejected; synthetic reservation total 2000000 microdollars')

finally:
    if os.environ.get('FACTORY_PROBE_REAL_CLI'):
        import sqlite3
        try:
            with sqlite3.connect(Path(os.environ['FACTORY_PROBE_SYNC']) / 'db') as db:
                row = db.execute('select allocation_id from allocations where attempt_id=?', ('unit:0',)).fetchone()
            if row:
                prefix = 'factory-' + row[0]
                for role in ('successor', 'worker', 'proxy'):
                    subprocess.run(['docker','rm','-f',prefix+'-'+role],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                subprocess.run(['docker','network','rm',prefix+'-network'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                for role in ('socket','budget','anchor'):
                    subprocess.run(['docker','volume','rm',prefix+'-'+role],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        except sqlite3.Error:
            pass
    if owned_allocation and not allocation:
        for role in ('budget','anchor'):
            subprocess.run(['docker','volume','rm','factory-'+owned_allocation+'-'+role],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    if successor_id:
        subprocess.run(['docker','rm','-f',successor_id],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    if allocation:
        # The deterministic name is unique to this probe's allocation and also
        # catches a failed create/start before a successor ID was returned.
        subprocess.run(['docker','rm','-f','factory-'+allocation['allocation_id']+'-successor'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        allocation['proxy_process'].terminate()
        for container in (allocation['worker'], allocation['proxy']):
            subprocess.run(['docker','rm','-f',container],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        subprocess.run(['docker','network','rm',allocation['network']],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for key in ('socket_volume','budget_volume','anchor_volume'):
            subprocess.run(['docker','volume','rm',allocation[key]],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    subprocess.run(['docker','image','rm',firewall_tag],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    subprocess.run(['docker','image','rm',agent_tag],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    subprocess.run(['docker','image','rm',worker_tag],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    subprocess.run(['docker','image','rm',tag],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    scratch.cleanup()
