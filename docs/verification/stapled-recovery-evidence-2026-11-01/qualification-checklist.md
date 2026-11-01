# Stapled recovery evidence qualification checklist

Claim: `software-factory-stapled:delivery-loop-qualification-and-recovery:assemble-stapled-recovery-evidence-bundle`

## Qualification record

- [x] **Full NUC deployment and recovery suite passed.** The approved command `/home/bo/.local/share/hermes-dev-projects/test-venv/bin/pytest -o testpaths=stapled-factory/deployment/nuc -q` exited `0` with `127 passed, 6 subtests passed in 2.03s`. The complete captured stdout/stderr is in [nuc-test-output.txt](nuc-test-output.txt).
- [x] **Recovery behavior was included in the full selection.** The `deployment/nuc` test path included the operator-workflow and evaluation-recovery tests, together with the other deployment qualification tests under that directory; no narrower test selector was used.
- [x] **The run had zero live side effects.** The exact pytest process ran inside a fresh empty user/network namespace with only loopback enabled. It made no NUC, Docker, GitHub, provider/model, or production contact. Immediately afterward, tracked `git diff` exited `0` and `git status --short --untracked-files=all` was empty. The machine-readable boundary is in [execution-boundary.json](execution-boundary.json).
- [x] **Evidence provenance is retained.** The qualification source revision is `24b4ca4ffd9173d3c9916f611c5043fd8ad16795`; [evidence-manifest.json](evidence-manifest.json) binds the bundle files by SHA-256.

## Boundary

This bundle qualifies fixture-backed local source behavior only. It does not launch the corrected NUC probe, activate a runtime, authorize paid dispatch, accept a live receipt, bypass an operator gate, or establish production readiness.
