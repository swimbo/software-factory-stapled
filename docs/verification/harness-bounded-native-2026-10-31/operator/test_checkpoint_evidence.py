import os
from pathlib import Path
import pytest
from checkpoint_evidence import checkpoint_evidence

def fixture(tmp_path):
    home=tmp_path/'.git/factory-home/conversations/id';home.mkdir(parents=True)
    (home/'base_state.json').write_text('{}')
    return home

def test_regular_checkpoint_is_bounded_and_hashed(tmp_path):
    fixture(tmp_path);result=checkpoint_evidence(tmp_path)
    assert result['files']==1 and result['bytes']==2 and len(result['sha256'])==64

@pytest.mark.parametrize('directory',['.git','.git/factory-home','.git/factory-home/conversations'])
def test_symlink_ancestor_refused(tmp_path,directory):
    fixture(tmp_path);p=tmp_path/directory;renamed=p.with_name(p.name+'-real');p.rename(renamed);p.symlink_to(renamed,target_is_directory=True)
    with pytest.raises((ValueError,OSError)):checkpoint_evidence(tmp_path)

def test_file_symlink_and_fifo_never_read(tmp_path):
    home=fixture(tmp_path);target=tmp_path/'private';target.write_text('host-private-fixture')
    bad=home/'bad';bad.symlink_to(target)
    with pytest.raises(ValueError):checkpoint_evidence(tmp_path)
    bad.unlink();os.mkfifo(bad)
    with pytest.raises(ValueError):checkpoint_evidence(tmp_path)

def test_hardlink_and_oversized_file_refused(tmp_path):
    home=fixture(tmp_path);os.link(home/'base_state.json',home/'hard')
    with pytest.raises(ValueError):checkpoint_evidence(tmp_path)
    (home/'hard').unlink()
    with (home/'large').open('wb') as f:f.truncate(4194305)
    with pytest.raises(ValueError):checkpoint_evidence(tmp_path)

def test_file_count_limit_refused(tmp_path):
    home=fixture(tmp_path)
    for i in range(2049):(home/str(i)).touch()
    with pytest.raises(ValueError):checkpoint_evidence(tmp_path)
