# Work package — unit-002

**Parent epic:** Auth foundation (quarterly OKR KR1: "working session boundary")

**Spec:** The auth module must expose a session boundary with login/logout semantics.
A user with valid credentials reaches a session; wrong credentials are rejected
(401); logout invalidates the session so subsequent access is denied; unauthenticated
access to a protected resource is denied; the health endpoint reflects a running service.

**Acceptance criteria:** see the wall's holdout scenarios (`wall/holdout/scenarios.json`).
The generator never reads those; this spec is the intent, the wall is the judge.

**Blast radius:** single module, no destructive ops, no privileged credentials.