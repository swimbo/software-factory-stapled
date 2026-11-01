# Dependency provenance evidence ledger

Evidence grade for selecting a direction: adequate. Evidence grade for accepting an implementation: insufficient; no controlled builder or real end-to-end proof exists yet.

- The current signed build binds Git revision, archive, diff, Dockerfile and image configuration; it does not enumerate network-fetched materials. See `stapled-factory/deployment/nuc/build_candidate.py`.
- The existing negative image fixture caused OSV to report 135 npm packages from a hidden lock when one package metadata file was present. See `docs/verification/delivery-lifecycle-2026-11-01/fullstack-dependency-probe.json`.
- A separate local scratch-image control used identical npm package metadata but changed JavaScript bytes. Trivy returned the same package name, version and purl for both images; their image and code hashes differed. This demonstrates a limit of that package-identity comparison, not the behavior of every scanner configuration.
- Docker documents that network selection applies to `RUN` instructions, so it is not by itself proof that every Dockerfile input was isolated. [Docker build reference](https://docs.docker.com/reference/cli/docker/buildx/build/).
- BuildKit can identify local-source material completeness as false, and its attestation storage depends on the exporter and image store. [Provenance definitions](https://docs.docker.com/build/metadata/attestations/slsa-definitions/); [attestation storage](https://docs.docker.com/build/metadata/attestations/).
- OSV lists Rust binary extraction for binaries built with cargo-auditable. That project disclaims supply-chain protection and full coverage of native statically linked libraries. [OSV supported artifacts](https://google.github.io/osv-scanner/supported-languages-and-lockfiles/); [cargo-auditable](https://github.com/rust-secure-code/cargo-auditable).
- The current fullstack template has missing npm locks and its agents manifest does not resolve as written. A separate private diagnostic lock is not an approved template change or release. See `docs/verification/delivery-lifecycle-2026-11-01/fullstack-dependency-probe.json`.

Open proof gaps: exact supported recipe and ecosystem contract; immutable materials acquisition and publisher verification; independently established builder and network isolation; native and vendored dependency handling; exact output/receipt binding; real build, transfer, recovery and adversarial controls; independent release review.
