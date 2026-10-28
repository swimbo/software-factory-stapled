"""Credential broker — short-lived, task-scoped, single-action tokens.

The agent never sees the long-lived master key (the root of trust). It holds
only short-lived broker-issued tokens, and the broker re-verifies the requested
action against the originating claim before honoring anything. This is the
"dedicated agent secret set" + "intent-scoped tool authorization" from the
architecture and the prompt-injection mitigation stance.

Trust anchor (parked decision #2): the master key lives in the environment
(`FACTORY_MASTER_KEY`), outside the agent loop. It can be swapped for a secret
store / KMS without changing the broker's API.
"""
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass


@dataclass
class TaskToken:
    token: str
    scope: dict
    expires_at: float


class CredentialBroker:
    def __init__(self, master_key: bytes):
        if not master_key:
            raise ValueError("master key must not be empty")
        self._master_key = master_key

    @classmethod
    def from_env(cls) -> "CredentialBroker":
        key = os.environ.get("FACTORY_MASTER_KEY")
        if not key:
            raise RuntimeError("FACTORY_MASTER_KEY is not set (the root of trust)")
        return cls(key.encode())

    def _sign(self, payload: str) -> str:
        return hmac.new(self._master_key, payload.encode(), hashlib.sha256).hexdigest()

    def issue(
        self,
        claim_id: str,
        repo: str,
        action: str,
        ttl_seconds: int = 300,
    ) -> TaskToken:
        """Mint a single-action, single-repo, task-scoped token bound to a claim."""
        scope = {
            "claim_id": claim_id,
            "repo": repo,
            "action": action,
            "nonce": os.urandom(8).hex(),
            "exp": int(time.time()) + ttl_seconds,
        }
        body = json.dumps(scope, sort_keys=True)
        token = f"{body}.{self._sign(body)}"
        return TaskToken(token=token, scope=scope, expires_at=scope["exp"])

    def verify(self, token: str, action: str) -> tuple[bool, str]:
        """Check signature, expiry, and single-action scope. Returns (ok, reason)."""
        try:
            body, sig = token.rsplit(".", 1)
        except ValueError:
            return False, "malformed token"
        if not hmac.compare_digest(self._sign(body), sig):
            return False, "bad signature"
        try:
            scope = json.loads(body)
        except json.JSONDecodeError:
            return False, "bad payload"
        if int(time.time()) >= scope["exp"]:
            return False, "expired"
        if scope.get("action") != action:
            return False, "out of scope (single-action)"
        return True, ""

    def verify_and_bind(self, token: str, action: str, claim_id: str, repo: str) -> tuple[bool, str]:
        """Verify the token AND re-bind it to the originating claim + repo.

        This is the anti-injection guard: the broker independently checks that
        the requested action matches the claim the token was minted for, so a
        prompt-injected instruction cannot repurpose a token mid-trajectory.
        """
        ok, reason = self.verify(token, action)
        if not ok:
            return False, reason
        body = token.rsplit(".", 1)[0]
        scope = json.loads(body)
        if scope.get("claim_id") != claim_id:
            return False, "claim mismatch"
        if scope.get("repo") != repo:
            return False, "repo mismatch"
        return True, ""
