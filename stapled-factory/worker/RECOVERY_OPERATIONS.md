# Coordinated control-plane recovery

Status: operator runbook; no whole-control-plane restore drill has passed. This
runbook does not authorize a deployment, quiesce shared services, or establish a
recovery time or recovery point objective.

## Snapshot boundary

A usable recovery set must contain the same quiesced generation of:

- Restate journal and service state, using the installed Restate version's
  supported backup procedure;
- attempt database and its identity/anchor, including SQLite WAL state through
  SQLite's backup API or a cleanly closed database;
- reservation ledger, any independent budget authority and witness state;
- standalone baseline and attempt/successor workspaces with complete Git objects;
- operator runtime configuration, exact image digests, service versions and
  credential references (secrets require a separate protected recovery channel).

Copying live SQLite main files or Docker volumes independently is insufficient.
A restore manifest must name every component, its generation, digest, encryption
and retention location, and the supported restoration procedure. An independent
witness must not be rolled back to match a restored local authority.

## Procedure

1. Inventory workload owners and establish an interruption window. Stop new
   ingress and drain orchestration. Fence every original and successor worker;
   verify stopped container identities and revoke active gateway capabilities.
   Treat unconfirmed remote effects as uncertain. Preserve the existing stores.
2. Stop the dedicated service writers. Create the coordinated recovery set using
   supported service backups and consistent database snapshots; hash every
   artifact into the generation manifest. Encrypt and protect the recovery set
   outside the worker's accessible filesystem. Resume only after checking the
   original service state, or keep the deployment offline.
3. Restore into an isolated environment with paid gateway access disabled and
   all external effect credentials revoked. Verify manifest hashes, store
   identities, baseline commits and image identities before starting services.
   Refuse missing components, mixed generations and witness regression.
4. Start the restored control plane behind disabled ingress. Reconcile recorded
   attempts by their original identities. No unresolved attempt may be silently
   redispatched, marked complete, or given a new budget because state was lost.
   Recovery from uncertain tool effects requires old-worker fencing and a fresh
   workspace under the original reservation policy.
5. Demonstrate that a completed unit remains complete, an in-flight original is
   fenced, a recorded successor is not duplicated, and reserved spend survives.
   Deliberately restore an older ledger and mix one component from another
   generation: both must prevent dispatch. Demonstrate workspace positive access
   and denied access to credentials, holdouts, Docker socket and authority state.
6. Record observed data loss, elapsed recovery time, exact versions and all
   negative controls. Operator reviews results before restoring ingress or paid
   capabilities. Retain the pre-restore stores until acceptance and retention
   policy allow deletion.

The ENT-04 High finding remains open until this complete drill passes with the
actual Restate storage backend and independently protected recovery artifacts.

## Executable cold-storage check

`python3 worker/probes/control_plane_restore.py` starts an actual disposable
Restate backend, cleanly stops it, copies the quiesced backend together with
fixture attempt/ledger SQLite stores, anchor/config and a committed Git
workspace, validates all file hashes, and boots the restored backend. Altered
ledger, anchor and configuration components each prevent acceptance. The local
check passed with zero paid requests and preserved 1,579,010 synthetic reserved
microdollars. It never interrupts shared services.

The drill now persists actual WorkUnit completed-build and unresolved-build state
through the real Restate endpoint, restores that non-empty backend, checks exact
state equality, and verifies both units reject a new claim without changing state.
External attempt/ledger records still use fixture schemas, and backup and manifest
share the same host. It does **not** verify full worker replay, independent backup
integrity, encryption/retention,
or recoverability of a deployed control plane. ENT-04 remains open until the
complete procedure above passes with real application state and protected
recovery artifacts. A writable manifest is not a rollback-resistant authority.
