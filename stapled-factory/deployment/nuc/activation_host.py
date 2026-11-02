"""NUC read-only inspector and scoped systemd adapter for activate_runtime.

No inspection or service action occurs at import. The Restate SQL schema and
inventory completeness must be verified against the installed server before
use; unknown schemas, ongoing invocations and missing evidence fail closed.
The trusted host, Docker daemon, operator account and local admin API are in
scope of the trust boundary. Never publish captured WorkUnit data or tokens.
"""
import hashlib
import fcntl
import sqlite3
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
WORKER = HERE.parents[1] / 'worker'
SERVICES = ('factory-endpoint', 'factory-ingress')
UNIT = re.compile(r'[A-Za-z0-9_.:-]{1,160}\Z')
CONTAINER = re.compile(r'[a-f0-9]{64}\Z')
MAX_RESPONSE = 32 * 1024 * 1024
MAX_ROWS = 100000
CLIENT = r'''import json,sys,urllib.request
r=json.load(sys.stdin)
req=urllib.request.Request('http://127.0.0.1:8000/api/conversations/'+r['id'],
 headers={'X-Session-API-Key':'dummy'})
with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=10) as response:
 data=response.read(1048577)
 if len(data)>1048576: raise ValueError('oversized response')
 print(data.decode())
'''


def _private(path):
    fd=os.open(path,os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as f:
        st=os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or st.st_mode & 0o077 or st.st_size>65536:
            raise ValueError('owner-only credential required')
        return f.read()


def _module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    if spec is None or spec.loader is None:raise ValueError('required release module unavailable')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def _request(url,body=None,token=None,expect_unauthorized=False):
    headers={'Accept':'application/json'}
    if body is not None:headers['Content-Type']='application/json'
    if token is not None:headers['Authorization']='Bearer '+token
    req=urllib.request.Request(url,None if body is None else json.dumps(body).encode(),headers=headers)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=20) as response:
            raw=response.read(MAX_RESPONSE+1)
    except urllib.error.HTTPError as exc:
        if expect_unauthorized and exc.code==401:return None
        raise
    if expect_unauthorized:raise ValueError('unauthenticated ingress was not rejected')
    if len(raw)>MAX_RESPONSE:raise ValueError('oversized control response')
    return json.loads(raw)


def _normalize(value):
    if isinstance(value,bytes):return {'bytes_hex':value.hex()}
    if value is None or isinstance(value,(str,int,float)):return value
    raise ValueError('unsupported guarded store value')


def _canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()


