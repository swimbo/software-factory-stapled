"""Material lookup must stay within the selected digest-addressed store."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from material_store import MaterialStore


@pytest.mark.parametrize('algorithm,digest', [('sha256', '../outside'), ('sha256', 'a'*128),
                                               ('md5', 'a'*32), ('sha512', 'F'*128)])
def test_rejects_unsafe_material_names_before_open(tmp_path, algorithm, digest):
    store = tmp_path/'store';store.mkdir(mode=0o700)
    (tmp_path/'outside').write_text('not material')
    with MaterialStore(store) as material:
        with pytest.raises(ValueError, match='identity'):
            material.verify(algorithm, digest, 'fixture')
