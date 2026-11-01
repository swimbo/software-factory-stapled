# Software factory delivery completion

Status: active, November 1, 2026. This plan implements the full user-authorized delivery/build objective; completion is not established by local checks alone. Execution evidence and delegation updates go in progress.md. Existing architecture, user stories, data map, UX/UI and tech guide remain requirements; the final gap audit must reconcile every applicable requirement.

## Environment and authority

Canonical checkout: /Users/bo/code/software-factory-stapled. Initial branch codex/phase9d-qualification, HEAD 8b3c41c82d9d9c6b65bfb1d5d90bc6c7a139191b, clean index/worktree, no commits outside fetched origin/main. PR16 previously merged as cdc476a. Native execution selected: no enabled checkout engine configuration or external execution assignment. No compound packs configured. Two independent read-only audits dispatched before implementation. Current remote runtime must be rechecked before operating it.

## Scope

IN: reusable exact-revision validation of actual factory output using existing isolated disposable-target wall/security primitives; signed receipt acceptance; complete acceptance/rejection/crash/approval E2E coverage; one small real fullstack feature from factory build to independently reviewed PR; Inception goal cascade and issue qualification; control-plane dashboard and required audit persistence; full plan-gap audit and fixes.

OUT: harness profile promotion, SDK fork, cloud migration, replacing OpenHands/Restate/Bifrost, changing shared gateway routing, production cutover, deleting existing ledgers/containers/volumes or rewriting Git history. Baseline stays default; native truncation telemetry stays unknown. Existing reservations remain estimates, not hard actual-charge caps. Worker task/commands/results are data, not operator authority. Holdouts, keys and raw thoughts stay outside publication and worker access.

The user has authorized implementation of this existing proposed scope. Disposable validation deployment and one scoped model-driven dogfood are part of that scope; production approval remains a distinct gate. New provider/SDK/framework/datastore choices require the existing decision/debate process rather than silent substitution.

## Delivery dependencies and tasks

- [ ] Audit existing validation and delivery surfaces; distinguish present signed validation from missing operational integration.
- [ ] Link the actual selected WorkUnit build identity, exact candidate archive, locally prebuilt signed image and original baseline to the disposable network-none validator. Never accept a worker/caller URL as target authority.
- [ ] Preserve owned validation container identity before start; retain durable intent/result so accepted-start timeout and interrupted collection do not create or resend a replacement. Retain evidence and stop only the original owned lifecycle.
- [ ] Expose operator validation/observation separately from paid worker dispatch; accept the exact signed evidence into the original WorkUnit and preserve a verifiable receipt. Recover accepted response loss by original readback, never reset the build.
- [ ] Require signed application validation for application publication; keep explicitly scoped repository-only publication honestly distinct. A reviewed PR must reference its exact validation evidence and checks.
- [ ] Complete Phases10–11 acceptance, rejection, source/artifact/holdout substitution, crash-after-acceptance and approval tests at their real boundaries. Use pinned existing test tools, with browser E2E for user interfaces.
- [ ] Run Phase12 on one small fullstack feature: freeze intent, baseline, private acceptance and signing inputs; dispatch one real factory build; collect exact result; build candidate locally; transfer immutable artifact; independently evaluate; validate original WorkUnit; review source; pass exact CI; open and review PR. Preserve failed consumption and originals; no automatic paid retries.
- [ ] Implement Inception goal→yearly/quarterly approvals→monthly epic proxy→work packages according to existing architecture, including durable revision-sensitive decisions and append-only audit evidence.
- [ ] Implement deterministic risk/security issue qualification, authorized issue intake and dark/human routing. Keep model complexity routing separate and unchanged.
- [ ] Implement the planned dashboard, approval flows, state visibility, audit persistence and error/empty/keyboard/mobile states. Verify rendered flows and backend persistence, not screenshots alone.
- [ ] Audit every existing planning artifact against current code/runtime/evidence; write plan-gaps.md with exact evidence and severity. Fix all objective-required gaps and rerun appropriate checks. Do not silently waive requirements.
- [ ] Independent review, secret-safe atomic commits, required exact local CI and remote publication verification. Complete the goal only after all preceding deliverables have authoritative evidence.

