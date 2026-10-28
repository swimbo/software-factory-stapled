"""Fail-closed upstream keyless signature verification with explicit trust policy.

The operator must establish publisher identity and issuer independently. Registry
artifact discovery alone does not authenticate a publisher or build provenance.
"""
import argparse
import json
import re
import subprocess


def verify(image, identity, issuer, *, runner=subprocess.run):
    if not re.fullmatch(r'[A-Za-z0-9./:_-]+@sha256:[0-9a-f]{64}', image):
        raise ValueError('immutable registry image required')
    if not identity or not issuer:
        raise ValueError('independently established publisher identity and issuer required')
    result = runner(['cosign', 'verify', '--certificate-identity', identity,
                     '--certificate-oidc-issuer', issuer, '--output', 'json', image],
                    capture_output=True, text=True, timeout=120, check=True)
    signatures = json.loads(result.stdout)
    digest = image.split('@')[1]
    if not isinstance(signatures, list) or not signatures:
        raise ValueError('no verified signatures')
    for signature in signatures:
        if signature.get('critical', {}).get('image', {}).get('docker-manifest-digest') != digest:
            raise ValueError('verified signature digest mismatch')
    return {'image': image, 'publisher_identity': identity, 'issuer': issuer,
            'verified_signatures': len(signatures), 'build_provenance_verified': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image')
    parser.add_argument('--identity', required=True)
    parser.add_argument('--issuer', required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.image, args.identity, args.issuer)))
