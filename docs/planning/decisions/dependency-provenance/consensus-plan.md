# Dependency provenance: decision brief

Status: direction selected; implementation and release gates remain open.

Use a new, opt-in factory-controlled build profile for future builds. A trusted builder must verify immutable source, recipe, toolchain, base image and dependency materials; isolate build steps from undeclared inputs; and sign an externally generated record binding those materials to the exact output. Define the supported ecosystems and binary formats explicitly. Unknown inputs remain incomplete. Existing arbitrary-Dockerfile builds remain inspectable but cannot establish complete provenance. Existing attempts, receipts and recovery intents retain their original contracts.

Run read-only preflight before dispatch and again before creating the one-shot build intent. The first cannot predict candidate changes. Pin the new evidence to the original transfer and evaluation intents and reuse it on observation and recovery. Keep vulnerability scanners and SBOMs as corroboration, not proof of package origin. Checksum verification alone establishes that bytes match a lock entry, not publisher identity or complete material capture.

Acceptance requires real supported npm and Rust builds; independent image, material and signature binding; controls for changed payload with unchanged metadata, forged lock data, missing materials, external contexts, network and cache bypasses, image and receipt swaps, unsupported binaries and recovery without rebuilding or resubmitting; and independent review plus exact-revision CI. The factory runtime's release audit and application publication are separate gates. The requested ordering holds runtime deployment until the provenance work passes.

Research, disagreement and evidence: [evidence ledger](evidence-ledger.md) and [debate record](debate-transcript.md). No SLSA level, publisher authenticity, reproducibility, complete dependency inventory or release approval is claimed by this decision.
