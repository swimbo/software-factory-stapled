import os
from pathlib import Path
import subprocess
import sys

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workspace_progress import snapshot


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.DEVNULL)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path.resolve()
    git(root, 'init', '-q')
    git(root, 'config', 'user.name', 'Test')
    git(root, 'config', 'user.email', 'test@example.invalid')
    (root/'app.py').write_text('first\n')
    git(root, 'add', 'app.py')
    git(root, 'commit', '-qm', 'baseline')
    return root


def digest(root):
    result = snapshot(str(root))
    assert result['schema'] == 'factory.workspace-progress/v1'
    assert result['complete'] is True
    assert set(result) == {'schema', 'complete', 'sha256', 'files', 'bytes'}
    return result['sha256']


def test_content_progress_covers_staged_unstaged_and_untracked(repo):
    initial = digest(repo)
    (repo/'app.py').write_text('other\n')
    unstaged = digest(repo)
    assert unstaged != initial
    git(repo, 'add', 'app.py')
    staged = digest(repo)
    assert staged != unstaged
    (repo/'app.py').write_text('first\n')
    assert digest(repo) not in (initial, staged)
    git(repo, 'reset', '-q', 'HEAD', '--', 'app.py')
    assert digest(repo) == initial
    (repo/'new.py').write_text('new\n')
    assert digest(repo) != initial
    (repo/'new.py').unlink()
    assert digest(repo) == initial
    (repo/'app.py').unlink()
    assert digest(repo) != initial


def test_head_changes_and_mtime_alone_does_not(repo):
    initial = digest(repo)
    os.utime(repo/'app.py', ns=(123456789, 123456789))
    assert digest(repo) == initial
    git(repo, 'commit', '--allow-empty', '-qm', 'new head')
    assert digest(repo) != initial


def test_ignored_volatile_and_secret_files_do_not_contribute(repo):
    (repo/'.gitignore').write_text('cache/\n')
    (repo/'cache').mkdir()
    files = ['cache/data', 'trace.log', '.env', '.env.local', 'credentials.json', 'server.key', 'cert.pem']
    for name in files:
        (repo/name).write_text('sensitive first')
    git(repo, 'add', '-f', 'credentials.json', 'server.key', '.env')
    initial = digest(repo)
    for name in files:
        (repo/name).write_text('sensitive other')
    git(repo, 'add', '-f', 'credentials.json', 'server.key', '.env')
    assert digest(repo) == initial
    assert 'sensitive' not in str(snapshot(str(repo)))


@pytest.mark.parametrize('kind', ['file_symlink', 'directory_symlink', 'fifo', 'hardlink'])
def test_unsafe_worker_paths_are_unknown_without_following(repo, tmp_path, kind):
    # Keep the target outside the workspace.
    outside = repo.parent/(repo.name+'-outside')
    outside.mkdir()
    (outside/'private').write_text('host data')
    if kind == 'file_symlink':
        (repo/'unsafe').symlink_to(outside/'private')
    elif kind == 'directory_symlink':
        (repo/'unsafe').symlink_to(outside, target_is_directory=True)
    elif kind == 'fifo':
        os.mkfifo(repo/'unsafe')
    else:
        os.link(outside/'private', repo/'unsafe')
    result = snapshot(str(repo))
    assert result['complete'] is False and result['sha256'] is None
    assert 'host data' not in str(result)


def test_symlink_ancestor_and_non_repository_are_unknown(repo, tmp_path):
    link = repo.parent/(repo.name+'-link')
    link.symlink_to(repo, target_is_directory=True)
    assert snapshot(str(link))['complete'] is False
    assert snapshot(str(repo.parent))['complete'] is False


@pytest.mark.parametrize('limit', ['MAX_FILE_BYTES', 'MAX_TOTAL_BYTES', 'MAX_FILES', 'MAX_NODES', 'MAX_DEPTH'])
def test_scan_limits_are_unknown_instead_of_partial_digest(repo, monkeypatch, limit):
    import workspace_progress
    monkeypatch.setattr(workspace_progress, limit, 1)
    (repo/'nested').mkdir()
    (repo/'nested'/'another.py').write_text('more contents')
    result = snapshot(str(repo))
    assert result['complete'] is False and result['sha256'] is None


def test_git_configuration_cannot_enable_fsmonitor_execution(repo):
    marker = repo/'unexpected-command'
    hook = repo/'monitor.sh'
    hook.write_text('#!/bin/sh\ntouch '+str(marker)+'\n')
    hook.chmod(0o700)
    git(repo, 'config', 'core.fsmonitor', str(hook))
    assert snapshot(str(repo))['complete'] is True
    assert not marker.exists()


def test_untracked_filename_cannot_impersonate_an_index_entry(repo):
    path = repo/('100644 '+'0'*40+' 0\tother.py')
    path.write_text('first')
    first = digest(repo)
    path.write_text('other')
    assert digest(repo) != first


def test_worker_git_config_and_metadata_symlinks_cannot_read_host_files(repo):
    # Even an include that would block Git is not read by the private index reader.
    fifo = repo/'.git'/'blocking-config'
    os.mkfifo(fifo)
    git(repo, 'config', 'include.path', str(fifo))
    assert snapshot(str(repo))['complete'] is True
    (repo/'.git'/'index').unlink()
    (repo/'.git'/'index').symlink_to(repo/'app.py')
    assert snapshot(str(repo))['complete'] is False


def test_total_content_reads_stop_at_the_remaining_budget(repo, monkeypatch):
    import workspace_progress
    monkeypatch.setattr(workspace_progress, 'MAX_TOTAL_BYTES', 10)
    (repo/'new.py').write_text('second')
    result = snapshot(str(repo))
    assert result['complete'] is False and result['sha256'] is None
    assert result['bytes'] <= 10


@pytest.mark.parametrize('unsafe', ['oversized', 'symlink'])
def test_unsafe_ignore_input_is_rejected_before_git_parses_it(repo, monkeypatch, unsafe):
    import workspace_progress
    ignore = repo/'.gitignore'
    if unsafe == 'oversized':
        ignore.write_bytes(b'x' * (workspace_progress.MAX_FILE_BYTES + 1))
    else:
        ignore.symlink_to(repo/'app.py')
    monkeypatch.setattr(workspace_progress, '_git', lambda *a, **kw: pytest.fail('unsafe ignore reached Git'))
    assert snapshot(str(repo))['complete'] is False
