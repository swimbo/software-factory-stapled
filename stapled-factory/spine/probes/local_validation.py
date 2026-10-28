"""Real disposable Docker positive and negative controls; no paid calls."""
import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path

SPINE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('adapter', SPINE / 'local_validation.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
image = json.loads(adapter.command('docker', 'image', 'inspect', 'python:3.11-slim'))[0]['RepoDigests'][0]
with tempfile.TemporaryDirectory() as temp:
    repo = Path(temp)
    def git(*args):
        return adapter.command('git', '-C', temp, *args)
    git('init', '-q')
    git('config', 'user.name', 'Local verification')
    git('config', 'user.email', 'verification@invalid.local')
    (repo / 'README').write_text('baseline')
    git('add', '.')
    git('commit', '-qm', 'baseline')
    baseline = git('rev-parse', 'HEAD')
    results = {}
    for name, fixture in [('good', 'app.py'), ('broken', 'broken_app.py'), ('secret', 'app.py')]:
        text = (SPINE.parent / 'wall/target' / fixture).read_text().replace('("127.0.0.1", 8777)', '("0.0.0.0", 8000)').replace('("127.0.0.1", 8778)', '("0.0.0.0", 8000)')
        if name == 'secret':
            text += '\nAWS_ACCESS_KEY_ID = "AKIA' + 'ABCDEFGHIJKLMNOP"\n'
        (repo / 'server.py').write_text(text)
        git('add', '.')
        git('commit', '-qm', name)
        result = adapter.validate(repo, git('rev-parse', 'HEAD'), baseline, SPINE.parent / 'wall/holdout/scenarios.json', 'server.py', image)
        assert result['passed'] == (name == 'good'), result
        results[name] = dict(passed=result['passed'], artifact_digest=result['artifact_digest'], deployment_id=result['deployment_id'], security=result['security']['verdict'], wall=result['wall']['verdict']['passed'])
    print(json.dumps(results))
