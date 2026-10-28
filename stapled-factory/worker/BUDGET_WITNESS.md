# Budget witness contract

`WitnessedBudgetAuthority` is a fail-closed local primitive, not a deployed or
production-ready authority. It requires an independently protected witness that
offers atomic, durable compare-and-swap of an immutable namespace key and a
state digest. It must authenticate and authorize its caller, retain all accepted
advances across restart, failover and restore, and reject stale comparisons even
if every worker, proxy and authority database is restored. A witness whose
snapshot can be rolled back along with the authority does not meet the contract.
The caller must provision a globally unique, non-reused witness key in an
independent durable registry. A missing witness or an ambiguous reply must deny
access, never initialize or reset an existing identity. The witness and its
credentials must be outside the worker's control; local transport or filesystem
permissions alone do not establish host or operator independence.

Each transaction computes a digest of the complete authority policy table and
SQLite schema, mutates it under an exclusive writer transaction, and advances
the witness from the old digest to the new digest before committing. Separate
copies racing from the same state cannot both advance. If the witness commits
but the SQLite commit or acknowledgment fails, the authority becomes unusable
until an independently verified recovery procedure is designed; it must not
reset the witness or replay the mutation. The design favors denial over double
spending. Direct use of the unguarded `BudgetAuthority` or direct write access
to its database bypasses this protection and must be unavailable to the proxy.

Policy expiry currently relies on the authority host's wall clock. Production
requires a trusted, rollback-resistant time source or an independently enforced
expiry. Neither that time source nor a durable witness implementation and
trusted provisioning are configured. The in-memory witness in the test suite is
only a test double. Finally, a reservation is a dollar ceiling only when the
authorized policy's cost is a conservative verified maximum for every possible
upstream charge, including retries and fallback; the witness does not prove
pricing or gateway behavior.
