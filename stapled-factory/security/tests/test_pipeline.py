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
