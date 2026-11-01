# Template remediation debate record

Five perspectives reviewed the private candidate: supply-chain security, CI/CD, reliability, quality engineering and product usability. A moderator synthesized their research and positions; this is a concise record, not a verbatim transcript.

The group conditionally favored jsonwebtoken 10.4.0 with `aws_lc_rs`, default features disabled, and the actually tested Rust 1.99.1 full image. The proposed dependency change remains experimental until release-mode and cross-service behavior tests pass. An initially suggested slim image has not been tested and cannot be substituted without its own evidence. All perspectives retained the RSA lockfile finding despite the target-specific feature-tree observation.

The QA and product perspectives identified an existing Rust/Node expiry tolerance and claim-validation difference. The decision does not silently change that policy. Required tests cover old and new HS256 tokens across both services, malformed/missing claims, altered signatures, wrong keys and algorithms, and expiry boundaries. The shared template remains unchanged; any future change requires exact-image and material verification, independent review, and a rollback copy.
