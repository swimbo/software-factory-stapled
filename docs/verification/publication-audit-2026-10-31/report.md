# Phase 9d publication audit — October 31, 2026

The workspace was reconciled with the reviewed Phase 9d release history before publication. All 420 implementation/evidence files in the original dirty-file inventory matched existing commits exactly; the remaining four differences were documentation. The 24 existing logical implementation, correction, test and evidence commits are preserved without rewriting their immutable release/CI identities.

Two stale README links were reconciled to the current upstream local-CI document. Eight unchanged older-base paths were hydrated from already-committed upstream content. The obsolete upstream-deleted `.github/README.md` was preserved privately and excluded so it cannot shadow the repository homepage. None of these older snapshots was republished as a new change.

The remaining documentation is organized into separate commits: current plan status and evidence checklist; missing dated execution history; this publication audit. All changed paths belong to the Phase 9d worker, deployment controls, planning or verification scope. No private configuration, local database, runtime directory, dependency tree, Python bytecode or unrelated branch is included. Original staging and documentation preimages were retained in a private audit snapshot before reconciliation.

The entire 24-commit unpublished range passed a redacted history secret scan. A whole-directory scan also encountered a synthetic AWS-pattern test sentinel in ignored Python bytecode; no bytecode is tracked or published. The tracked key artifacts are public verification keys, not signing private keys. Final exact-revision local CI, its secret scan and final publication-tree/history scans gate the push.

Publication uses a new `codex/phase9d-publication` branch. It does not merge into main, change live services, promote a harness profile, rerun paid trials or rewrite prior commits. The Phase 9d.5 retain-baseline decision and its telemetry/grading limitations remain in force. Earlier reports retain their dated local-only statements as historical receipts; this audit supersedes those statements only for Git availability.