class NucHost:
    """Read-only evidence adapter for the known loopback NUC control plane.

    Instantiate only from a manifest-verified release; `worker` must name that
    release's worker directory. `docker_sha256` pins the local Docker binary.
    """
    def __init__(self,control,worker,*,docker='/usr/bin/docker',docker_sha256):
        self.control=Path(control).absolute();self.worker=Path(worker).absolute()
        self.docker=Path(docker).resolve(strict=True);self.docker_sha256=docker_sha256
        if not re.fullmatch(r'[a-f0-9]{64}',docker_sha256) or not self.docker.is_absolute():
            raise ValueError('pinned Docker executable required')
        self._check_docker()

    def _check_docker(self):
        fd=os.open(self.docker,os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as f:
            st=os.fstat(f.fileno())
            if (not stat.S_ISREG(st.st_mode) or st.st_uid not in (0,os.getuid()) or st.st_mode & 0o022 or
                    not st.st_mode & 0o111 or hashlib.file_digest(f,'sha256').hexdigest()!=self.docker_sha256):
                raise ValueError('Docker executable differs')

    def _command(self,*args,input_data=None):
        runtime='/run/user/'+str(os.getuid())
        return subprocess.run(list(map(str,args)),input=input_data,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            check=True,timeout=30,env={'PATH':'/usr/bin:/bin','HOME':str(Path.home()),
                'XDG_RUNTIME_DIR':runtime,'DBUS_SESSION_BUS_ADDRESS':'unix:path='+runtime+'/bus',
                'DOCKER_HOST':'unix:///var/run/docker.sock'}).stdout

    def _query(self,query):
        result=_request('http://127.0.0.1:19070/query',{'query':query})
        rows=result.get('rows') if isinstance(result,dict) else None
        if not isinstance(rows,list) or len(rows)>=MAX_ROWS or any(not isinstance(r,dict) for r in rows):
            raise ValueError('complete Restate SQL result required')
        return rows

    def _assert_idle_invocations(self):
        # Unknown/missing columns and SQL errors are hard failures. The exact
        # installed Restate schema must be independently verified before use.
        rows=self._query("SELECT COUNT(*) AS n FROM sys_invocation WHERE status <> 'completed' OR status IS NULL")
        if len(rows)!=1 or type(rows[0].get('n')) is not int or rows[0]['n']!=0:
            raise ValueError('active or unresolved Restate invocation')

    def _workunits(self):
        counted=self._query("SELECT COUNT(*) AS n FROM state WHERE service_name = 'WorkUnit' AND key = 'state'")
        if (len(counted)!=1 or type(counted[0].get('n')) is not int or not 0 < counted[0]['n'] < MAX_ROWS):
            raise ValueError('authoritative WorkUnit count unavailable')
        rows=self._query("SELECT service_key, value_utf8 FROM state WHERE service_name = 'WorkUnit' AND key = 'state'")
        if len(rows)!=counted[0]['n']:raise ValueError('truncated WorkUnit state inventory')
        values={}
        for row in rows:
            unit=row.get('service_key');raw=row.get('value_utf8')
            if not isinstance(unit,str) or not UNIT.fullmatch(unit) or unit in values or not isinstance(raw,str):
                raise ValueError('invalid WorkUnit state inventory')
            state=json.loads(raw)
            if not isinstance(state,dict):raise ValueError('invalid WorkUnit state')
            values[unit]=state
        if not values:raise ValueError('no existing WorkUnit for authenticated health check')
        return values

    def _authenticated_sentinel(self, values):
        # One stable selected WorkUnit verifies ingress authentication per capture.
        unit=min(values);state=values[unit]
        token=_private(self.control/'ingress.token').decode().strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{43,}',token):raise ValueError('invalid ingress credential')
        url='http://127.0.0.1:19081/WorkUnit/'+unit+'/getState'
        _request(url,{},expect_unauthorized=True)
        if _request(url,{},token=token)!=state:raise ValueError('authenticated WorkUnit differs from SQL state')

    def _services(self):
        result={}
        for name in SERVICES:
            fields=self._command('/usr/bin/systemctl','--user','show',name,'-p','MainPID','-p','ActiveState').decode().splitlines()
            data=dict(line.split('=',1) for line in fields if '=' in line)
            pid=int(data.get('MainPID','0'))
            if data.get('ActiveState')!='active' or pid<=0:raise ValueError('factory service unavailable')
            cwd=Path('/proc',str(pid),'cwd').resolve(strict=True)
            rel=cwd.parent.parent
            if cwd!=rel/'stapled-factory/spine' or rel.parent!=self.control/'releases' or not re.fullmatch(r'[a-f0-9]{40}',rel.name):
                raise ValueError('service source release unavailable')
            result[name]={'active':True,'pid':pid,'release':rel.name}
            if name=='factory-endpoint':
                environment=Path('/proc',str(pid),'environ').read_bytes().split(b'\0')
                bindings=[item[len(b'FACTORY_RUNTIME_CONFIG='):] for item in environment
                          if item.startswith(b'FACTORY_RUNTIME_CONFIG=')]
                if len(bindings)!=1 or not bindings[0]:
                    raise ValueError('running endpoint runtime configuration unavailable')
                result[name]['runtime_config_sha256']=hashlib.sha256(bindings[0]).hexdigest()
        return result

    def _store_and_namespaces(self):
        db=self.control/'attempt-store/attempts.db';anchor=self.control/'attempt-anchor/watermark.json'
        lock=anchor.with_name(anchor.name+'.lock')
        if not db.is_file() or not anchor.is_file() or not lock.is_file() or lock.is_symlink():
            raise ValueError('guarded store or existing lock unavailable')
        ids=[]
        for role in ('worker','proxy'):
            rows=self._command(self.docker,'ps','-q','--no-trunc','--filter','label=factory.role='+role).decode().splitlines()
            if any(not CONTAINER.fullmatch(x) for x in rows):raise ValueError('invalid running namespace ID')
            ids.extend(rows)
        if len(ids)!=len(set(ids)):raise ValueError('duplicate running namespace')
        containers=[] if not ids else json.loads(self._command(self.docker,'inspect',*ids))
        if not isinstance(containers,list) or len(containers)!=len(ids):raise ValueError('namespace inspection incomplete')
        fd=os.open(lock,os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd,'r') as guard:
            info=os.fstat(guard.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode & 0o077:
                raise ValueError('unsafe guarded lock')
            fcntl.flock(guard,fcntl.LOCK_EX | fcntl.LOCK_NB)
            anchored=json.loads(_private(anchor))
            conn=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True,timeout=10,isolation_level=None)
            try:
                conn.execute('BEGIN')
                return self._read_guarded(conn,anchored,anchor,ids,containers)
            finally:
                conn.close()

    def _read_guarded(self,conn,anchored,anchor,ids,containers):
        meta=conn.execute('SELECT store_id,generation FROM metadata').fetchall()
        if len(meta)!=1 or anchored!={'store_id':meta[0][0],'generation':meta[0][1]}:
            raise ValueError('guarded identity or watermark differs')
        tables={}
        for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',name):raise ValueError('unsafe ledger table')
            rows=conn.execute('SELECT * FROM "'+name+'"').fetchall()
            if len(rows)>MAX_ROWS:raise ValueError('guarded table too large')
            tables[name]=sorted(_canonical([_normalize(v) for v in row]).decode() for row in rows)
        namespaces=[]
        for item in containers:
            labels=(item.get('Config') or {}).get('Labels') or {};role=labels.get('factory.role');allocation=labels.get('factory.allocation')
            cid=item.get('Id');state=item.get('State') or {}
            if cid not in ids or role not in ('worker','proxy') or not isinstance(allocation,str):
                raise ValueError('running namespace identity differs')
            row=conn.execute('SELECT a.status,a.conversation_id FROM attempts a JOIN allocations l ON l.attempt_id=a.attempt_id WHERE l.allocation_id=?',(allocation,)).fetchone()
            if not row or row[0]!='completed':raise ValueError('running namespace is not completed')
            sdk=None
            if role=='worker':
                if not isinstance(row[1],str) or not re.fullmatch(r'[0-9a-f-]{36}',row[1]):raise ValueError('invalid conversation identity')
                raw=self._command(self.docker,'exec','-i',cid,'python3','-c',CLIENT,input_data=json.dumps({'id':row[1]}).encode())
                result=json.loads(raw);sdk=result.get('execution_status')
                if result.get('id')!=row[1] or sdk!='finished':raise ValueError('running worker is unfinished')
            namespaces.append({'id':cid,'role':role,'allocation_id':allocation,'allocation_status':row[0],
                'sdk_status':sdk,'running':state.get('Running'),'image':item.get('Image'),'started_at':state.get('StartedAt')})
        if len(meta)!=1:raise ValueError('guarded identity unavailable')
        guarded={'store_id':meta[0][0],'generation':meta[0][1],
            'anchor_sha256':hashlib.sha256(anchor.read_bytes()).hexdigest(),
            'tables_sha256':hashlib.sha256(_canonical(tables)).hexdigest()}
        return guarded,sorted(namespaces,key=lambda x:x['id'])

    def capture(self):
        if os.path.lexists(self.control/'operator-active.json'):raise ValueError('operator active')
        self._check_docker();self._assert_idle_invocations()
        health=_request('http://127.0.0.1:19070/health')
        if not isinstance(health,dict) or health.get('admin_status')!='Ready':raise ValueError('Restate admin unavailable')
        workunits=self._workunits();self._authenticated_sentinel(workunits)
        guarded,namespaces=self._store_and_namespaces();services=self._services()
        if self._workunits()!=workunits:raise ValueError('WorkUnit inventory changed during capture')
        self._assert_idle_invocations();self._check_docker()
        if os.path.lexists(self.control/'operator-active.json'):raise ValueError('operator became active')
        return {'authenticated':True,'inventory_complete':True,'operator_active':False,'workunits':workunits,
                'namespaces':namespaces,'guarded_store':guarded,'services':services}

    def admission_fence(self):
        # No independently enforced fence for direct loopback Restate ingress
        # has been provisioned or verified. Never stop ingress as a substitute.
        raise RuntimeError('no verified admission fence available')

    def assert_restart_quiescent(self,names,journal):
        if tuple(names)!=SERVICES:raise ValueError('unexpected service set')
        # A missing or partial completion receipt may mean that the original
        # client timed out or died after submission; job absence is insufficient.
        try:
            requested=json.loads(_private(Path(journal)/'restart-requested.json'))
            completed=json.loads(_private(Path(journal)/'restart-completed.json'))
        except (OSError,ValueError) as exc:raise RuntimeError('original restart completion unavailable') from exc
        if (requested!={'services':list(SERVICES)} or completed!={'services':list(SERVICES),'returned':True} or
                type(completed.get('returned')) is not bool):
            raise RuntimeError('original restart completion differs')
        def read_jobs():
            result={}
            for name in SERVICES:
                raw=self._command('/usr/bin/systemctl','--user','show',name,'-p','ActiveState','-p','SubState','-p','Job').decode()
                pairs=[line.split('=',1) for line in raw.splitlines() if '=' in line]
                value=dict(pairs)
                if len(pairs)!=3 or set(value)!={'ActiveState','SubState','Job'}:
                    raise RuntimeError('restart job state unavailable')
                result[name]=value
            return result
        first=read_jobs()
        if (any(value!={'ActiveState':'active','SubState':'running','Job':''} for value in first.values()) or
                read_jobs()!=first):
            raise RuntimeError('restart jobs are not quiescent')

    def restart_services(self,names):
        if tuple(names)!=SERVICES:raise ValueError('unexpected service set')
        self._command('/usr/bin/systemctl','--user','daemon-reload')
        self._command('/usr/bin/systemctl','--user','restart',*SERVICES)
