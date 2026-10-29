# Factory-produced wall request-default fix

When request method/path are omitted, the transport defaults to `GET /`. Previously a failed status/body expectation indexed absent keys and crashed instead of reporting a normal failure. The real NUC worker produced commit `e1a39fa47835c28b8591e9d9648866cfea9d90aa` against main baseline `f5f18a4d848a687c9d9a2be7bd98dc3828bc4dd6`; only the evaluator and its new regression test changed.

Five contained regressions passed; the original evaluator failed four with KeyError while the explicit-fields control passed. Independent actual CLI checks covered eight scenario quorums with three runs each, including status/body mismatches, omitted fields, explicit fields and a passing request. The signed, locally built image passed the frozen external factory validation adapter and private Restate acceptance reached `validated`. The security pipeline used regex-fallback SAST/secrets and manifest-flag dependency checks; no full enterprise security claim is made.

The worker made nine real auto-routed Bifrost calls to Flash, using the trusted attempt session and existing complexity rules. Recorded reservations were **$0.890006** against the $2 best-effort target. Actual charges remain unknown; null costs are not zero and no hard cap is claimed. No GitHub runners, remote builds, privileged worker credentials or main merge were used.

`verification.json` binds source, build/config, artifact, scenario, runtime and CI identities. `operator-scenarios.json` and `validation-server.py` preserve the independent CLI protocol checks; they were supplied only by the operator at candidate validation, after worker completion. The trusted protocol adapter invokes the actual candidate CLI via `/dev/stdin` against a loopback fixture in the network-none image. It contains no expected answers.

After acceptance, only this task's worker/proxy were stopped and the previous endpoint binding restored. Durable evidence, workspaces, budget/anchor volumes and Restate state were retained. The fix is proposed for review and is not installed into the factory runtime. Existing broader release/security gates remain open.
