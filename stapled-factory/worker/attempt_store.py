"""Durable, fail-closed record of a potentially dispatched worker attempt."""
import sqlite3
from pathlib import Path


class AttemptStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS attempts (attempt_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, conversation_id TEXT NOT NULL UNIQUE, status TEXT NOT NULL, result TEXT)")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.execute("PRAGMA synchronous=FULL")
        return db

    def reserve(self, attempt_id: str, request_hash: str, conversation_id: str) -> tuple[str, str | None]:
        """A newly reserved attempt may dispatch once; existing attempts never redispatch."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT request_hash, conversation_id, status, result FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if row:
                if row[:2] != (request_hash, conversation_id):
                    db.execute("ROLLBACK")
                    raise ValueError("attempt identity does not match original request")
                db.execute("COMMIT")
                return ("completed", row[3]) if row[2] == "completed" else ("unknown", None)
            db.execute("INSERT INTO attempts VALUES (?, ?, ?, 'reserved', NULL)", (attempt_id, request_hash, conversation_id))
            db.execute("COMMIT")
            return "new", None

    def complete(self, attempt_id: str, result: str) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status, result FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if not row or (row[0] == "completed" and row[1] != result):
                db.execute("ROLLBACK")
                raise ValueError("missing attempt or conflicting result")
            db.execute("UPDATE attempts SET status='completed', result=? WHERE attempt_id=?", (result, attempt_id))
            db.execute("COMMIT")
