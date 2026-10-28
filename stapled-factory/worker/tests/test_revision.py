import subprocess
import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from revision import verify_revision


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.DEVNULL).decode().strip()


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q')
    git(repo, 'config', 'user.email', 'test@example.invalid')
    git(repo, 'config', 'user.name', 'Test')
    (repo / 'tracked').write_text('base')
    git(repo, 'add', 'tracked')
    git(repo, 'commit', '-qm', 'base')
    return repo


def commit_change(repo):
    (repo / 'tracked').write_text('changed')
    git(repo, 'add', 'tracked')
    git(repo, 'commit', '-qm', 'change')
    return git(repo, 'rev-parse', 'HEAD')


def test_clean_committed_descendant_required(repo):
    base = git(repo, 'rev-parse', 'HEAD')
    assert verify_revision(str(repo), base) is None
    head = commit_change(repo)
    assert verify_revision(str(repo), base) == head
    (repo / 'new').write_text('untracked')
    assert verify_revision(str(repo), base) is None
    (repo / 'new').unlink()
    (repo / 'tracked').write_text('dirty')
    assert verify_revision(str(repo), base) is None


def test_nonancestor_and_invalid_input_fail_closed(repo):
    base = git(repo, 'rev-parse', 'HEAD')
    changed = commit_change(repo)
    assert verify_revision(str(repo), changed) is None
    git(repo, 'checkout', '--detach', '-q', base)
    assert verify_revision(str(repo), changed) is None
    assert verify_revision(str(repo), 'HEAD') is None
    assert verify_revision(str(repo / 'missing'), base) is None
    sub = repo / 'sub'
    sub.mkdir()
    assert verify_revision(str(sub), base) is None
