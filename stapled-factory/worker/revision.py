"""Verify committed worker output in a local Git worktree.

This checks Git state only; the caller must independently establish that the
path is the intended isolated worker workspace and that its contents are trusted.
"""
import re
import os
import subprocess
from pathlib import Path

_SHA = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')


def verify_revision(repo: str, baseline: str) -> str | None:
    """Return a clean, changed descendant HEAD, or None for any uncertainty."""
    if not isinstance(baseline, str) or not _SHA.fullmatch(baseline):
        return None
    try:
        path = Path(repo).resolve(strict=True)
        if not path.is_dir():
            return None
        def git(*args):
            env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
            env['GIT_CONFIG_NOSYSTEM'] = '1'
            env['GIT_CONFIG_GLOBAL'] = os.devnull
            return subprocess.run(['git', '-c', 'core.fsmonitor=false', '-C', str(path), *args], check=True, env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=15).stdout
        root = Path(git('rev-parse', '--show-toplevel').decode().strip()).resolve(strict=True)
        if root != path:
            return None
        baseline_commit = git('rev-parse', '--verify', baseline + '^{commit}').decode().strip()
        head = git('rev-parse', '--verify', 'HEAD^{commit}').decode().strip()
        if baseline_commit != baseline or not _SHA.fullmatch(head) or head == baseline:
            return None
        git('merge-base', '--is-ancestor', baseline, head)
        if git('status', '--porcelain=v1', '--untracked-files=all', '--ignore-submodules=none', '-z'):
            return None
        if git('rev-parse', '--verify', 'HEAD^{commit}').decode().strip() != head:
            return None
        return head
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError):
        return None
