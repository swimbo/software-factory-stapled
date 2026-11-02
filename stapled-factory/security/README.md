# Security boundary — credential broker + destructive-op firewall

The two controls the adversarial debate said were missing, and that close the parked decisions
(#2 trust anchor, #3 effect-vs-intent). Both classifiers are **deterministic — no LLM in the loop.** They are standalone primitives; the current OpenHands Agent Server terminal path does not enforce them for every tool call. Do not grant that path privileged external credentials or production effects on the strength of these classifiers.

## `firewall.py` — destructive-op firewall (effect-based)

Classifies a worker's tool call by **what it does**, not what any model says it intends. Calls explicitly routed through `worker/guard.py` are checked *before* execution:

- `allow` — safe (e.g. `SELECT`, `DELETE ... WHERE`).
- `approve` — dangerous but legitimate (e.g. `DROP TABLE`, `DELETE` without `WHERE`, `rm -rf`, `chmod`),
  → routed to the spine's human `destructive_op` gate.
- `block` — deny-by-default (unknown op types).

## `broker.py` — credential broker (short-lived, task-scoped)

The agent never sees the long-lived **master key** (the root of trust). It holds only short-lived tokens:

- `issue(claim_id, repo, action, ttl)` → single-action, single-repo, task-scoped token, HMAC-signed.
- `verify(token, action)` → checks signature, expiry, and single-action scope.
- `verify_and_bind(token, action, claim_id, repo)` → re-binds the token to the originating claim — the
  anti-injection guard: a prompt-injected instruction can't repurpose a token mid-trajectory.

**Trust anchor (decision #2, default):** `FACTORY_MASTER_KEY` in the environment, outside the agent loop.
Swap for a secret store / KMS without changing the broker's API.

## How it wires into the spine

The standalone `worker/guard.py` wrapper calls `firewall.check(...)` and uses `broker.issue(...)`.
The current real OpenHands terminal path is not wired through this wrapper. A wrapper `firewall` verdict of `approve`
and a `broker` credential for a privileged action both route through the spine's human gates
(`gate.request(..., "destructive_op" | "credential_issue")`). Full tool mediation remains a separate production gate; connecting `build.ts` to the Agent Server does not establish that enforcement.

## `pipeline.py` — full deterministic security pipeline

The committed prerequisite for security-relevant code to run dark (tiered dark). Non-LLM; orchestrates
real engines and records which one ran (so a missing engine is never silently presented as full SAST):

| Check | Engine | Fallback (if absent) |
|---|---|---|
| SAST | **Semgrep** (`--config auto --json`) | regex SAST-lite |
| Secrets | **Gitleaks** (`dir --report-format json`) | regex secret patterns |
| Dependencies | **OSV-Scanner** (`scan <lockfile> --format json`) | manifest-change flag |
| Provenance | **Cosign** (`verify --certificate-identity ...`) | (unchecked) |

Install the engines to get full SAST (all run locally):

```bash
brew install semgrep gitleaks osv-scanner cosign    # or the equivalent for your distro
```

Verdict: `pass` (run dark) · `needs_review` (security-relevant / medium → human `security_review` gate) ·
`fail` (secrets / critical → block merge). CLI: `pipeline_cli.py` (reads a diff, prints JSON incl. `engines`).

## Verify (stdlib, no deps — runnable in-sandbox)

```bash
cd security && python3 -m pytest tests -q     # or run the assertions inline
```

## Material verification primitives

`npm_materials.py` and `cargo_materials.py` check a deliberately narrow set of
lockfile entries against archive bytes in an owner-only digest-addressed directory.
`cargo_vendor.py` compares one vendored crate's regular files and executable
bits with its locked archive; it reports Cargo-omitted Git metadata. These are
point-in-time local checks. They neither freeze inputs nor attest what a builder
consumed, publisher identity, a complete dependency inventory, or a release.
They are not connected to the runtime validation or publication gate.

The npm verifier can additionally check registry signatures against public keys
that the caller has independently pinned. It trusts the host's `openssl` and
reports key lifetime and revocation as unknown. Registry signatures bind the
package name, version and integrity; they do not prove publisher identity or
complete provenance. A frozen npm archive can retain this evidence; a reader
must supply independently pinned keys to verify it. Controlled npm preflight
and the isolated build library can verify and report the evidence digest when
given those keys. The operator binds a selected policy and evidence digest into
the signed build manifest, and the NUC material verifier rechecks both against
the frozen archive. New validation evidence binds the build manifest digest.
None of these checks establish key lifetime, publisher identity, Cargo or
base-image provenance, or complete coverage; the release gate is unchanged.

`oci_archive.py` verifies the descriptor graph and linux/amd64 image config of
a pinned OCI archive and records an archive digest. Its result is deliberately
incomplete: it does not establish publisher identity, build provenance, safe
execution, or prevent later replacement. It is not yet connected to receipts.
