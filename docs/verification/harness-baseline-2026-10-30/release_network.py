import json,pathlib,re,sqlite3,subprocess,sys
r=pathlib.Path('/home/bo/.local/share/stapled-factory-control');unit=sys.argv[1]
assert re.fullmatch(r'h9d-20261030-[A-Za-z0-9_-]+',unit)
trial=r/'dispatch'/unit
with sqlite3.connect('file:'+str(r/'attempt-store/attempts.db')+'?mode=ro',uri=True) as db:
 row=db.execute('SELECT status FROM attempts WHERE attempt_id=?',(unit+':0',)).fetchone();assert row and row[0] in ('completed','abandoned')
 allocation=db.execute('SELECT allocation_id FROM allocations WHERE attempt_id=?',(unit+':0',)).fetchone()[0]
 binding=json.loads(db.execute('SELECT binding FROM allocation_bindings WHERE attempt_id=?',(unit+':0',)).fetchone()[0])
for role in ('worker','proxy'):
 info=json.loads(subprocess.check_output(['docker','inspect',binding[role+'_id']],text=True))[0]
 assert info['Config']['Labels'].get('factory.allocation')==allocation and info['Config']['Labels'].get('factory.role')==role
 assert info['State']['Running'] is False
net='factory-'+allocation+'-network'
p=subprocess.run(['docker','network','inspect',net],text=True,capture_output=True)
if p.returncode==0:
 info=json.loads(p.stdout)[0];assert info['Labels'].get('factory.allocation')==allocation and not info.get('Containers')
 subprocess.run(['docker','network','rm',info['Id']],check=True,stdout=subprocess.DEVNULL)
for name,expected in binding['volumes'].items():
 info=json.loads(subprocess.check_output(['docker','volume','inspect',name],text=True))[0]
 assert all(info.get(k)==expected.get(k) for k in ('Name','CreatedAt','Driver','Labels'))
proof={'unit':unit,'allocation_id':allocation,'network_released':True,'containers_and_workspaces_retained':True,'all_bound_volumes_retained':True,'shared_networks_touched':False}
p=trial/'baseline.network-released.json'
if not p.exists():p.write_text(json.dumps(proof,indent=2)+'\n');p.chmod(0o600)
print(json.dumps(proof))
