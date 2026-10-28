"""Tests for the firewall and credential broker."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from broker import CredentialBroker  # noqa: E402
from firewall import check  # noqa: E402


# --- firewall (effect-based) ---

def test_firewall_approves_destructive_sql():
    assert check({"type": "sql", "statement": "DROP TABLE users"}).disposition == "approve"
    assert check({"type": "sql", "statement": "DELETE FROM users"}).disposition == "approve"  # no WHERE
    assert check({"type": "sql", "statement": "GRANT ALL ON *.* TO x"}).disposition == "approve"


def test_firewall_allows_safe_sql():
    assert check({"type": "sql", "statement": "SELECT * FROM users"}).disposition == "allow"
    assert check({"type": "sql", "statement": "DELETE FROM users WHERE id=1"}).disposition == "allow"


def test_firewall_approves_destructive_shell():
    assert check({"type": "shell", "statement": "rm -rf /"}).disposition == "approve"
    assert check({"type": "shell", "statement": "chmod 777 /etc/passwd"}).disposition == "approve"


def test_firewall_blocks_unknown_types():
    assert check({"type": "exotic", "statement": "anything"}).disposition == "block"


# --- credential broker ---

def test_broker_issues_and_verifies_scoped_token():
    b = CredentialBroker(b"master-secret")
    tok = b.issue("claim-1", "repo-a", "read")
    ok, reason = b.verify(tok.token, "read")
    assert ok, reason


def test_broker_rejects_wrong_action():
    b = CredentialBroker(b"master-secret")
    tok = b.issue("claim-1", "repo-a", "read")
    ok, _ = b.verify(tok.token, "write")
    assert not ok


def test_broker_rejects_forged_token():
    b = CredentialBroker(b"master-secret")
    ok, _ = b.verify("forged.payload", "read")
    assert not ok


def test_broker_rejects_expired_token():
    b = CredentialBroker(b"master-secret")
    tok = b.issue("claim-1", "repo-a", "read", ttl_seconds=-1)  # already expired
    ok, reason = b.verify(tok.token, "read")
    assert not ok and reason == "expired"


def test_broker_binds_token_to_claim_and_repo():
    b = CredentialBroker(b"master-secret")
    tok = b.issue("claim-1", "repo-a", "read")
    ok, _ = b.verify_and_bind(tok.token, "read", "claim-1", "repo-a")
    assert ok
    ok2, _ = b.verify_and_bind(tok.token, "read", "claim-2", "repo-a")  # wrong claim
    assert not ok2
