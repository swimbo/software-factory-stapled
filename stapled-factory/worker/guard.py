"""Guarded execution — the firewall + broker wrapped around the worker's tool calls.

This boundary applies only to callers that explicitly invoke it. The current
OpenHands Agent Server executes terminal and file tools outside this wrapper.
Do not grant it privileged credentials or production effects based on this
classifier. Arbitrary shell/Python execution requires an external capability
boundary; a command-name classifier cannot mediate all resulting effects.

- firewall.check(op) classifies by EFFECT:
    "allow"   → execute
    "approve" → route to the human `destructive_op` gate (do NOT execute here)
    "block"   → refuse
- broker.issue / verify_and_bind for any credential.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "security"))

from broker import CredentialBroker  # noqa: E402
from firewall import check  # noqa: E402


def guarded_exec(op: dict, execute_fn=None) -> dict:
    """Run one worker operation through the firewall.

    Returns a result dict with `disposition` and, for "approve", the `gate` the
    spine should route to (`gate.request(..., "destructive_op")`).
    """
    verdict = check(op)

    if verdict.disposition == "block":
        return {"allowed": False, "disposition": "block", "reason": verdict.reason}

    if verdict.disposition == "approve":
        return {
            "allowed": False,
            "disposition": "approve",
            "gate": "destructive_op",
            "reason": verdict.reason,
        }

    # "allow" → execute (or record the intent when no executor is provided)
    if execute_fn is not None:
        return {"allowed": True, "disposition": "allow", "result": execute_fn(op)}
    return {"allowed": True, "disposition": "allow"}


def request_credential(
    broker: CredentialBroker,
    claim_id: str,
    repo: str,
    action: str,
    ttl_seconds: int = 300,
) -> dict:
    """Mint a short-lived, single-action, task-scoped token for a claim.

    The agent only ever sees this token, never the broker's master key.
    """
    tok = broker.issue(claim_id, repo, action, ttl_seconds)
    return {"token": tok.token, "scope": tok.scope, "expires_at": tok.expires_at}
