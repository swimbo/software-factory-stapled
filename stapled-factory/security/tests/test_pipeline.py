"""Tests for the deterministic security pipeline."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline import scan  # noqa: E402


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


def test_osv_findings_and_clean_report_remain_distinct(monkeypatch):
    import pipeline
    import subprocess
    monkeypatch.setattr(pipeline, '_which', lambda tool: '/fixture/osv-scanner' if tool == 'osv-scanner' else None)
    reports = [(0, '{"results": []}'), (1, '{"results":[{"packages":[{"package":{"name":"p"},"vulnerabilities":[{"id":"TEST-1"}]}]}]}')]
    for code, output in reports:
        def run(args, **kwargs):
            assert args[:6] == ['osv-scanner', 'scan', '-L', 'package-lock.json', '--format', 'json']
            assert args[6] == '--config' and Path(args[7]).read_text() == '# operator-controlled empty configuration\n'
            return subprocess.CompletedProcess(args, code, output, '')
        monkeypatch.setattr(pipeline.subprocess, 'run', run)
        report = pipeline.scan('safe change', lockfile='package-lock.json')
        assert report.verdict == ('pass' if code == 0 else 'needs_review')
        assert report.engines['dependency'] == 'osv-scanner'


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
