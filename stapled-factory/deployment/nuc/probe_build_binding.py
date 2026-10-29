"""Actual NUC source/signature/artifact negative controls; no model calls."""
import argparse,json,subprocess,sys,tempfile
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--project',type=Path,required=True);p.add_argument('--root',type=Path,required=True);a=p.parse_args()
sys.path.insert(0,str(a.project/'spine'))
from prebuilt_validation import validate_prebuilt
root=a.root;case=root/'good';att=case/'attested';m=json.loads((case/'metadata.json').read_text())
image=subprocess.check_output(['docker','image','inspect',m['tag'],'--format','{{.Id}}'],text=True).strip()
args=[att/'source.tar',m['revision'],image,a.project/'wall/holdout/scenarios.json',att/'diff',root/'repo',m['baseline'],att/'build.json',att/'build.sig',root.parent/'operator-build-public.pem']
with tempfile.TemporaryDirectory() as tmp:
 tmp=Path(tmp);archive=tmp/'source.tar';archive.write_bytes(b'fake archive');diff=tmp/'diff';diff.write_text('fake diff');signature=tmp/'build.sig';signature.write_bytes(b'forged')
 broken=json.loads((root/'broken/metadata.json').read_text());other=subprocess.check_output(['docker','image','inspect',broken['tag'],'--format','{{.Id}}'],text=True).strip()
 for name,index,value in [('archive',0,archive),('diff',4,diff),('signature',8,signature),('artifact',2,other)]:
  values=list(args);values[index]=value
  try:validate_prebuilt(*values)
  except (ValueError,subprocess.CalledProcessError):print(name+' substitution rejected')
  else:raise AssertionError(name+' accepted')
