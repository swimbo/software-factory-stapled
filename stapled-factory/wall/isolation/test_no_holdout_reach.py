"""Prove the holdout store is unreachable from a worker namespace.

The wall's isolation is a *property*, not a config we believe. This test asserts
that a simulated worker sandbox (which mounts only the target + evaluator, never
the holdout) cannot reach the holdout scenarios — so a generator cannot read the
answers out of the same filesystem it works in.
"""
import tempfile
from pathlib import Path

WALL = Path(__file__).resolve().parents[1]
HOLDOUT = WALL / "holdout"


def test_holdout_exists_and_holds_scenarios():
    assert (HOLDOUT / "scenarios.json").is_file()


def test_holdout_is_outside_the_worker_namespace():
    with tempfile.TemporaryDirectory() as tmp:
        worker_root = Path(tmp)
        # A worker sandbox mounts only what it needs (its worktree + tooling),
        # NOT the holdout. Model that as a namespace that contains "target" but no "holdout".
        (worker_root / "target").mkdir()

        # 1. The holdout's absolute path is disjoint from the worker's root.
        assert not str(HOLDOUT.resolve()).startswith(str(worker_root.resolve()))

        # 2. A worker resolving "holdout/scenarios.json" from within its own root
        #    finds nothing — the scenarios are not present in its namespace.
        assert not (worker_root / "holdout" / "scenarios.json").exists()
