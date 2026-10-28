"""Destructive-op firewall — deterministic, effect-based, no LLM in the loop.

This is the Security Architect's hard stance #3: gate on the *effect* of an
operation (what it actually does), not on any model's classification of intent.
Every worker tool call is checked here BEFORE it runs. Dangerous primitives are
routed to the human `destructive_op` gate; unknown types are denied by default.

Dispositions:
  "allow"   — safe, execute now.
  "approve" — dangerous but legitimate → route to the human destructive_op gate.
  "block"   — deny by default (unknown / unsupported op type).
"""
from dataclasses import dataclass


@dataclass
class OpVerdict:
    disposition: str  # "allow" | "approve" | "block"
    reason: str = ""


# Effect-based danger signatures (deterministic string/verb matching, not a model).
_DESTRUCTIVE_SQL_VERBS = {"DROP", "TRUNCATE", "ALTER", "GRANT", "REVOKE", "RENAME"}

_DESTRUCTIVE_SHELL = [
    "rm -rf", "rm -fr", "rm -r ", "rm -f ",
    "chmod", "chown", "mkfs", "dd ", "mount", "umount",
    "shutdown", "reboot", "kill -9", "sudo rm", "mv /", "dd if=/dev/zero",
]

_DESTRUCTIVE_CONFIG = [
    "drop database", "delete zone", "rotate root key", "revoke all",
    "disable auth", "delete user", "truncate table", "drop table",
]


def check_sql(statement: str) -> OpVerdict:
    sql = statement.strip().upper()
    words = sql.split()
    first = words[0] if words else ""
    if first in _DESTRUCTIVE_SQL_VERBS:
        return OpVerdict("approve", f"destructive SQL verb '{first}'")
    if first == "DELETE" and "WHERE" not in sql:
        return OpVerdict("approve", "DELETE without WHERE (would affect all rows)")
    if first == "UPDATE" and "WHERE" not in sql:
        return OpVerdict("approve", "UPDATE without WHERE (would affect all rows)")
    if first == "":
        return OpVerdict("block", "empty statement")
    return OpVerdict("allow")


def check_shell(command: str) -> OpVerdict:
    lower = command.lower()
    for pat in _DESTRUCTIVE_SHELL:
        if pat in lower:
            return OpVerdict("approve", f"destructive shell pattern '{pat.strip()}'")
    return OpVerdict("allow")


def check_config(statement: str) -> OpVerdict:
    lower = statement.lower()
    for pat in _DESTRUCTIVE_CONFIG:
        if pat in lower:
            return OpVerdict("approve", f"destructive config change '{pat}'")
    return OpVerdict("allow")


def check(op: dict) -> OpVerdict:
    """Classify one worker operation by effect.

    op: {"type": "sql" | "shell" | "config" | ..., "statement": "<concrete op>"}
    """
    t = op.get("type", "")
    s = op.get("statement", "")
    if t == "sql":
        return check_sql(s)
    if t == "shell":
        return check_shell(s)
    if t == "config":
        return check_config(s)
    # deny-by-default for anything the firewall doesn't explicitly understand
    return OpVerdict("block", f"unknown op type '{t}'")
