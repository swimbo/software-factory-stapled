"""Validate the operator-supplied public-key policy for npm registry evidence.

The caller authenticates and pins the policy separately. This module does not
assert the trust, historical validity or revocation status of its keys.
"""
import base64
import binascii
from npm_materials import registry_key_fingerprint


def decode_policy(policy):
    if not isinstance(policy, dict) or not 1 <= len(policy) <= 64:
        raise ValueError('bounded registry key policy required')
    decoded = {}
    for keyid, encoded in policy.items():
        if not isinstance(keyid, str) or not isinstance(encoded, str) or len(encoded) > 256:
            raise ValueError('invalid registry key policy')
        try:key = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:raise ValueError('invalid registry key policy') from exc
        if base64.b64encode(key).decode() != encoded or registry_key_fingerprint(key) != keyid:
            raise ValueError('registry key fingerprint differs')
        decoded[keyid] = key
    return decoded
