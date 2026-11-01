"""Tests for the deterministic security pipeline."""
import sys
import shutil
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline import scan  # noqa: E402


@pytest.mark.skipif(not shutil.which('semgrep'), reason='semgrep is not installed')
def test_semgrep_ignores_candidate_suppression(tmp_path):
    import pipeline
    target = tmp_path/'candidate'; target.mkdir()
    (target/'.semgrepignore').write_text('*\n')
    (target/'app.py').write_text('dangerous_call()  # nosemgrep\n')
    rules = tmp_path/'rules.yml'
    rules.write_text('rules:\n- id: fixture-rule\n  languages: [python]\n  message: fixture\n  severity: ERROR\n  pattern: dangerous_call()\n')
    findings = pipeline.run_semgrep(str(target), str(rules))
    assert findings is not None and any(f.rule.endswith('fixture-rule') for f in findings)


@pytest.mark.skipif(not shutil.which('gitleaks'), reason='gitleaks is not installed')
def test_gitleaks_ignores_candidate_suppression(tmp_path):
    import pipeline
    target = tmp_path/'candidate'; target.mkdir()
    (target/'.gitleaks.toml').write_text('[allowlist]\npaths = [".*"]\n')
    (target/'.gitleaksignore').write_text('*\n')
    (target/'app.txt').write_text('aws_access_key_id = "' + 'AKIA' + 'ABCDEFGHIJKLMNOP' + '" # gitleaks:allow\n')
    findings = pipeline.run_gitleaks(str(target))
    assert findings is not None and any('aws' in f.rule.lower() for f in findings)


def test_secret_in_diff_fails():
    r = scan("aws key: AKIAABCDEFGHIJKLMNOP")  # gitleaks:allow -- deliberate scanner fixture
    assert r.verdict == "fail"
    assert any(f.category == "secret" and f.severity == "critical" for f in r.findings)


def test_private_key_fails():
    r = scan("-----BEGIN RSA PRIVATE KEY-----\n...")
    assert r.verdict == "fail"


def test_sql_concat_flags_high():
    r = scan("q = \"SELECT * FROM t WHERE id='\" + user_input + \"'\"")
    assert r.verdict == "fail"  # high severity


def test_eval_fails():
    r = scan("eval(user_input)")
    assert r.verdict == "fail"


def test_clean_diff_passes():
    r = scan("def add(a, b):\n    return a + b\n")
    assert r.verdict == "pass"
    assert not r.security_relevant


def test_security_relevant_path_routes_to_review():
    r = scan("def login():\n    pass\n", paths=["src/auth/login.py"])
    assert r.security_relevant is True
    assert r.verdict == "needs_review"


def test_dependency_change_is_info_only():
    r = scan("package.json: added left-pad@1.0.0")
    assert r.verdict == "pass"  # info finding doesn't gate
    assert any(f.category == "dependency" for f in r.findings)


def test_xss_is_medium_and_routes_review():
    r = scan("el.innerHTML = user_input")
    assert r.verdict == "needs_review"  # medium severity, not security-relevant path


def test_diff_checked_when_external_scanners_report_clean(monkeypatch, tmp_path):
    import pipeline
    monkeypatch.setattr(pipeline, "run_semgrep", lambda target: [])
    monkeypatch.setattr(pipeline, "run_gitleaks", lambda target: [])
    report = pipeline.scan("aws key: AKIAABCDEFGHIJKLMNOP", target=str(tmp_path))  # gitleaks:allow -- deliberate scanner fixture
    assert report.verdict == "fail"
    assert any(f.category == "secret" and f.severity == "critical" for f in report.findings)


def test_osv_failure_or_invalid_output_cannot_pass(monkeypatch):
    import pipeline
    import subprocess
    import pytest
    monkeypatch.setattr(pipeline, '_which', lambda tool: '/fixture/osv-scanner' if tool == 'osv-scanner' else None)
    for code, output in [(127, '{}'), (128, '{}'), (129, '{}'), (0, ''), (0, 'invalid'), (0, '[]')]:
        monkeypatch.setattr(pipeline.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a[0], code, output, ''))
        with pytest.raises(ValueError, match='OSV'):
            pipeline.scan('safe change', lockfile='package-lock.json')


def test_osv_findings_and_clean_report_remain_distinct(monkeypatch, tmp_path):
    import pipeline
    import subprocess
    monkeypatch.setattr(pipeline, '_which', lambda tool: '/fixture/osv-scanner' if tool == 'osv-scanner' else None)
    lock = tmp_path/'package-lock.json';lock.write_text('{"lockfileVersion":3,"packages":{"":{}}}')
    reports = [(0, '{"results": []}'), (1, '{"results":[{"packages":[{"package":{"name":"p"},"vulnerabilities":[{"id":"TEST-1"}]}]}]}')]
    for code, output in reports:
        def run(args, **kwargs):
            assert args[:6] == ['osv-scanner', 'scan', '-L', str(lock), '--format', 'json']
            assert args[6] == '--config' and Path(args[7]).read_text() == '# operator-controlled empty configuration\n'
            assert '--all-packages' in args
            return subprocess.CompletedProcess(args, code, output, '')
        monkeypatch.setattr(pipeline.subprocess, 'run', run)
        report = pipeline.scan('safe change', lockfile=str(lock))
        assert report.verdict == ('pass' if code == 0 else 'needs_review')
        assert report.engines['dependency'] == 'osv-scanner-incomplete'


