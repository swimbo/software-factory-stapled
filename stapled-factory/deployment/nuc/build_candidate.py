"""Local operator build+attestation, never run the signing key inside the build."""
import argparse,hashlib,io,json,subprocess,tarfile,tempfile
from pathlib import Path
p=argparse.ArgumentParser()
for n in ('repo','revision','baseline','dockerfile','tag','output','signing-key'):p.add_argument('--'+n,required=True)
a=p.parse_args();out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
def git(*args):return subprocess.check_output(['git','-C',a.repo,*args])
assert git('rev-parse',a.revision+'^{commit}').decode().strip()==a.revision
subprocess.run(['git','-C',a.repo,'merge-base','--is-ancestor',a.baseline,a.revision],check=True)
archive=git('archive',a.revision);diff=git('diff',a.baseline,a.revision);dockerfile=Path(a.dockerfile).read_bytes()
with tempfile.TemporaryDirectory(prefix='attested-build-') as temp:
 context=Path(temp)
 with tarfile.open(fileobj=io.BytesIO(archive)) as tar:tar.extractall(context,filter='data')
 (context/'Dockerfile').write_bytes(dockerfile)
 # The signing key stays outside this directory, outside Docker's build context.
 if Path(a.signing_key).resolve().is_relative_to(context):raise ValueError('key in build context')
 subprocess.run(['docker','build','--platform','linux/amd64','--quiet','-t',a.tag,temp],check=True)
 subprocess.run(['docker','save','-o',str(out/'image.tar'),a.tag],check=True)
 with tarfile.open(out/'image.tar') as tar:
  record=json.load(tar.extractfile('manifest.json'))
  config=tar.extractfile(record[0]['Config']).read()
 binding=dict(revision=a.revision,baseline=a.baseline,archive_sha256=hashlib.sha256(archive).hexdigest(),diff_sha256=hashlib.sha256(diff).hexdigest(),dockerfile_sha256=hashlib.sha256(dockerfile).hexdigest(),config_digest='sha256:'+hashlib.sha256(config).hexdigest())
 (out/'source.tar').write_bytes(archive);(out/'diff').write_bytes(diff);(out/'Dockerfile').write_bytes(dockerfile)
 (out/'build.json').write_text(json.dumps(binding,sort_keys=True,separators=(',',':')))
 subprocess.run(['openssl','pkeyutl','-sign','-inkey',a.signing_key,'-rawin','-in',str(out/'build.json'),'-out',str(out/'build.sig')],check=True)
 print(json.dumps(binding))
