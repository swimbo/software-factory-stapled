#!/usr/bin/env python3
"""Prepare one operator-owned sandbox attempt. Never activates a service or worker."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

WORKER = Path(__file__).resolve().parents[2] / 'worker'
sys.path.insert(0, str(WORKER))
from guarded_store import GuardedAttemptStore, StoreQuarantined
from image_approval import verify_approval
from pricing_policy import policy_id, reservation_microdollars

MODEL = 'auto'


def private(path):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError('absolute non-symlink private file required')
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError('operator-only file required')
    return path.read_bytes()


def directory(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError('canonical absolute directory required')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError('operator-only directory required')
    return path


def write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(data)


def git(*args):
    return subprocess.run(['git', *map(str, args)], check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.strip()


def prepare(*, root, unit, manifest, release, approval, signature, trust_root,
            attempt_db, attempt_anchor, task_text):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', unit):
        raise ValueError('invalid unit identifier')
    release = Path(release)
    if not release.is_absolute() or release.resolve() != release or not (release/'stapled-factory/worker/worker.py').is_file():
        raise ValueError('canonical frozen release required')
    identities = json.loads(private(manifest))
    expected = {'worker_image', 'proxy_image', 'helper_image', 'firewall_image', 'worker_environment'}
    if set(identities) != expected:
        raise ValueError('invalid image identity manifest')
    for role in expected - {'worker_environment'}:
        if not isinstance(identities[role], str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', identities[role]):
            raise ValueError('immutable image identity required')
    environment = identities['worker_environment']
    if not isinstance(environment, list) or any(not isinstance(x, str) or '=' not in x or
            any(k in x.partition('=')[0].upper() for k in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'CREDENTIAL')) for x in environment):
        raise ValueError('credential-free image environment required')
    if not task_text or len(task_text.encode()) > 1024*1024:
        raise ValueError('bounded task required')
    provision = {k: identities[k] for k in ('worker_image','proxy_image','helper_image')}
    provision['firewall'] = {'helper_image': identities['firewall_image'], 'gateway_ip': '100.92.236.42'}
    # This approval is mandatory even if a service environment omitted its key.
    verify_approval(private(approval), private(signature), private(trust_root), provision)
    root = directory(root)
    db, anchor = Path(attempt_db), Path(attempt_anchor)
    directory(db.parent); directory(anchor.parent)
    if db.exists() != anchor.exists() or db.is_symlink() or anchor.is_symlink():
        raise StoreQuarantined('partial or substituted store; quarantine required')
    if db.exists():
        private(db); private(anchor)
        store = GuardedAttemptStore(db, anchor)
        with store._transaction():
            pass
    else:
        GuardedAttemptStore.initialize(db, anchor)
    task_root = root / unit
    task_root.mkdir(mode=0o700)  # refuse overwriting even a partial preparation
    workspace, successor = task_root/'workspace', task_root/'successor'
    workspace.mkdir(mode=0o700)
    git('init','-q',workspace)
    git('-C',workspace,'config','user.name','Factory Operator')
    git('-C',workspace,'config','user.email','factory@example.invalid')
    git('-C',workspace,'commit','-q','--allow-empty','-m','Operator-approved empty baseline')
    baseline = git('-C',workspace,'rev-parse','HEAD')
    git('clone','-q','--no-hardlinks',workspace,successor)
    successor.chmod(0o700)
    task = task_root/'task.txt'; write(task, task_text)
    attempt = unit + ':0'
    fingerprint = hashlib.sha256((unit+'\n'+baseline+'\n'+task_text).encode()).hexdigest()
    provision.update({'workspace':str(workspace),'uid':os.geteuid(), 'worker_environment':environment,
        'worker_command':['tini','--','python','/opt/factory/agent_entrypoint.py'],
        'proxy_command':['python3','/opt/factory/egress_proxy.py','--socket','/gateway/api.sock',
            '--model',MODEL,'--gateway-ip','100.92.236.42','--max-requests','6',
            '--budget-db','/budget/b.db','--budget-anchor','/anchor/b.json','--initialize-budget-store',
            '--attempt-id',attempt,'--attempt-fingerprint',fingerprint,'--ceiling-microdollars','2000000',
            '--worst-case-request-microdollars',str(reservation_microdollars(MODEL)),
            '--pricing-policy-id',policy_id(MODEL),'--pricing-policy-expires-at',str(int(time.time())+3600)]})
    config = {'deployment_scope':'sandbox','attempt_id':attempt,'task_file':str(task),'repo':str(workspace),
        'baseline':baseline,'successor_workspace':str(successor), 'provision':provision,
        'image_approval':{'document':str(approval),'signature':str(signature)},
        'payload':{'agent_settings':{'llm':{'usage_id':attempt,'model':'anthropic/'+MODEL,
            'base_url':'http://127.0.0.1:8123','api_key':'dummy','max_output_tokens':1024}},
            'workspace':{'working_dir':'/workspace'},'max_iterations':6}}
    config_path = task_root/'runtime.json'; write(config_path,json.dumps(config,indent=2)+'\n')
    invocation = {'status':'prepared_not_activated','unit':unit,'environment':{
        'FACTORY_RUNTIME_CONFIG':str(config_path),'FACTORY_IMAGE_APPROVAL_KEY':str(trust_root)},
        'argv':[sys.executable,str(release/'stapled-factory/worker/worker.py'),'--task-file',str(task),
            '--repo',str(workspace),'--attempt-id',attempt,'--attempt-db',str(db),'--attempt-anchor',str(anchor)],
        'activation_requires':['single-operator deployment attestation','upstream provenance and dependency audit acceptance','operator service binding'],
        'budget':'best effort reservations; actual charges not capped'}
    write(task_root/'invocation.json',json.dumps(invocation,indent=2)+'\n')
    return invocation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root','unit','manifest','release','approval','signature','trust-root','attempt-db','attempt-anchor','task-file'):
        parser.add_argument('--'+name,required=True)
    args = vars(parser.parse_args())
    args['task_text'] = private(args.pop('task_file')).decode()
    print(json.dumps(prepare(**args)))


if __name__ == '__main__':
    main()
