# End-to-end verification plan

- Correct and broken targets: wall accepts the former, rejects the latter, emits one-line feedback and a receipt.
- Real isolated worker: probe a seeded holdout canary, host secrets and forbidden network destinations from both tool and subprocess; confirm denial and positive controls.
- Worker integration: dispatch a disposable task, confirm the authenticated server produces a revision and actual changed files; verify policy and usage evidence.
- Crash after remote acceptance: recover the same attempt or stop on ambiguity without duplicate dispatch.
- Budget: deny missing/exhausted authorization and verify any claimed runtime ceiling at its enforcing boundary.
- Approval: reject and approve paths record identity and prevent promotion until approved.
- Later dashboard: list, detail, approval, empty/error and keyboard paths.

A mocked server or empty directory does not establish actual isolation or production readiness. Tests requiring a real worker remain open until independently verified.
