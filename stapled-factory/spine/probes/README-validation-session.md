# Isolated signed receipt boundary

Build the spine locally with its locked dependencies before running this probe.
Supply private complete passing `validate_prebuilt` checks and their matching
signed-build manifest; keep them outside worker mounts and publication.

```sh
python3 stapled-factory/spine/probes/restate_validation_session.py \
  --checks /private/candidate/checks.json \
  --baseline FULL_BASELINE_COMMIT \
  --build-manifest /private/candidate/build.json \
  --output /private/proof/new-exclusive-directory
```

The output directory must not exist. The probe retains its private evidence,
fresh fixture key, logs, original journal and Restate data. It stops only its
owned local processes on normal completion or an exception. Inspect retained
process records before any cleanup after abrupt host/client termination; do not
rerun with the same output or delete/reset an unresolved operation.

The probe imports the actual compiled WorkUnit handler into a generated unsigned
loopback-only test endpoint and seeds a clearly identified fixture build. It
checks stale bindings and HMAC tampering, crashes its client after real committed
acceptance but before local acknowledgment, crashes/restarts the owned endpoint,
and reconciles the original journal twice without reevaluating or resubmitting.
It checks stored aggregate receipt equality and rejected duplicate acceptance.

This proves a control-plane boundary with a fixture key and fixture-seeded unit.
It does not establish checks provenance, a model build, production endpoint
identity/ingress authorization, actual target execution or a reviewed remote PR.
There are no factory/model dispatches or shared-service operations.
