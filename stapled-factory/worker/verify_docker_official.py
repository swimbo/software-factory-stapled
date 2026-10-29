"""Recheck pinned Docker Official Images publication metadata over GitHub HTTPS.

This is provider-authenticated evidence, not a portable signature verification.
"""
import base64
import hashlib
import json
import re
import subprocess

INDEX = 'sha256:f9fa7f851e38bfb19c9de3afbc4b86ae7176ea7aaf94535c31df5458d5849457'
INFO_REV = 'f33f60f13d5546d9b944e228248a6563be4986a2'
OFFICIAL_REV = '1d9e44084e553b7f77c58a1a775535092116d22c'
SOURCE_REV = 'c859f2b9e567f72c94e00c969f916d3f92ae52a7'
INFO_PATH = 'repos/python/remote/3.11-slim.md'


def inspect(info, policy):
    if f'docker pull python@{INDEX}' not in info.split('\n### ')[0]:
        raise ValueError('official index substitution')
    required = ('Tags: 3.11.15-slim-trixie, 3.11-slim-trixie, 3.11.15-slim, 3.11-slim',
                f'GitCommit: {SOURCE_REV}', 'Directory: 3.11/slim-trixie')
    if not any(all(item in section for item in required) for section in policy.split('\n\n')):
        raise ValueError('official source mapping substitution')
    platforms = {}
    for section in info.split('\n### ')[1:]:
        name = section.splitlines()[0]
        if ' - linux;' not in name:
            continue
        manifest = re.search(r'docker pull python@(sha256:[0-9a-f]{64})', section)
        config = re.search(r'Image ID: `(sha256:[0-9a-f]{64})`', section)
        if not manifest or not config or name in platforms:
            raise ValueError('invalid platform mapping')
        platforms[name] = {'manifest': manifest[1], 'image_config': config[1]}
    if not any('linux; amd64' in name for name in platforms):
        raise ValueError('required platform missing')
    return {'image': 'python@' + INDEX, 'evidence_type': 'authenticated-docker-library-repo-info',
            'publisher_signature_verified': False, 'metadata_repository': 'docker-library/repo-info',
            'metadata_revision': INFO_REV, 'metadata_path': INFO_PATH,
            'metadata_sha256': hashlib.sha256(info.encode()).hexdigest(), 'platforms': platforms,
            'source_repository': 'https://github.com/docker-library/python',
            'source_revision': SOURCE_REV, 'source_directory': '3.11/slim-trixie',
            'official_images_revision': OFFICIAL_REV,
            'official_images_sha256': hashlib.sha256(policy.encode()).hexdigest()}


def verify(*, runner=subprocess.run):
    def fetch(repo, path, revision):
        raw = runner(['gh', 'api', f'repos/{repo}/contents/{path}?ref={revision}'],
                     capture_output=True, text=True, timeout=60, check=True).stdout
        return base64.b64decode(json.loads(raw)['content']).decode()
    return inspect(fetch('docker-library/repo-info', INFO_PATH, INFO_REV),
                   fetch('docker-library/official-images', 'library/python', OFFICIAL_REV))


if __name__ == '__main__':
    print(json.dumps(verify(), indent=2))
