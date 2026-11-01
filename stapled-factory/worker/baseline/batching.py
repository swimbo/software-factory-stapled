"""Operator-authored native terminal characterization, not model-facing tools.

Known solutions deliberately test execution capability, not autonomous reasoning.
Frozen prompts, seed and independent checker remain unchanged.
"""
import shlex


def python(code):
    return 'python3 -B -c ' + shlex.quote(code)


def commands(task, batched):
    reads = [python("from pathlib import Path; print(Path('catalog.py').read_text())"),
             python("from pathlib import Path; print(Path('app.py').read_text())")]
    edits = {
        'discovery': "import json; from pathlib import Path; Path('discovery.json').write_text(json.dumps({'version_source':'catalog.VERSION','entrypoint':'app.py'})+'\\n')",
        'small-edit': "from pathlib import Path; p=Path('catalog.py'); s=p.read_text(); assert 'return name.strip()' in s; p.write_text(s.replace('return name.strip()','return name.strip().lower()'))",
        'multi-file': "from pathlib import Path; p=Path('catalog.py'); s=p.read_text(); assert \"VERSION = '0.1.0'\" in s; p.write_text(s.replace(\"VERSION = '0.1.0'\",\"VERSION = '0.2.0'\")); p=Path('app.py'); s=p.read_text(); assert \"'0.0.0'\" in s; p.write_text(s.replace(\"'0.0.0'\",'VERSION'))",
        'failing-tests': "from pathlib import Path; p=Path('catalog.py'); s=p.read_text(); assert 'return sum(prices) + 1' in s; p.write_text(s.replace('return sum(prices) + 1','return sum(prices)'))",
        'noisy-output': "import json,subprocess,sys; from pathlib import Path; r=subprocess.run([sys.executable,'-B','noisy.py'],check=True,capture_output=True,text=True); hits=[int(x.split()[1]) for x in r.stdout.splitlines() if x.endswith(' target')]; assert len(hits)==1; Path('noisy-result.json').write_text(json.dumps({'target_index':hits[0]})+'\\n'); print('target records:',len(hits))",
        'stalled-run': "import json,subprocess,sys; from pathlib import Path; r=subprocess.run([sys.executable,'-B','-c','import factory_missing_dependency'],capture_output=True,text=True); assert r.returncode!=0 and 'ModuleNotFoundError' in r.stderr; Path('blocker.json').write_text(json.dumps({'status':'blocked','dependency':'factory_missing_dependency'})+'\\n'); print('one failed diagnostic; no installation')",
    }
    checks = {
        'discovery': "import json; assert json.load(open('discovery.json'))['entrypoint']=='app.py'",
        'small-edit': "import catalog; assert catalog.normalize(' A b ')=='a b'",
        'multi-file': "import catalog,app; assert catalog.VERSION=='0.2.0' and app.version()=={'version':'0.2.0'}",
        'failing-tests': "import subprocess,sys; subprocess.run([sys.executable,'-B','check_totals.py'],check=True)",
        'noisy-output': "import json; assert json.load(open('noisy-result.json'))['target_index']==6789",
        'stalled-run': "import json; assert json.load(open('blocker.json'))['status']=='blocked'",
    }
    initial = ['test -z "$(git status --porcelain)"', *reads]
    if task=='failing-tests':
        initial += [python("import subprocess,sys; r=subprocess.run([sys.executable,'-B','check_totals.py'],capture_output=True); assert r.returncode!=0; print('expected original test failure observed')")]
    final = [python(edits[task]),python(checks[task]),'git diff --check',
             'git add --all && git commit -q -m "Native execution characterization"',
             'test -z "$(git status --porcelain)"']
    # Independent reads share a call; dependent mutations/checks/commit stay ordered.
    return [' &&\n'.join(initial), ' &&\n'.join(final)] if batched else initial+final
