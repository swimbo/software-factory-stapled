import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prebuilt_validation import verify_signed_image_archive


def test_signed_archive_and_config_must_match(tmp_path):
    path = tmp_path / 'image.tar'
    config = b'{"architecture":"amd64"}'
    with tarfile.open(path, 'w') as tar:
        for name, data in [('manifest.json', b'[{"Config":"config.json"}]'), ('config.json', config)]:
            info = tarfile.TarInfo(name); info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    binding = {'schema': 'factory.build.v2', 'image_tar_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
               'config_digest': 'sha256:' + hashlib.sha256(config).hexdigest()}
    verify_signed_image_archive(binding, path)
    with pytest.raises(ValueError, match='configuration mismatch'):
        verify_signed_image_archive(dict(binding, config_digest='sha256:' + '0'*64), path)
    with path.open('ab') as stream:
        stream.write(b'tampered')
    with pytest.raises(ValueError, match='archive mismatch'):
        verify_signed_image_archive(binding, path)
    verify_signed_image_archive({}, tmp_path / 'absent-legacy-archive')
