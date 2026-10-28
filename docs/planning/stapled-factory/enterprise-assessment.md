# Enterprise assessment — October 27, 2026

**D, 50.7/100, BLOCKED for enterprise deployment.** Default twelve-category
rubric, default weights (sum13), security and IAM must-pass. This supersedes the
pre-NUC44.0 assessment, retained in Git history. Scores are conservative evidence
judgments, not a compliance certification. The A/B implementation gate still
fails; no threshold or policy override is asserted.

Scope: trusted NUC host/Docker administrator and one operator, with unprivileged
workspace transformations and disposable candidate validation. Kernel isolation
constrains arbitrary terminal/Python/file code. Privileged external effects,
unattended multi-unit scheduling, multiuser authorization and automatic activation
of old backups are not approved by these checks. Generic production worker scope
remains denied; the persistent control service lacks a per-attempt runtime config.

## Category scores

| Category | Score | Weight | Evidence and remaining gap |
|---|---:|---:|---|
| Security |64|1.5| Actual NUC worker/proxy deny controls, gateway TLS positive control, signed candidate/build bindings. Upstream provenance and image/Python vulnerability audit incomplete. |
| IAM |65|1.5| Signed Restate endpoint, owner-only bearer/key files, narrow ingress with actual401/404 negatives. Trusted single operator; no multiuser access review. |
| Data governance |25|1.25| Encrypted/signed off-host backup and isolated stores verified. Retention, deletion and data inventory unverified. |
| Compliance |25|1.25| Durable attempt/approval evidence and change logs. No verified applicability mapping or organizational control evidence. |
| Operations |30|1| Dedicated enabled user services, health and scoped restart checks. No SLO, capacity envelope or on-call/escalation policy. |
| Incident response/BCP |45|1| Live in-flight recovery and exact joined off-host cold restore verified. No declared RTO/RPO, automatic old-generation activation or independent rollback witness. |
| Infrastructure |65|1| Local-only builds, exact image-transfer identities, frozen NUC release and source publication. Generic production parity and vendor provenance incomplete. |
| Code quality |50|.75| Strict TypeScript build, modular adapters and independent technical review. No enforced formatting/lint gate; large probe/coordinator modules remain. |
| Testing |80|1| Python/Node suites, actual NUC crash/duplicate/isolation/egress/candidate and restore controls. Coverage/mutation acceptance unmeasured. |
| Documentation |80|.75| Reproducible NUC deployment/recovery/operator instructions and exact evidence boundaries. Broader production operations incomplete. |
| Third-party risk |30|1| Locked npm audit and pinned image content identities. No verified upstream signatures, complete SBOM/license/image audit or vendor fallback policy. |
| Change management |55|1| Published baseline, local CI receipts/status, required main check/admin enforcement, frozen release and independent agent technical review. Independent human approval and formal release/version policy remain incomplete; main now requires one approving review of the latest push. |

Weighted sum658.5 /13 =50.65, rounded50.7. Unknown organizational controls receive
no credit; the restricted runtime scope does not confer enterprise approval.

## Five High findings

| ID | Current disposition | Verified evidence / remaining boundary |
|---|---|---|
| ENT-01 | RESOLVED for trusted operator/disposable candidate scope | Exact Git archive/diff recomputed; operator-signed local build binds revision/baseline/archive/diff/Dockerfile/image config; NUC image content checked. Good wall/security passes; broken wall and secret source reject. Private signed Restate acceptance binds selected attempt/revision and serializes validation; substitution/tamper/stale/replay and public-route controls reject. This is candidate evaluation, not continuing production-service health. |
| ENT-02 | RESOLVED for unprivileged workspace scope | Actual arbitrary-code container negatives deny host fixtures/stores/socket, direct TCP/DNS and root-directory writes; workspace writes pass. Kernel boundaries apply to every tool process; no regex/per-command mediation claim. Container root filesystem is not globally read-only. Privileged external effects remain denied. |
| ENT-03 | RESOLVED for reviewed bounded single-operator deployment; OPEN High for broader enterprise supply chain | Official exact-source/index publication, Python index→amd64 config, locally built derived-image provenance and exact NUC transfer bindings verified. Actual Trivy image/dependency/license audit includes extracted OpenHands frozen metadata, retains all findings and documents runtime-specific acceptance. No established credential-boundary or container-escape blocker found in this scope. Vulnerabilities and incomplete frozen/native/Rust SBOM coverage remain; portable publisher signatures and exhaustive dependency attestation are not claimed. See runtime-audit-2026-10-28.md and signed operator release evidence. |
| ENT-04 | RESOLVED for tested single-operator cold restore | Exact joined Restate data, guarded attempt/anchor, shared budget/anchor and both workspaces restored from121 encrypted/authenticated off-host files. Actual restored WorkUnit successor revision equals guarded result and GitHEAD; ledger unchanged. Older backup activation requires manual quarantine/fencing; no automatic anti-rollback or whole-host disaster recovery claim. |
| ENT-05 | RESOLVED for authorized single-operator deployment; OPEN High for enterprise separation of duties | User explicitly authorized single-operator NUC release. Verified feature commit may deploy with signed exact-source/CI/image/audit/task attestation and bounded private service binding. This is human deployment authorization, not independent GitHub approval. Main still requires independent latest-push review and local CI including admins; no review is forged, protection removed or merge bypassed. |

Two enterprise High findings remain open. ENT-01/02/04 dispositions are expressly
limited to the tested trusted-operator scope; expanding privileges or deployment
scope requires reassessment.

## Other tracked findings

- ENT-06 fixed: triple wall runs are the default; real regression observes arguments.
- Evaluator redirect gap fixed: candidate redirects cannot reach another host
  service, and environment proxies are ignored. A real two-server negative
  control verifies rejection.
- ENT-07 accepted scope: user dropped the hard$2 actual-charge ceiling. Reservations
  remain best-effort. Five live NUC requests reserved$1.579010; actual charges
  were not measured. No non-overshooting billing guarantee is asserted.
- ENT-08 open Medium: one operator-approved attempt configuration per deployment;
  no multi-tenant/unattended scheduling claim.

## Source and evidence

The baseline and follow-ups through `f5f18a4` were pushed only after isolated-clone
local CI. Receipts record source/tree, commands, exits and log hashes. GitHub
runners and provider-side builds are excluded by user policy. Final verification
and release evidence are recorded in progress.md and docs/verification/.

See `nuc-target.md`, `nuc-worker-verification.md`, worker/RUNTIME_IMAGES.md,
spine/VALIDATION_BINDING.md and deployment/nuc/BACKUP.md. The user corrected the
target from Railway to NUC; disposable Railway sandboxes were removed and the
previously created persistent private service/volume preserved. No shared NUC
services were restarted or pruned. No later implementation phase is marked done.
