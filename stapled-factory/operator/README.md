# Single-operator task workflow

This CLI connects the existing signed NUC provisioning, Restate/OpenHands worker,
Git export, local checks and GitHub PR path. It does not install a new runtime,
merge a PR, deploy candidate code, or schedule tasks. The NUC must already run a
verified frozen factory release, with its existing image/public trust root and
private gateway credential reference. SSH uses `hermes-control`, BatchMode and
no forwarded agent. The existing Mac image-approval key signs task preparations;
its bytes never enter the worker, NUC command arguments or build context.

## Task input

Create a JSON file with exactly these fields (absolute local paths):

```json
{
  "unit": "operator-docs-20261028-01",
  "repo": "/absolute/path/to/clean/repository",
  "baseline": "main",
  "task": "Correct current operator deployment and review policy instructions",
  "acceptance": ["Current instructions use the NUC and retain strict local CI"],
  "allowed_paths": [".github/README.md", "docs/planning/stapled-factory/release-approval.md"],
  "check_script": "/absolute/path/outside/repository/check_policy.py"
}
```

The operator-owned stdlib Python check receives the collected candidate directory
as its only argument. It stays outside the generator repository; its captured
hash is rechecked before publishing. Acceptance text goes to the worker, but the
check script does not. A new task needs a new unique unit. The source checkout
must be clean and the baseline must name a Git ref at the current GitHub main commit; feature baselines are rejected. Scope paths are
exact filenames, not wildcards. This version publishes to that GitHub repo's
`main` branch via a new `codex/<unit>` PR and uses its existing
`scripts/verify-local.py` gate.

## Explicit operator steps

```sh
python3 stapled-factory/operator/cli.py prepare /absolute/path/task.json
python3 stapled-factory/operator/cli.py run ~/.local/share/stapled-factory-operator/runs/operator-docs-20261028-01
# Inspect candidate.diff and candidate source/tests before executing their CI.
python3 stapled-factory/operator/cli.py publish ~/.local/share/stapled-factory-operator/runs/operator-docs-20261028-01
python3 stapled-factory/operator/cli.py status ~/.local/share/stapled-factory-operator/runs/operator-docs-20261028-01
```

`prepare` verifies the currently deployed release, captures the clean source as
a Git bundle, seeds a fresh isolated workspace, and signs exact task/runtime
inputs. It does not dispatch a model call. Pricing policy expires after an hour;
prepare a new unit if it expires before first dispatch.

`run` temporarily binds the existing endpoint to that task and starts the keyed
workflow once. Lost responses are observed under the same intent, never resent.
A global lock and persistent active marker prevent another task from rebinding
an uncertain run. Run the same command to observe an uncertain outcome; do not
create a replacement unit just to bypass uncertainty. If the process dies before
sending the intent, manual reconciliation may be necessary. No automatic retry,
successor or budget reset is performed. Existing guarded attempt and budget
stores remain authoritative.

After clean committed completion, `run` exports Git from inside the confined
worker and clones its bundle into a fresh operator repository, checks exact
revision/path scope, stops only its completed worker/proxy, and restores the
previous endpoint binding. Ledgers, volumes, workspaces and results remain.
Interrupted or rejected execution leaves an explicit `NEEDS_INTERVENTION` state;
its binding is retained until safely observed/reconciled. Never delete the
NUC `operator-active.json` marker or change a binding while effects are uncertain.

`publish` is the explicit diff-review boundary: it executes operator checks and
candidate local CI on the Mac, then pushes only the completed revision and opens
or reuses an unmerged PR. Review all code, tests and dependency changes first;
this command is not a host execution sandbox. The trusted CI runner itself may
not be changed by a worker through this path. Do not publish an unreviewed diff.
A rejected check produces no push or success status. Merging and deployment are
separate operator actions; this CLI has no such commands.

The status command prints the last recorded result, reserved request/spending counters and Restate
stage; it does not monitor GitHub after publication. Reservations use the existing $2 best-effort policy; actual charges are
unknown and not capped. A repository-only task can open a locally verified PR
while Restate remains `needs_validation`: that accurately means the separate
signed application-candidate validator has not accepted a deployed app. This
CLI does not forge application acceptance from local tests or conflate an open
PR with merge, deployment or enterprise release approval.
