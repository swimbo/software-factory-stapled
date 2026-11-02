# Dependency provenance debate record

Five research perspectives reviewed the decision brief: supply-chain security, CI/CD architecture, site reliability, quality engineering, and product usability. Each inspected the repository and used primary technical sources. A moderator synthesized their findings and a position round; no major disagreement remained. This is a concise record of positions, not a verbatim transcript.

- Supply-chain security: restrict a future profile to operator-owned recipes, verified immutable materials and isolated building; do not represent a checksum as publisher authenticity.
- CI/CD architecture: bind the actual builder, policy, materials and output; do not assume `docker save` retains attached attestations or that disabling `RUN` networking covers all input channels.
- Reliability: retain existing one-shot identities and recovery evidence; keep runtime activation held until the requested provenance and release gates pass.
- Quality engineering: require real npm and Rust positive builds and adversarial material, image, isolation and crash controls; mocks alone do not prove these boundaries.
- Product usability: provide preflight before dispatch and before the one-shot build intent, while explaining that pre-dispatch evidence cannot predict candidate changes.

The moderator found agreement on the opt-in constrained-builder direction. Exact recipe formats, builder isolation, unsupported binary handling and publisher-authenticity policy remain unresolved implementation decisions; the evidence does not justify claiming complete provenance now.
