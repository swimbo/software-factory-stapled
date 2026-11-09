"""Owner-scoped teardown for disposable Docker resources a launch site creates.

A launch site that runs ``docker create``/``docker run``/``docker volume create``
owns what it created: on success, failure, exception and timeout it must remove
it again.  This module is that ownership record.  It is deliberately small and
stdlib-only so the worker, spine and probe trees can all import it.

Contract:
  * every recorded resource is removed in reverse creation order, best effort;
  * teardown never raises, so it can never replace the caller's original error;
  * teardown is idempotent - a second call removes nothing and returns ``[]``;
  * a resource may be recorded by *name before* the create call, so a create
    whose Docker response is lost (timeout, client death) is still removed;
  * every container a site creates should carry ``factory.launch`` and
    ``factory.launch-id`` labels, so it stays findable by a later sweep even if
    this process dies before teardown runs.
"""
import subprocess
import uuid

# Consistent label family for every container/volume/network a site creates.
LAUNCH_LABEL = 'factory.launch'
LAUNCH_ID_LABEL = 'factory.launch-id'

_CONTAINER, _VOLUME, _NETWORK = 'container', 'volume', 'network'
_REMOVE = {_CONTAINER: ['rm', '-f'], _VOLUME: ['volume', 'rm'], _NETWORK: ['network', 'rm']}


def launch_labels(site):
    """The consistent label family, for a site that retains instead of removing.

    A site whose container is deliberate evidence still must be *findable* by a
    later sweep.  Returns ``(labels, launch_id)``; no ownership is recorded.
    """
    if not isinstance(site, str) or not site:
        raise ValueError('launch site name required')
    launch_id = uuid.uuid4().hex
    return (['--label', LAUNCH_LABEL + '=' + site,
             '--label', LAUNCH_ID_LABEL + '=' + launch_id], launch_id)


def _default_runner(argv, timeout):
    return subprocess.run(argv, check=False, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, timeout=timeout)


class DockerScope:
    """Record disposable Docker resources and remove them on every exit path.

    ``site`` names the launch site (for example ``'successor-runtime'``).  Use
    :meth:`labels` when building create/run argv, then record with
    :meth:`container` / :meth:`volume` / :meth:`network` *before* the create
    call so a lost Docker response is still covered::

        scope = DockerScope('probe')
        name = scope.name('factory-probe')
        scope.container(name)                      # before create
        _run(['run', '-d', '--name', name, *scope.labels(), ...])
        try:
            ...
        finally:
            scope.teardown()
    """

    def __init__(self, site, *, docker='docker', runner=None, timeout=60):
        if not isinstance(site, str) or not site:
            raise ValueError('launch site name required')
        self.site = site
        self.docker = docker
        self.timeout = timeout
        self.launch_id = uuid.uuid4().hex
        self._runner = runner or (lambda argv: _default_runner(argv, timeout))
        self._created = []
        self._pending = set()
        self._protected = set()
        self._closed = False

    # ---- identity -------------------------------------------------------
    @property
    def closed(self):
        return self._closed

    def name(self, prefix):
        """A unique, greppable resource name for this scope."""
        return prefix + '-' + self.launch_id

    def labels(self):
        """Argv fragment carrying the consistent launch-site label family."""
        return ['--label', LAUNCH_LABEL + '=' + self.site,
                '--label', LAUNCH_ID_LABEL + '=' + self.launch_id]

    def label_filter(self):
        """``docker ps``/``volume ls`` filter value for this exact scope."""
        return LAUNCH_ID_LABEL + '=' + self.launch_id

    # ---- recording ------------------------------------------------------
    def record(self, kind, name, *, confirmed=True):
        """Record one already-created (or about-to-be-created) resource.

        ``confirmed=False`` records a *tentative* claim: the name is reserved and
        used in the create argv, but teardown will not delete it until
        :meth:`confirm` proves this call actually created it.  Use that for
        ``docker volume create``, which is idempotent and may therefore match a
        volume that already existed (and must never be deleted by us).
        """
        if kind not in _REMOVE:
            raise ValueError('unsupported docker resource kind: ' + str(kind))
        if not isinstance(name, str) or not name:
            raise ValueError('resource name required')
        if self._closed:
            # A scope that already tore down must not silently re-acquire
            # ownership of a new resource; that is how orphans reappear.
            raise RuntimeError('docker scope already closed')
        if confirmed:
            self._pending.discard((kind, name))
        if (kind, name) not in self._created:
            self._created.append((kind, name))
            if not confirmed:
                self._pending.add((kind, name))
        elif not confirmed:
            self._pending.add((kind, name))
        return name

    def confirm(self, *names):
        """Mark recorded resources as provably created by this scope."""
        for name in names:
            self._pending = {entry for entry in self._pending if entry[1] != name}
        return [name for name in names]

    def container(self, name, *, confirmed=True):
        return self.record(_CONTAINER, name, confirmed=confirmed)

    def volume(self, name, *, confirmed=True):
        return self.record(_VOLUME, name, confirmed=confirmed)

    def network(self, name, *, confirmed=True):
        return self.record(_NETWORK, name, confirmed=confirmed)

    def protect(self, *names):
        """Mark already-recorded resources as deliberately retained.

        A launch site may keep a resource on purpose (a budget ledger that must
        survive a failed provisioning, a stopped container kept as evidence).
        Protected resources are skipped by :meth:`teardown` and still count as
        owned for accounting.  They are not removed on any exit path.
        """
        for name in names:
            if name:
                self._protected.add(name)

    # ---- teardown -------------------------------------------------------
    def teardown(self):
        """Remove every recorded, unprotected resource, best effort, once.

        Returns ``[(kind, name, ok), ...]`` in removal order.  Never raises for
        a failing or already-absent resource, so callers can call this from a
        ``finally``/``except`` block without masking the original exception.
        """
        if self._closed:
            return []
        self._closed = True
        results = []
        for kind, name in reversed(self._created):
            if name in self._protected or (kind, name) in self._pending:
                # Retained by policy, or never proven to be ours (a `volume
                # create` that matched a pre-existing volume).
                results.append((kind, name, None))
                continue
            try:
                result = self._runner([self.docker, *_REMOVE[kind], name])
                ok = getattr(result, 'returncode', 0) == 0
            except Exception:
                ok = False
            results.append((kind, name, ok))
        self._created = []
        return results

    close = teardown

    def release(self, kind=None):
        """Transfer ownership instead of removing (a resource that IS the result).

        A launch site whose created container is the deliverable (a live worker
        bound into a durable allocation) must not delete it on return; it hands
        ownership to that binding.  Releasing records nothing for teardown and
        makes a later :meth:`teardown` for those entries a no-op.
        """
        if kind is None:
            self._created = []
            return []
        kept = []
        released = []
        for entry_kind, name in self._created:
            (released if entry_kind == kind else kept).append((entry_kind, name))
        self._created = kept
        return released

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.teardown()
        return False  # never suppress the caller's exception
