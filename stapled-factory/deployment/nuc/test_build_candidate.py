"""The local image build must carry the identity the validator checks."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def test_build_labels_match_exact_committed_archive(tmp_path):
    repo = tmp_path / 'repo';repo.mkdir()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()
    git('init', '-q');git('config', 'user.email', 'fixture@example.invalid');git('config', 'user.name', 'Fixture')
    (repo / 'app').write_text('baseline\n');git('add', 'app');git('commit', '-qm', 'base')
    baseline = git('rev-parse', 'HEAD')
    (repo / 'app').write_text('candidate\n');git('commit', '-qam', 'candidate')
    revision = git('rev-parse', 'HEAD')
    bindir = tmp_path / 'bin';bindir.mkdir();capture = tmp_path / 'docker-args.json'
    docker = bindir / 'docker'
    docker.write_text('''#!/usr/bin/env python3
import io,json,os,sys,tarfile
if sys.argv[1] == 'build':
 open(os.environ['TEST_DOCKER_ARGS'],'w').write(json.dumps(sys.argv[2:]))
elif sys.argv[1] == 'save':
 with tarfile.open(sys.argv[3],'w') as tar:
  for name,data in [('manifest.json',b'[{"Config":"config.json"}]'),('config.json',b'{}')]:
   info=tarfile.TarInfo(name);info.size=len(data);tar.addfile(info,io.BytesIO(data))
else: raise SystemExit(1)
''')
    docker.chmod(0o700)
    key = tmp_path / 'fixture-key.pem'
    subprocess.run(['openssl', 'genpkey', '-algorithm', 'ed25519', '-out', str(key)], check=True)
    dockerfile = tmp_path / 'Dockerfile';dockerfile.write_text('FROM scratch\n')
    out = tmp_path / 'output'
    env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ['PATH'], TEST_DOCKER_ARGS=str(capture))
    script = Path(__file__).with_name('build_candidate.py')
    subprocess.run([sys.executable, str(script), '--repo', str(repo), '--revision', revision,
        '--baseline', baseline, '--dockerfile', str(dockerfile), '--tag', 'fixture:local',
        '--output', str(out), '--signing-key', str(key)], env=env, check=True, capture_output=True)
    manifest = json.loads((out / 'build.json').read_text())
    digest = hashlib.sha256((out / 'source.tar').read_bytes()).hexdigest()
    args = json.loads(capture.read_text())
    labels = [args[i+1] for i, value in enumerate(args[:-1]) if value == '--label']
    assert 'factory.git-revision=' + revision in labels
    assert 'factory.source-archive-sha256=' + digest in labels
    assert manifest['revision'] == revision and manifest['archive_sha256'] == digest
