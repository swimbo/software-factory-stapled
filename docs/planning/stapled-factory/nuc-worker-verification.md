# NUC worker verification — 2026-10-27

Target corrected by user from Railway to existing NUC `hermes-control` (bo uid1000, x86_64). The disposable Railway sandbox was destroyed; persistent Railway Restate was preserved. No unrelated NUC workloads were restarted, pruned or changed.

## Local build and transfer

All worker/proxy/firewall image construction occurred on the Mac targeting linux/amd64 and worker uid1000. Docker save, gzip and approved-key SSH transferred images into the NUC daemon; no source builds or GitHub runners ran remotely. Actual docker-save OCI manifest bytes were hashed locally and their config digests matched local Docker image IDs. NUC image IDs then matched those manifest hashes exactly. Docker29 containerd reports OCI manifest IDs rather than the Mac daemon's config IDs; these are distinct identities, not an arbitrary substitution.

| Role | Local config SHA256 | Transferred OCI manifest SHA256 |
| --- | --- | --- |
| Python helper | 1ba42ef4e94c8e45e52e3a68e4c42245a30e50936d02e4722872b9343be7b531 | 9b09eb2859006ce36d3a5223bb7c697282c1ac8b5c7a96bed8135f4ffc9ed057 |
| Firewall | 842e0706bcd1e63de31b5f5bb6eca5128fbee3321bcb329c4f067e5f9110facb | 0e95faf5bdcbe6030ea7ede690af1f22af37645747ea8792a254912daf97233d |
| Synthetic proxy | f881fce221dd37af5414746e790bbaf098dfd8c16a0d2d2f6ce9328109812fcb | b5b719613906f4ac3a3d680b0ecd39cf5c8d018229b26bda544961dbb8b26dee |
| OpenHands worker | 048bd5f7e85d99dde3eb97a9223504877ee58c4954441c6e46fd6df185118919 | 026fbb4d6f9e1a6eae146e25a107ea003a067d9a214201e5b8de75b0af3c9f77 |
| Instrumented live proxy | 27812a3aff2e8179af2dc892a876e0e32bbff06155dbe06c58926497a9021133 | 6a1031459dca50f45007e6e796a92211b9bcc2b1770cebd622d284f070cb45b6 |

This proves transferred content identity. It does not authenticate the upstream publisher or constitute a signed production release. Probe source included current working-tree changes.

## Actual integration results

The synthetic joined probe passed on the NUC with containerized Restate1.8, real CLI and OpenHands server, source fencing, fresh successor workspace, a verified clean descendant commit and the same shared reservation ledger. Duplicate restart left workunit state, container identities and ledger unchanged. The quiescent attempt database, anchor, budget database, budget anchor and both workspaces were retained at `/home/bo/.local/share/stapled-factory-control/recovery-fixture-run4` for the independent backup drill; both SQLite integrity checks passed. Disposable Restate data was not retained in this fixture.

The live joined probe also passed on the NUC: actual in-flight Bifrost request, source killed/fenced, successor committed result, duplicate restart invariant checks. Five requests reserved 1,579,010 microdollars ($1.579010 estimated). Actual provider charges were not measured. Credentials remained in the existing owner-only NUC file and were passed by the real CLI to the proxy over stdin; no credential was copied into source, argv or environment.

The live successor's actual container negative controls passed: nonroot uid, host secret fixture and attempt/budget paths unreadable, Docker socket absent, direct TCP and DNS denied, root directory writes denied by nonroot permissions; workspace write/delete succeeded. The gateway-only namespace firewall and pinned gateway route were the real provisioner's configuration. This tested kernel isolation does not make the Docker administrator or host untrusted.

Disposable probe containers and volumes were removed. Host free space stayed above 24GiB during the run (initial34GiB); no shared images or volumes were pruned. These disposable sandbox-scope checks do not establish unattended scheduling, production terminal tool mediation, or signed publisher provenance.

## Portability fixes exercised

Prebuilt image manifests avoid remote builds. Linux containerized Restate accepts empty HTTP200 health responses; disposable internal port15122 avoids persistent Restate's internal5122. Synthetic credentials use the secure file reference instead of requiring Ruby. A retained-root option exports the cold budget pair before owned resources are removed.

Correction: the container filesystem is not mounted read-only. The negative control proves a nonroot process cannot create a file directly in `/`; it does not prove that every image path is unwritable. `/tmp` is intentionally writable.

## Unpaid proxy egress proof

A fresh disposable provision with a dummy key, trusted prebuilt live proxy image and no model POST verified the actual proxy namespace's OUTPUT policy before and after execution. Real connection attempts to `1.1.1.1:443`, metadata `169.254.169.254:80`, the allowed gateway address on port80, IPv6 `2606:4700:4700::1111:443`, and DNS UDP53 were denied. A TLS handshake to `100.92.236.42:443` verified the expected `hermes-control.tail4296e6.ts.net` certificate/hostname; no HTTP request or credential was sent. The worker's actual `network=none`, capabilities, host/store/socket negatives and workspace positive control also passed. `worker/probes/proxy_negatives.py` reproduces this check. All owned resources were removed.

## Complete joined cold fixture

A further synthetic-only run passed the same real CLI/OpenHands recovery, strengthened duplicate assertions and worker isolation controls while binding Restate's data directory into the retained fixture. `/home/bo/.local/share/stapled-factory-control/recovery-fixture-complete` contains the exact joined Restate store (`restate-data`, node name `factory-recovery-probe`), attempt database and anchor, budget database and anchor, and original/successor workspaces from one stopped generation. WorkUnit `unit` reached `needs_validation`, with successor attempt `unit:0:successor` and a verified commit; five synthetic reservations totaled 2,000,000 microdollars. The Restate container ran uid1000 and stopped before retaining the cold fixture. The independent backup agent owns off-host encryption and actual restore/readback verification; fixture retention alone is not a restore proof. Minimum free space observed during this extra run was 23GiB. No additional paid model calls occurred.
