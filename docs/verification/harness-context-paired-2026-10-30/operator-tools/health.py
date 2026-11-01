import pathlib,subprocess,json,urllib.request,urllib.error
b=pathlib.Path('/home/bo/.local/share/stapled-factory-control');rev='850259ae877e0cb171310e95d11719d9882e879e'
assert subprocess.check_output(['systemctl','--user','is-active','factory-endpoint','factory-ingress','factory-restate'],text=True).splitlines()==['active']*3
assert urllib.request.urlopen('http://127.0.0.1:19070/health',timeout=5).status==200
req=urllib.request.Request('http://127.0.0.1:19081/WorkUnit/nuc-pr34-runtime-20261028-01/getState',b'{}',headers={'Content-Type':'application/json'})
try:urllib.request.urlopen(req,timeout=5)
except urllib.error.HTTPError as e:assert e.code==401
else:raise AssertionError('unauthenticated ingress accepted')
js="const h=require('http2');const c=h.connect('http://127.0.0.1:19080');const r=c.request({':path':'/discover',':method':'GET'});r.on('response',x=>{console.log(x[':status'])});r.on('data',()=>{});r.on('end',()=>c.close());r.end();"
assert subprocess.check_output(['node','-e',js],text=True,timeout=10).strip()=='401'
lines=subprocess.check_output(['ss','-ltnH'],text=True).splitlines()
for port in (18080,19070,19080,19081):
 selected=[x for x in lines if x.split()[3].endswith(':'+str(port))];assert len(selected)==1 and selected[0].split()[3]=='127.0.0.1:'+str(port)
paths={}
for service in ('factory-endpoint','factory-ingress'):
 pid=subprocess.check_output(['systemctl','--user','show',service,'-p','MainPID','--value'],text=True).strip();path=pathlib.Path('/proc',pid,'cwd').resolve();assert path==b/'releases'/rev/'stapled-factory/spine';paths[service]=str(path)
p={'revision':rev,'services_healthy':True,'admin_health':200,'unauthenticated_ingress':401,'unsigned_endpoint_discovery':401,'all_factory_listeners_loopback':True,'process_working_directories':paths,'verification_scope':'context-v1 release health and authentication boundaries; paired trials tracked separately','shared_services_restarted':False}
(b/'rollouts'/rev/'health.json').write_text(json.dumps(p,indent=2)+'\n');print(json.dumps(p))