## Verification contract

Source and artifact must identify the actual selected original/successor attempt and built commit. The wall must evaluate the actual isolated artifact, keeping holdouts unavailable to candidate code. Failed checks cannot mint passing validation evidence. Signed evidence cannot be replayed for a different unit, attempt, revision, artifact or scenario set. Response loss must reconcile the original identity. Approval rejects cannot advance delivery, and initial production approval is never inferred from sandbox validation. Fullstack dogfood requires a real model-generated change and reviewed remote PR, not a fixture result. Inception/qualification/dashboard each require complete behavior, persistence, authorization and visible workflow tests. Final audit must prove all numbered user objectives and applicable plan requirements, with missing/indirect evidence treated as incomplete.

## Independent audit delegation

| Task | Agent | Ownership | State |
| --- | --- | --- | --- |
| Existing delivery/validation boundary | delivery_binding_audit | read-only source/test audit | dispatched |
| Inception/qualification/dashboard requirements | build_scope_audit | read-only requirements/code audit | dispatched |

## Required phase completion checklists

The following gates are copied verbatim from the planning skill. They are not current pass claims. Scope-specific evidence is recorded in progress.md and the verification artifacts before any phase is declared complete.

### phase-09-implementation.md

## Hard Gates

- [ ] **Scope fidelity**: every IN-scope deliverable in `phased-plan.md` is represented by at least one merged/committed change. Nothing OUT-of-scope was implemented.
- [ ] **User-story coverage**: every P0 story from `user-stories.md` has a code path that fulfils its acceptance criteria. Evidence: story ID referenced in commit messages, code comments, or `progress.md`.
- [ ] **Architecture adherence**: implemented component boundaries match `architecture.md > ## Components`. Deviations were negotiated and recorded in `progress.md`.
- [ ] **Tech-guide adherence**: versions, conventions, and dev-env commands match `tech-guide.md`. Deviations recorded with rationale.
- [ ] **Subagent delegation used where required**: any phase with 2+ independent tasks dispatched parallel agents; delegation table in `phased-plan.md > ## Delegated Work` shows statuses and results.
- [ ] **Error log is clean**: `progress.md > ## Errors` has no open errors. Every entry is either `resolved` with resolution, or tagged `accepted_risk` with a user sign-off line.
- [ ] **3-strike protocol respected**: no task shows 3+ repeated identical failing attempts. Every failure has a mutation entry.
- [ ] **Mid-phase volatile decisions debated**: every decision made during Implementation that falls into a Volatile Decision Category (LLM/model, AI SDK, third-party API/service, datastore swap, framework version bump, dependency major, architectural pattern change, auth strategy, data contract, deploy/CI pattern, observability) has a Mode B decision brief in `docs/planning/decisions/`, an `agents-argue:debate` outcome recorded in the brief, and an entry in `docs/planning/decisions/INDEX.md`. No silent adoption.
- [ ] **Secrets hygiene**: no secrets in code, commits, or planning files. `.env` present in `.gitignore` if used.
- [ ] **Build succeeds**: canonical build command from `tech-guide.md` runs to success locally. Output pasted or linked in `progress.md`.
- [ ] **Lint clean**: canonical lint command runs clean. Output linked.
- [ ] **Type-check clean** *(typed languages)*: type-check runs clean.


### phase-10-e2e-test-generation.md

## Hard Gates

