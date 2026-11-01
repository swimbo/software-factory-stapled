"""Validate the actual selected factory build and reconcile signed acceptance.

Run on the trusted NUC beside Docker. Keep the private session and validation
key outside worker mounts. This command never dispatches or resumes a worker.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import urllib.parse
import urllib.request

SPINE = Path(__file__).resolve().parents[2] / 'spine'
sys.path.insert(0, str(SPINE))
from prebuilt_validation import validate_prebuilt
from validation_session import validate_session


def post(ingress, unit, method, value):
    route = ingress + '/WorkUnit/' + urllib.parse.quote(unit, safe='') + '/' + method
    request = urllib.request.Request(route, json.dumps(value).encode(),
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('repo', 'revision', 'baseline', 'scenarios', 'unit', 'attempt', 'key-file',
                 'prebuilt-image', 'source-archive', 'diff-file', 'build-manifest',
                 'build-signature', 'session-dir'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--entrypoint', help=argparse.SUPPRESS)
    parser.add_argument('--runtime-image', help=argparse.SUPPRESS)
    parser.add_argument('--ingress', default='http://127.0.0.1:18080')
    parser.add_argument('--observe-only', action='store_true')
    args = parser.parse_args(argv)
    address = urllib.parse.urlsplit(args.ingress)
    if (address.scheme != 'http' or address.hostname != '127.0.0.1' or
            address.username or address.password or address.path or address.query or
            address.fragment or address.port is None):
        raise ValueError('private loopback Restate ingress with explicit port required')
    keyfile = Path(args.key_file)
    st = keyfile.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise ValueError('owner-only validation key required')
    key = keyfile.read_bytes()
    if not 32 <= len(key) <= 4096:
        raise ValueError('validation key must contain 32 to 4096 bytes')

    def digest(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    binding = dict(unit_id=args.unit, attempt_id=args.attempt, git_revision=args.revision,
                   baseline=args.baseline, artifact_digest=args.prebuilt_image,
                   source_archive_digest=digest(args.source_archive),
                   scenario_digest=digest(args.scenarios),
                   build_manifest_digest=digest(args.build_manifest))
    directory = Path(args.session_dir)

    def evaluate():
        return validate_prebuilt(args.source_archive, args.revision, args.prebuilt_image,
            args.scenarios, args.diff_file, args.repo, args.baseline, args.build_manifest,
            args.build_signature, os.environ['FACTORY_BUILD_APPROVAL_PUBLIC_KEY'],
            evidence_dir=directory / 'target')

    result = validate_session(directory, binding,
        lambda: post(args.ingress, args.unit, 'getState', {}), evaluate,
        lambda envelope: post(args.ingress, args.unit, 'acceptValidation', envelope),
        key, observe_only=args.observe_only)
    print(json.dumps(result, sort_keys=True))
    return result


if __name__ == '__main__':
    raise SystemExit(0 if main()['accepted'] else 1)
