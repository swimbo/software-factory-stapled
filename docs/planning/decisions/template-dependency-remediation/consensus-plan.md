# Fullstack template dependency remediation

Status: conditional diagnostic direction; no shared-template change or release approved by this record.

The private candidate upgrades `jsonwebtoken` to 10.4.0 with default features disabled and only `aws_lc_rs` enabled. Reviewers favor testing this route because it includes the published claim-type fix and does not select the RustCrypto RSA backend through jsonwebtoken. The Rust 1.99.1 full image has passed offline `cargo check` for the original lock and the private JWT candidate (the latter after a timed-out run was continued using its retained cache); its exact digest, publisher and complete release build still require verification. Do not substitute an untested slim image or infer that Cargo offline mode confines every build input.

Before applying any template changes, verify a locked Linux amd64 release build and actual cross-service JWT behavior. Test existing and new HS256 tokens with dummy keys, altered signatures, wrong keys and algorithms, required and malformed claims, expiration boundaries and HTTP rejection paths. Explicitly assess existing Rust/Node expiration and claim-validation differences; do not silently change a policy affecting active sessions. Confirm the exact selected feature graph and preserve the remaining lockfile RSA advisory with a target-specific applicability analysis. Pin and verify the selected builder image and materials, independently review the exact change and preserve the original template for rollback.

These steps concern a shared template used for future projects. They do not update existing applications, prove universal security, satisfy the factory provenance gate or authorize runtime activation. [Evidence](evidence-ledger.md) and [debate record](debate-transcript.md).
