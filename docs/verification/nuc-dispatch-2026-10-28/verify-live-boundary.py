import json,pathlib,subprocess,sys,tempfile
root=pathlib.Path('/home/bo/.local/share/stapled-factory-control');rev='6335e302fb873fe280529e6e512bf9bf7d5140b8';sys.path.insert(0,str(root/'releases'/rev/'stapled-factory/worker'))
from netns_firewall import verify_proxy_firewall
from provision_check import validate_resources
from probes.container_negatives import verify
proof=json.loads((root/'dispatch/nuc-dispatch-20261028-01/verified-dispatch.json').read_text());snap=proof['runtime_snapshot'];cfg=json.loads((root/'dispatch/nuc-dispatch-20261028-01/runtime.json').read_text())
for role in ('worker','proxy'):
 info=json.loads(subprocess.check_output(['docker','inspect',snap['containers'][role]['id']],text=True))[0];validate_resources(info,role)
 assert info['Image']==cfg['provision'][role+'_image']
 kernel=json.loads(subprocess.check_output(['docker','exec',info['Id'],'python3','-c',"import pathlib,json;print(json.dumps({k:pathlib.Path('/sys/fs/cgroup',k).read_text().strip() for k in ['cpu.max','memory.max','memory.swap.max','pids.max']}))"],text=True))
 proof.setdefault('actual_live_boundary',{})[role+'_cgroup']=kernel
with tempfile.TemporaryDirectory(prefix='factory-live-boundary-') as tmp:verify(snap['containers']['worker']['id'],pathlib.Path(tmp))
private_paths=[str(root/'attempt-store/attempts.db'),str(root/'attempt-anchor/watermark.json'),str(root/'dispatch/nuc-dispatch-20261028-01/runtime.json'),'/home/bo/.hermes/bifrost/virtual-key-claude-desktop-1.txt']
code="import json,os;paths=json.loads(os.environ['DENIED_PATHS']);\nfor p in paths:\n try:f=open(p,'rb')\n except OSError:continue\n else:f.close();raise AssertionError('operator private path reachable')\nprint('PASS actual operator store/config/credential paths unreachable from worker')"
print(subprocess.check_output(['docker','exec','-e','DENIED_PATHS='+json.dumps(private_paths),snap['containers']['worker']['id'],'python3','-c',code],text=True).strip())
network=snap['allocation_id'];binding=json.loads(subprocess.check_output(['docker','network','inspect','factory-'+network+'-network'],text=True))[0]
verify_proxy_firewall(snap['containers']['proxy']['id'],network,binding['Id'],cfg['provision']['firewall']['helper_image'],'100.92.236.42')
proof['actual_live_boundary'].update({'private_host_paths_denied':True,'worker_isolation_positive_and_negatives_passed':True,'proxy_firewall_reverified':True})
(root/'dispatch/nuc-dispatch-20261028-01/verified-dispatch.json').write_text(json.dumps(proof,indent=2)+'\n')
print('PASS actual bound live allocation isolation, images, cgroups and firewall')