- [ ] **Kickoff research done**: current Playwright API surface and best practices captured in `findings.md > ## Phase 10 Kickoff Research` (web-app projects) or current CLI-testing practice (CLI projects).
- [ ] **Story-to-test traceability**: `e2e-tests.md > ## Coverage Matrix` maps every P0 story — and every P1 story with observable UX — to at least one test file/test case with a stable ID.
- [ ] **Flow-to-test traceability**: every primary flow in `ux-plan.md` is exercised by at least one test.
- [ ] **Happy-path + error-path coverage**: for each mapped story, both a happy-path case and at least one error/edge case exist.
- [ ] **Test files committed**: test source files exist, conform to `tech-guide.md` conventions, and are discovered by the canonical test runner command.
- [ ] **Runner config**: `playwright.config.*` (or equivalent) committed with correct base URL, browsers, timeouts, and retries for CI vs. local.
- [ ] **Selectors policy**: tests use stable selectors (roles, labels, `data-testid`) — not brittle CSS/XPath. Policy stated in `e2e-tests.md > ## Selector Policy`.
- [ ] **Dry run**: the test runner can at minimum **collect/discover** all tests without errors. Evidence: command output pasted into `progress.md`.
- [ ] **Flakiness stance**: condition-based waits used instead of arbitrary sleeps. `e2e-tests.md > ## Waiting Strategy` explicitly forbids sleeps except where documented.


### phase-11-testing-verification.md

## Hard Gates

- [ ] **Unit tests pass**: full suite run, exit code 0. Evidence: command + output timestamp.
- [ ] **Integration tests pass** (if present): exit code 0.
- [ ] **E2E / Playwright tests pass** (if Phase 10 applies): exit code 0; no tests skipped without a logged reason.
- [ ] **Lint clean**: canonical lint command exit code 0.
- [ ] **Type-check clean** (typed languages): exit code 0.
- [ ] **Build succeeds**: canonical build exit code 0.
- [ ] **User-story acceptance**: for each P0 story, acceptance criteria have been walked through and checked off. For web/CLI projects, this means a live run of the flow — not code inspection.
- [ ] **Dark-pattern sanity**: for UX projects, a manual pass against `ux-plan.md > ## Dark Pattern Audit` confirms no regressions.
- [ ] **Accessibility spot-check** (UI projects): keyboard navigation, focus order, and contrast sampled on primary flows. Issues filed as `plan-gaps` if found.
- [ ] **No unresolved errors**: `progress.md > ## Errors` has zero open entries.
- [ ] **Cross-plugin verification considered**: `/done` warnings on Enterprise Assessment, Design Compliance, and Test Coverage reviewed (if those artifacts exist). Blocker-class issues either fixed or explicitly accepted by the user in `progress.md`.


### phase-12-delivery.md

## Hard Gates

- [ ] **All earlier phases closed**: every non-skipped phase 1–11 is `complete`. Phases marked `skipped` or `complete_with_override` list a reason in `phased-plan.md`.
- [ ] **README / docs updated**: user-facing documentation reflects what was actually built. Outdated sections edited, not abandoned.
- [ ] **Changelog / release note**: a single entry describing what shipped, in the project's conventional format (CHANGELOG.md, release description, PR body).
- [ ] **Demo evidence** *(where applicable)*: for UI / CLI features, a screenshot, terminal recording, or short GIF exists and is linked from `progress.md`.
- [ ] **Cleanup**: dead code, commented-out code, temporary scripts, and scratch files removed. Dependencies added during exploration but unused are removed.
- [ ] **Dependency audit**: `npm audit` / `pip-audit` / `cargo audit` (whichever applies) reviewed. High/critical issues either fixed or accepted with a logged rationale.
- [ ] **Secrets & config**: no live secrets in the repo; `.env.example` updated if env-var contract changed; rotation recorded if any secret was exposed during work.
- [ ] **`verification-before-completion` applied**: the `superpowers:verification-before-completion` discipline — run the commands, show the output — has been followed for the final sign-off run.
- [ ] **User sign-off**: the user has explicitly confirmed the work is acceptable. Evidence: timestamped line in `progress.md`: `User signed off on delivery <date>: <quote or paraphrase>`.


### phase-13-plan-gap-audit.md

## Hard Gates

