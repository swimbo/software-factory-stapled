# User Stories

The two users of this system are the **intent thinker** (the human operator who authors goals and
approves the strategic cascade) and the **harness engineer** (the person building/maintaining the factory).
There are no end-users — the "end product" is code merged without a human reading the diff.

## Intent thinker (operator)

1. **Author a 3yr goal** — As an operator, I author a structured 3yr goal (objective + measurable outcomes)
   so the factory has a single machine-derivable source of intent.
   - AC: the goal is stored as a versioned doc; the Inception stage can consume it and begin decomposition.

2. **Approve yearly and quarterly OKR drafts** — As an operator, I review and approve the factory's drafted
   yearly OKRs and quarterly OKRs, so I keep the steering wheel on *what to build*.
   - AC: each draft blocks the next decomposition level until I approve; my approval is recorded in
     `ApprovalGate`.

3. **Let the proxy approve monthly epics** — As an operator, monthly epics are approved by the hybrid proxy
   (deterministic policy gate + LLM judge) so I don't hand-review every month's epics.
   - AC: an epic auto-approves only when both layers pass; every verdict is logged; I can audit any
     approve/reject with a one-line reason.

4. **Stay in the loop on the three production gates** — As an operator, I approve the first production
   deployment, budget raises, and privileged credentials, while routine drift runs dark.
   - AC: promotion is blocked on `verification.yaml` passing + my sign-off; agents cannot self-raise budget
     or self-issue privileged credentials.

## Harness engineer

5. **Build the wall first** — As the harness engineer, I build the isolated holdout evaluator before any
   auto-merge, so a generator can't grade its own work.
   - AC: 5 holdout scenarios stored out of reach; triple-run + 2-of-3 + 90% gate; failure feeds back as a
     one-line message, never scenario text.

6. **Reclaim lost work** — As the harness engineer, a crashed worker's work is recoverable via receipts
   bound to git revision, so timeouts reconcile idempotently.
   - AC: kill a worker mid-task; Restate replays the journal and resumes from the checkpoint without
     duplicate side effects.

7. **Kill a runaway agent** — As the harness engineer, I can pause/cancel/kill a live invocation, so a
   3 a.m. runaway has an escalation path.
   - AC: Restate Operations surface can pause/resume/cancel/kill a running agent invocation.

8. **Route work to the right lane** — As the harness engineer, the qualifier scores each issue and routes
   it dark-lane vs human-lane, so low-risk work flows unattended and risky work reaches a human.
   - AC: trivially-safe issues route dark; privileged/destructive issues route human.

## Priorities & phase mapping

- P0 (Phase 9): story 5 (wall), story 6 (receipts), story 7 (kill-switch) — the wall-first MVP.
- P1 (Phase 9b): stories 1–3 (cascade + proxy).
- P2: story 4 (gates), story 8 (qualifier).