def test_osv_cannot_silently_omit_a_locked_package(monkeypatch, tmp_path):
    import json
    import pipeline
    import subprocess
    lock = tmp_path/'package-lock.json'
    lock.write_text(json.dumps({'lockfileVersion': 3, 'packages': {
        '': {}, 'node_modules/example': {'version': '1.2.3', 'resolved': 'https://registry.example/example.tgz'}}}))
    monkeypatch.setattr(pipeline, '_which', lambda tool: '/fixture/osv-scanner')
    monkeypatch.setattr(pipeline.subprocess, 'run', lambda args, **kw: subprocess.CompletedProcess(args, 0, '{"results":[]}', ''))
    with pytest.raises(ValueError, match='omitted locked packages'):
        pipeline.run_osv_scanner(str(lock))
    report = json.dumps({'results': [{'packages': [{'package': {'name': 'example', 'version': '1.2.3'}}]}]})
    monkeypatch.setattr(pipeline.subprocess, 'run', lambda args, **kw: subprocess.CompletedProcess(args, 0, report, ''))
    found = pipeline.run_osv_scanner(str(lock))
    assert found == []
    assert found.inventory['package_count'] == 1
    assert len(found.inventory['sha256']) == 64
    import hashlib
    assert found.inventory['packages'] == [['', 'example', '1.2.3', hashlib.sha256(b'').hexdigest()]]
    scan = pipeline.scan('safe', lockfiles=[str(lock)])
    assert scan.dependency_inventories[0]['source_sha256'] == hashlib.sha256(lock.read_bytes()).hexdigest()
    assert scan.dependency_inventories[0]['sha256'] == found.inventory['sha256']


def test_osv_cannot_silently_omit_registry_crates(monkeypatch, tmp_path):
    import pipeline
    import subprocess
    lock = tmp_path/'Cargo.lock'
    lock.write_text('[[package]]\nname="fixture"\nversion="1.2.3"\nsource="registry+https://example.invalid/index"\n')
    monkeypatch.setattr(pipeline, '_which', lambda tool: '/fixture/osv-scanner')
    monkeypatch.setattr(pipeline.subprocess, 'run', lambda args, **kw: subprocess.CompletedProcess(args, 0, '{"results":[]}', ''))
    with pytest.raises(ValueError, match='omitted locked packages'):
        pipeline.run_osv_scanner(str(lock))


def test_osv_disappearing_after_discovery_cannot_pass(monkeypatch):
    import pipeline
    import pytest
    monkeypatch.setattr(pipeline, '_which', lambda tool: '/fixture/osv-scanner' if tool == 'osv-scanner' else None)
    monkeypatch.setattr(pipeline, 'run_osv_scanner', lambda path: None)
    with pytest.raises(ValueError, match='OSV'):
        pipeline.scan('safe change', lockfile='package-lock.json')


def test_multiple_lockfiles_are_all_scanned_and_partial_coverage_is_labelled(monkeypatch):
    import pipeline
    seen = []
    monkeypatch.setattr(pipeline, '_which', lambda tool: 'fixture' if tool == 'osv-scanner' else None)
    monkeypatch.setattr(pipeline, 'run_osv_scanner', lambda path: seen.append(path) or [])
    report = pipeline.scan('safe', lockfiles=['a/Cargo.lock', 'b/package-lock.json'])
    assert seen == ['a/Cargo.lock', 'b/package-lock.json']
    assert report.engines['dependency'] == 'osv-scanner-incomplete'


def test_prebuilt_image_findings_contribute_to_security_verdict(monkeypatch):
    import pipeline
    monkeypatch.setattr(pipeline, '_which', lambda tool: 'fixture' if tool == 'osv-scanner' else None)
    monkeypatch.setattr(pipeline, 'run_osv_scanner', lambda path: [])
    monkeypatch.setattr(pipeline, 'run_osv_image', lambda path: [pipeline.Finding(
        'medium', 'dependency', 'TEST-IMAGE', 'fixture-package', 'known vulnerability')])
    report = pipeline.scan('safe', lockfiles=['package-lock.json'], image_archive='image.tar')
    assert report.engines['image_dependency'] == 'osv-scanner-image-incomplete'
    assert report.engines['dependency'] == 'osv-scanner-incomplete'
    assert report.verdict == 'needs_review'
    assert any(f.rule == 'TEST-IMAGE' for f in report.findings)


def test_dependency_inventory_reconciliation_reports_unmatched_components():
    import pipeline
    inventories = [
        {'kind': 'lockfile', 'packages': [['npm', 'present', '1', 'a'], ['npm', 'not-installed', '2', 'b']]},
        {'kind': 'image', 'packages': [['npm', 'present', '1', 'c'], ['npm', 'image-only', '3', 'd']]},
    ]
    result = pipeline.reconcile_inventories(inventories)
    assert result['coverage'] == 'incomplete'
    assert 'origins are not compared' in result['comparison_scope']
    assert result['lock_only'] == [['npm', 'not-installed', '2']]
    assert result['image_only'] == [['npm', 'image-only', '3']]
