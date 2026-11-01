import hashlib
import io
import json
import tarfile
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from oci_archive import verify_archive


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def _fixture(path, tamper=False):
    blobs = {}

    def blob(data, media):
        digest = hashlib.sha256(data).hexdigest()
        blobs[digest] = data
        return {'mediaType': media, 'digest': 'sha256:' + digest, 'size': len(data)}

    config = blob(_json({'architecture': 'amd64', 'os': 'linux'}), 'application/vnd.oci.image.config.v1+json')
    layer = blob(b'layer data', 'application/vnd.oci.image.layer.v1.tar')
    manifest = blob(_json({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json', 'config': config, 'layers': [layer]}), 'application/vnd.oci.image.manifest.v1+json')
    manifest['platform'] = {'os': 'linux', 'architecture': 'amd64'}
    index = blob(_json({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.index.v1+json', 'manifests': [manifest]}), 'application/vnd.oci.image.index.v1+json')
    entries = {'oci-layout': _json({'imageLayoutVersion': '1.0.0'}), 'index.json': _json({'schemaVersion': 2, 'manifests': [index]})}
    entries.update({'blobs/sha256/' + key: value for key, value in blobs.items()})
    if tamper:
        entries['blobs/sha256/' + layer['digest'][7:]] = b'other data'
    with tarfile.open(path, 'w') as tar:
        for name, data in entries.items():
            item = tarfile.TarInfo(name); item.size = len(data)
            tar.addfile(item, io.BytesIO(data))
    return index['digest'], config['digest']


def test_pinned_graph_and_config(tmp_path):
    archive = tmp_path / 'image.tar'
    index, config = _fixture(archive)
    result = verify_archive(archive, index)
    assert result['config_digest'] == config
    assert result['archive_sha256'] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert result['coverage'] == 'incomplete'
    with pytest.raises(ValueError, match='root digest'):
        verify_archive(archive, 'sha256:' + '0' * 64)


def test_rejects_changed_layer_with_same_size(tmp_path):
    archive = tmp_path / 'image.tar'
    index, _ = _fixture(archive, tamper=True)
    with pytest.raises(ValueError, match='blob digest'):
        verify_archive(archive, index)
