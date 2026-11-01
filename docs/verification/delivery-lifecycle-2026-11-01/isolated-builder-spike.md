# Isolated builder feasibility spike — November 1, 2026

This is a local synthetic feasibility check, not a factory release or a dependency provenance verdict. The NUC runtime was not changed.

A dedicated Docker Buildx `docker-container` builder was created with `network=none`; the container inspection reported that network mode and no Docker socket mount. The container was **privileged**, with its own writable BuildKit state volume. It used `moby/buildkit:v0.34.1`, local image config digest `sha256:f5f3a2ee06406dbd79d82390363268cf0b98797be1e4e56217e5403329db26d2`; registry digest observation and tag do not establish publisher authenticity.

The first build failed before execution because `BUILDX_DEFAULT_POLICY=1` rejected the `oci-layout` source scheme. The second build removed that build-time policy, used the same isolated builder, `--network none`, `--no-cache`, and a local OCI-layout Node image pinned to manifest `sha256:6747c4c23cd04c68ec99867b5a7aaa6db9db2e842adda3e69b2f5abd9f896ee7`. A simple `RUN` command observed a failed TCP connection to `1.1.1.1:443`, and the build exported an OCI image. This is one connectivity check, not proof against every network, daemon or malicious-build escape.

The output index digest was `sha256:1e5b0ad1fde5ccbd8b2663cf9fefb594dc2e73b8ffcd2f54d305bcd2c030fd26`, the image config digest was `sha256:52903af6254676a084cb087e914911478101f787514157000c42351f5b81426d`, and the OCI archive SHA-256 was `e7e15074cd5bafb4ce7f0662e47acceab347373d4b4b078eaec4f16774509e09`. A local point-in-time verifier checked its descriptor graph and archive. It is not wired into the build, transfer, evaluation, or receipt paths.

Retained private evidence: `/Users/bo/.local/share/stapled-factory-release/out/isolated-builder-spike-c10b368cec634867a9c12fdc2f23a184`. Failed build stderr SHA-256 `0dcfe18808ef7ae4ddd658a7a9c528a303f4fa643ec6b9a9b9a5fc6e0499ae07`; successful build stderr `da3ce761d910d95362bfa40014453be638fd79da07a877dbd969d583e2728098`; Buildx metadata `bd6414a659bbceed0121cedbe081a22e9b69284d0883dfb834fa16b2de2121a1`. The private OCI archive and source base remain retained. No real candidate was built or dispatched.

The publication and runtime deployment holds continue: the builder and base identities, immutable input handling, independent signed source/material-to-image binding, supported npm and Rust builds, and existing recovery integration remain to be established. The privileged builder's trust boundary requires explicit review.
