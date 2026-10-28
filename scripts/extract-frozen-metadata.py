#!/usr/bin/env python3
"""Extract PyInstaller distribution metadata without executing frozen modules.

Requires independently installed PyInstaller 6.22.3. Input is the exact server
ELF extracted from the reviewed image's docker-save archive. The image itself
must first be bound to its immutable image ID; this tool does not approve it.
"""
import argparse
import email
import hashlib
import json
from pathlib import Path, PurePosixPath
from PyInstaller.archive.readers import CArchiveReader

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('binary', type=Path)
parser.add_argument('output', type=Path)
args = parser.parse_args()
archive = CArchiveReader(str(args.binary))
args.output.mkdir(parents=True, exist_ok=False)
packages = []
for name in archive.toc:
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError('unsafe archive path')
    if not name.endswith('.dist-info/METADATA'):
        continue
    raw = archive.extract(name)
    destination = args.output / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)
    metadata = email.message_from_bytes(raw)
    if not metadata['Name'] or not metadata['Version']:
        raise ValueError('incomplete distribution metadata')
    packages.append({'name': metadata['Name'], 'version': metadata['Version'],
        'license': metadata['License-Expression'] or metadata['License'],
        'metadata_sha256': hashlib.sha256(raw).hexdigest()})
if not packages:
    raise ValueError('no distribution metadata found; audit coverage is unknown')
(args.output / 'requirements.txt').write_text(''.join(
    f'{p["name"]}=={p["version"]}\n' for p in packages))
(args.output / 'inventory.json').write_text(json.dumps({
    'binary_sha256': hashlib.sha256(args.binary.read_bytes()).hexdigest(),
    'packages': packages,
    'coverage': 'Only retained metadata; modules lacking metadata remain unknown.'
}, indent=2) + '\n')
