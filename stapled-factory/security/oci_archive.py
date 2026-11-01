"""Verify a pinned OCI image archive's descriptor graph, without extracting it.

This checks content addressing at the instant of inspection; it does not attest
who built the image, what went into it, or prevent later archive replacement.
"""
import hashlib
import json
import re
import tarfile

MAX_MEMBERS = 100000
MAX_JSON = 8 * 1024 * 1024
MAX_BLOB = 4 * 1024 * 1024 * 1024
MAX_ARCHIVE = 16 * 1024 * 1024 * 1024
INDEX = 'application/vnd.oci.image.index.v1+json'
MANIFEST = 'application/vnd.oci.image.manifest.v1+json'


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate OCI JSON key')
        result[key] = value
    return result


def _json(data):
    try:
        value = json.loads(data, object_pairs_hook=_unique)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('invalid OCI JSON') from exc
    if not isinstance(value, dict):
        raise ValueError('OCI JSON object required')
    return value


def verify_archive(path, expected_digest):
    """Verify all referenced blobs, returning the unique linux/amd64 config digest."""
    if not isinstance(expected_digest, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', expected_digest):
        raise ValueError('pinned OCI digest required')
    with open(path, 'rb') as source:
        archive_hash = hashlib.sha256()
        total = 0
        while chunk := source.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_ARCHIVE:
                raise ValueError('OCI archive too large')
            archive_hash.update(chunk)
        source.seek(0)
        with tarfile.open(fileobj=source, mode='r:') as tar:
            members = {}
            for count, member in enumerate(tar, 1):
                if count > MAX_MEMBERS or member.name in members:
                    raise ValueError('duplicate or excessive OCI archive members')
                if member.isdir():
                    continue
                if not member.isfile() or member.size > MAX_BLOB:
                    raise ValueError('unsafe OCI archive member')
                if member.name not in ('index.json', 'oci-layout') and not re.fullmatch(r'blobs/sha256/[0-9a-f]{64}', member.name):
                    raise ValueError('unexpected OCI archive member')
                members[member.name] = member

            def read_json(name):
                member = members.get(name)
                if member is None or member.size > MAX_JSON:
                    raise ValueError('missing or oversized OCI JSON')
                return _json(tar.extractfile(member).read())

            if read_json('oci-layout').get('imageLayoutVersion') != '1.0.0':
                raise ValueError('unsupported OCI layout')
            root = read_json('index.json')
            roots = root.get('manifests')
            if root.get('schemaVersion') != 2 or not isinstance(roots, list) or len(roots) != 1 or not isinstance(roots[0], dict) or roots[0].get('digest') != expected_digest or roots[0].get('mediaType') != INDEX:
                raise ValueError('OCI root digest differs')
            seen = {}
            platforms = []

            def visit(descriptor, platform=None, depth=0):
                if depth > 8:
                    raise ValueError('OCI descriptor graph too deep')
                if not isinstance(descriptor, dict):
                    raise ValueError('invalid OCI descriptor')
                digest, size, media = descriptor.get('digest'), descriptor.get('size'), descriptor.get('mediaType')
                if not isinstance(digest, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest) or type(size) is not int or size < 0 or size > MAX_BLOB or not isinstance(media, str):
                    raise ValueError('invalid OCI descriptor')
                name = 'blobs/sha256/' + digest[7:]
                member = members.get(name)
                if member is None or member.size != size:
                    raise ValueError('missing or wrong-sized OCI blob')
                # Repeated references are valid, but must always match descriptor size.
                if digest in seen:
                    if seen[digest] != media:
                        raise ValueError('conflicting OCI media types')
                    if isinstance(platform, dict) and platform.get('os') == 'linux' and platform.get('architecture') == 'amd64':
                        raise ValueError('duplicate linux/amd64 descriptor')
                    return
                seen[digest] = media
                stream = tar.extractfile(member)
                h = hashlib.sha256()
                while chunk := stream.read(1024 * 1024):
                    h.update(chunk)
                if h.hexdigest() != digest[7:]:
                    raise ValueError('OCI blob digest differs')
                if media not in (INDEX, MANIFEST):
                    return
                data = read_json(name)
                if data.get('schemaVersion') != 2 or data.get('mediaType') != media:
                    raise ValueError('unsupported OCI document')
                if media == INDEX:
                    children = data.get('manifests')
                    if not isinstance(children, list) or not children:
                        raise ValueError('empty OCI index')
                    for child in children:
                        visit(child, child.get('platform') if isinstance(child, dict) else None, depth + 1)
                else:
                    config, layers = data.get('config'), data.get('layers')
                    if not isinstance(layers, list):
                        raise ValueError('invalid OCI layers')
                    if not isinstance(config, dict) or config.get('mediaType') not in ('application/vnd.oci.image.config.v1+json', 'application/vnd.oci.empty.v1+json'):
                        raise ValueError('unsupported OCI config')
                    visit(config, depth=depth + 1)
                    for layer in layers:
                        visit(layer, depth=depth + 1)
                    if isinstance(platform, dict) and platform.get('os') == 'linux' and platform.get('architecture') == 'amd64':
                        if config.get('mediaType') != 'application/vnd.oci.image.config.v1+json':
                            raise ValueError('unsupported OCI image config')
                        config_json = read_json('blobs/sha256/' + config['digest'][7:])
                        if config_json.get('os') != 'linux' or config_json.get('architecture') != 'amd64':
                            raise ValueError('OCI config platform differs')
                        platforms.append(config['digest'])

            visit(roots[0])
            if len(platforms) != 1:
                raise ValueError('exactly one linux/amd64 image required')
            return {'status': 'verified-oci-descriptor-graph', 'coverage': 'incomplete',
                    'archive_sha256': archive_hash.hexdigest(), 'index_digest': expected_digest,
                    'config_digest': platforms[0]}