- [ ] **Full artifact coverage**: the audit has diffed the implemented code against **each** of these artifacts:
  - `user-stories.md` (every story ID)
  - `architecture.md` (every component + API)
  - `tech-guide.md` (every version + convention)
  - `ux-plan.md` (every flow, error/empty/loading state)
  - `ui-plan.md` (every token, component, state)
  - `e2e-tests.md` (every mapped story/flow)
  - Each `phase-#-plan.md` (every task in Tasks + every item in Verification)
- [ ] **Per-gap fields**: every row in `plan-gaps.md` has: `id`, `source artifact`, `source item`, `gap type (missing | partial | deviated)`, `severity (blocker | major | minor | nit)`, `proposed fix`, `status (open)`.
- [ ] **Severity rubric applied**:
  - `blocker` — IN-scope requirement missing or broken core flow.
  - `major` — significant quality, accessibility, or correctness gap.
  - `minor` — cosmetic or low-impact drift.
  - `nit` — style / polish.
  Rubric stated at the top of `plan-gaps.md`.
- [ ] **No phantom gaps**: every gap cites a concrete source line (artifact file + heading) and, where applicable, a concrete code file + location showing absence or deviation.
- [ ] **No missed artifacts**: a completeness line at the top of `plan-gaps.md` of the form `Audited against: user-stories.md, architecture.md, tech-guide.md, ux-plan.md, ui-plan.md, e2e-tests.md, phase-1-plan.md … phase-N-plan.md`. Any "not applicable" entry lists the reason.
- [ ] **User review**: the gap list has been presented to the user. The user has confirmed severities (or corrected them) and identified `wont_fix` rows with rationale. Confirmation recorded in `progress.md`.
- [ ] **Sorted by severity**: `plan-gaps.md` rows are ordered `blocker → major → minor → nit`.
- [ ] **Silent volatile decisions flagged**: the audit has identified any volatile-category choice (LLM/model, AI SDK, third-party service, datastore, framework version, dep major, architectural pattern, auth strategy, data contract, deploy/CI, observability) that was adopted during Phase 9 without a Mode B debate brief. Each one is recorded as a `major` gap in `plan-gaps.md`.


### phase-14-fix-all-gaps.md

## Hard Gates

- [ ] **Severity order respected**: `blocker` rows closed before `major`, `major` before `minor`, `minor` before `nit`. Evidence: timestamps in `progress.md` show monotonic order by severity bucket.
- [ ] **Zero open blockers**: `plan-gaps.md` contains no `blocker` rows with status `open`, `in_progress`, or `wont_fix`. Blockers **cannot** be accepted as `wont_fix`; they must be fixed or scope must be renegotiated with the user.
- [ ] **Zero open majors**: every `major` row is `fixed` or `wont_fix` with explicit user sign-off in `progress.md`.
- [ ] **Minors & nits**: every row is `fixed` or `wont_fix` (user sign-off line optional for `nit` but recommended for `minor`).
- [ ] **Per-fix evidence**: every `fixed` row cites the resolving commit SHA / PR / diff and the re-run test result.
- [ ] **Re-run relevant tests per fix**: tests exercising each fixed item were re-run and pass. Evidence: test output linked from `progress.md`.
- [ ] **Full-suite re-run at end**: after the last fix, the full test suite (unit + integration + e2e, as applicable) has been re-run and passes. Evidence timestamped in `progress.md > ## Test Results`.
- [ ] **Artifact reconciliation**: where a gap was resolved by updating a planning artifact (rather than code), the artifact edit is committed and `plan-gaps.md` notes `fix type: artifact-update`.
- [ ] **Volatile replacements debated**: any fix that introduces a replacement library, service, model, or architectural pattern (i.e. falls into a Volatile Decision Category) has a Mode B debate brief in `docs/planning/decisions/` and an entry in `decisions/INDEX.md` **before** its gap row is flipped to `fixed`.
- [ ] **Re-audit**: a quick re-check of the most-affected artifacts confirms no new drift was introduced by the fixes. Summary line in `progress.md`.
- [ ] **User final sign-off**: the user has confirmed `plan-gaps.md` is clean and the project is deliverable. Evidence: timestamped `progress.md` entry.

