# Local CI

This repository does not use GitHub runners. Run `python3 scripts/verify-local.py`
before pushing the committed revision. Uncommitted changes are excluded. It verifies an isolated clone of
HEAD, scans for leaked values, runs Python and Node tests, audits locked npm
dependencies, and builds the Restate spine locally. Receipts and hashed logs live
in ignored `.local-ci/<revision>/`. A passed receipt proves these local checks;
it does not prove production provisioning, isolation, or recovery.

Build deployment images locally for the target platform and transfer the built
artifacts to Railway. Do not configure provider-side source builds.
