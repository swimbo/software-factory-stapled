import os
from pathlib import Path
from types import SimpleNamespace
import pytest
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bounded_profile import PROFILE, validate_workspace, worker_arguments, effective_environment, validate_worker


def mounted(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    monkeypatch.setattr('bounded_profile.mounts',lambda: {str(tmp_path): {'id':'12','type':'tmpfs'}})
    monkeypatch.setattr('bounded_profile.os.statvfs',lambda _:SimpleNamespace(f_blocks=16384,f_frsize=4096))
    return tmp_path


def test_small_owned_mount_is_bound_and_shared_filesystem_refused(tmp_path,monkeypatch):
    mounted(tmp_path,monkeypatch)
    x=validate_workspace(str(tmp_path),PROFILE)
    assert x['capacity_bytes']==67108864 and x['inode']==tmp_path.stat().st_ino
    monkeypatch.setattr('bounded_profile.os.statvfs',lambda _:SimpleNamespace(f_blocks=2**30,f_frsize=4096))
    with pytest.raises(ValueError,match='capacity'):validate_workspace(str(tmp_path),PROFILE)


def test_ordinary_directory_or_symlink_cannot_claim_quota(tmp_path,monkeypatch):
    tmp_path.chmod(0o700);monkeypatch.setattr('bounded_profile.mounts',lambda:{})
    with pytest.raises(ValueError,match='mount'):validate_workspace(str(tmp_path),PROFILE)
    child=tmp_path/'link';child.symlink_to(tmp_path,target_is_directory=True)
    with pytest.raises(ValueError):validate_workspace(str(child),PROFILE)


def test_bounded_worker_requires_readonly_root_and_exact_temporary_limit():
    args=worker_arguments(PROFILE,1000)
    assert '--read-only' in args
    assert 'size=536870912' in args[args.index('--tmpfs')+1]
    env=['HOME=/tmp/old','PATH=/bin']
    expected=effective_environment(env,PROFILE)
    assert expected==['HOME=/workspace/.git/factory-home',
        'OH_CONVERSATIONS_PATH=/workspace/.git/factory-home/conversations',
        'OH_BASH_EVENTS_DIR=/workspace/.git/factory-home/bash_events',
        'OH_PERSISTENCE_DIR=/workspace/.git/factory-home/.openhands','PATH=/bin'] and env[0]=='HOME=/tmp/old'
    worker={'HostConfig':{'ReadonlyRootfs':True,'Tmpfs':{'/tmp':args[args.index('--tmpfs')+1].partition(':')[2]},'ShmSize':67108864},'Mounts':[]}
    validate_worker(worker,PROFILE,1000)
    worker['HostConfig']['ReadonlyRootfs']=False
    with pytest.raises(ValueError):validate_worker(worker,PROFILE,1000)


def test_unknown_profile_fails_and_default_does_not_change_runtime():
    with pytest.raises(ValueError):worker_arguments('unknown',1000)
    assert worker_arguments(None,1000)==[]
    assert validate_workspace('/unavailable',None) is None


def test_descendant_mount_cannot_bypass_workspace_capacity(tmp_path,monkeypatch):
    mounted(tmp_path,monkeypatch)
    monkeypatch.setattr('bounded_profile.mounts',lambda:{str(tmp_path):{'id':'12','type':'tmpfs'},str(tmp_path/'nested'):{'id':'13','type':'ext4'}})
    with pytest.raises(ValueError,match='descendant'):
        validate_workspace(str(tmp_path),PROFILE)


def test_sdk_conversation_and_event_checkpoints_use_retained_workspace():
    expected={'HOME':'/workspace/.git/factory-home',
              'OH_CONVERSATIONS_PATH':'/workspace/.git/factory-home/conversations',
              'OH_BASH_EVENTS_DIR':'/workspace/.git/factory-home/bash_events',
              'OH_PERSISTENCE_DIR':'/workspace/.git/factory-home/.openhands'}
    args=worker_arguments(PROFILE,1000)
    actual=dict(args[i+1].split('=',1) for i,v in enumerate(args) if v=='--env')
    assert actual==expected
    original=['PATH=/bin','HOME=/old','OH_CONVERSATIONS_PATH=/ephemeral']
    environment=dict(x.split('=',1) for x in effective_environment(original,PROFILE))
    assert all(environment[k]==v for k,v in expected.items())
    assert original==['PATH=/bin','HOME=/old','OH_CONVERSATIONS_PATH=/ephemeral']
    assert effective_environment(original,None)==original


def test_docker_explicit_environment_precedes_remaining_image_environment():
    image=['PATH=/bin','HOME=/tmp/old','OH_EXTRA_PYTHON_PATH=/opt/factory']
    assert effective_environment(image,PROFILE)==[
        'HOME=/workspace/.git/factory-home',
        'OH_CONVERSATIONS_PATH=/workspace/.git/factory-home/conversations',
        'OH_BASH_EVENTS_DIR=/workspace/.git/factory-home/bash_events',
        'OH_PERSISTENCE_DIR=/workspace/.git/factory-home/.openhands',
        'PATH=/bin','OH_EXTRA_PYTHON_PATH=/opt/factory']


def test_environment_order_is_irrelevant_but_duplicate_or_changed_values_fail():
    from bounded_profile import environment_matches
    expected=['HOME=/workspace/.git/factory-home','PATH=/bin']
    assert environment_matches(list(reversed(expected)),expected,PROFILE)
    assert not environment_matches(expected+expected[:1],expected,PROFILE)
    assert not environment_matches(expected+['BIFROST_API_KEY=secret'],expected,PROFILE)
    assert not environment_matches(['HOME=/tmp','PATH=/bin'],expected,PROFILE)
    assert not environment_matches(['PATH=/bin'],expected,PROFILE)
    assert not environment_matches(expected,['HOME=/tmp','HOME=/workspace/.git/factory-home','PATH=/bin'],PROFILE)
    assert not environment_matches(list(reversed(expected)),expected,None)
