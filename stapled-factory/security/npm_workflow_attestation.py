"""Verify a narrowly supported signed npm workflow subject against pinned trust.

This checks one package and one operator-selected workflow identity. It does
not verify the registry's publish attestation, build claims or full provenance.
"""
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from urllib.parse import quote, urlsplit

MAX_METADATA=16*1024*1024
MAX_PACKAGE=512*1024*1024


def _unique(pairs):
    value={}
    for key,item in pairs:
        if key in value:raise ValueError('duplicate attestation key')
        value[key]=item
    return value


def _json(raw):
    try:return json.loads(raw,object_pairs_hook=_unique)
    except (UnicodeDecodeError,json.JSONDecodeError) as exc:raise ValueError('invalid attestation JSON') from exc


def _purl(name,version):
    part=r'[a-z0-9][a-z0-9._~-]*'
    if (not isinstance(name,str) or not re.fullmatch(r'(?:'+part+r'|@'+part+r'/'+part+r')',name) or
            not isinstance(version,str) or not re.fullmatch(r'[0-9][A-Za-z0-9.+_-]*',version)):
        raise ValueError('unsupported npm package identity')
    encoded='/'.join(quote(piece,safe='-._~') for piece in name.split('/'))
    return 'pkg:npm/'+encoded+'@'+quote(version,safe='-._~')


def _bundle_statement(bundle_bytes, *, media_types=('application/vnd.dev.sigstore.bundle.v0.3+json',)):
    if not isinstance(bundle_bytes,bytes) or len(bundle_bytes)>MAX_METADATA:
        raise ValueError('invalid attestation input')
    bundle=_json(bundle_bytes)
    if not isinstance(bundle,dict) or bundle.get('mediaType') not in media_types:
        raise ValueError('unsupported sigstore bundle')
    envelope=bundle.get('dsseEnvelope')
    if (not isinstance(envelope,dict) or envelope.get('payloadType')!='application/vnd.in-toto+json' or
            not isinstance(envelope.get('payload'),str)):
        raise ValueError('invalid attestation envelope')
    try:payload=base64.b64decode(envelope['payload'],validate=True)
    except (ValueError,binascii.Error) as exc:raise ValueError('invalid attestation payload') from exc
    if base64.b64encode(payload).decode()!=envelope['payload']:raise ValueError('noncanonical attestation payload')
    return bundle,_json(payload)


def _subject(statement,name,version,sha512):
    if not isinstance(sha512,str) or not re.fullmatch(r'[a-f0-9]{128}',sha512):
        raise ValueError('invalid attestation input')
    subjects=statement.get('subject')
    if (not isinstance(subjects,list) or len(subjects)!=1 or not isinstance(subjects[0],dict) or
            subjects[0].get('name')!=_purl(name,version) or not isinstance(subjects[0].get('digest'),dict) or
            subjects[0]['digest'].get('sha512')!=sha512):
        raise ValueError('attestation subject differs')


def validate_statement(bundle_bytes,name,version,sha512):
    """Validate the subject in the exact bundle; signature verification is separate."""
    _,statement=_bundle_statement(bundle_bytes)
    if (not isinstance(statement,dict) or statement.get('_type')!='https://in-toto.io/Statement/v1' or
            statement.get('predicateType')!='https://slsa.dev/provenance/v1'):
        raise ValueError('unsupported provenance statement')
    predicate=statement.get('predicate')
    definition=predicate.get('buildDefinition') if isinstance(predicate,dict) else None
    details=predicate.get('runDetails') if isinstance(predicate,dict) else None
    builder=details.get('builder') if isinstance(details,dict) else None
    if (not isinstance(definition,dict) or not isinstance(definition.get('buildType'),str) or
            not definition['buildType'] or not isinstance(definition.get('externalParameters'),dict) or
            not isinstance(builder,dict) or not isinstance(builder.get('id'),str) or not builder['id']):
        raise ValueError('unsupported provenance predicate')
    _subject(statement,name,version,sha512)


def _small(path,limit,*,trusted=False):
    fd=os.open(path,os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_size>limit or
                trusted and (info.st_uid not in (0,os.getuid()) or info.st_mode & 0o022)):
            raise ValueError('unsafe attestation file')
        raw=stream.read(limit+1)
    if len(raw)>limit:raise ValueError('oversized attestation file')
    return raw


def _tool(path,expected):
    if not isinstance(expected,str) or not re.fullmatch(r'[a-f0-9]{64}',expected):raise ValueError('pinned verifier digest required')
    target=Path(path).resolve(strict=True)
    fd=os.open(target,os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in (0,os.getuid()) or info.st_mode & 0o022 or
                not info.st_mode & 0o111 or hashlib.file_digest(stream,'sha256').hexdigest()!=expected):
            raise ValueError('attestation verifier differs')
    return target


def _https(value):
    if (not isinstance(value,str) or len(value)>2048 or value!=value.strip() or
            any(char in value for char in ('\r','\n','\t'))):raise ValueError('exact HTTPS identity required')
    parsed=urlsplit(value)
    if parsed.scheme!='https' or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('exact HTTPS identity required')
    return value


def _verify_cosign(package,bundle_raw,root_raw,expected_sha512,cosign,cosign_sha256,arguments,key_pem=None):
    binary=_tool(cosign,cosign_sha256)
    with tempfile.TemporaryDirectory(prefix='factory-publisher-attestation-') as tmp:
        root=Path(tmp);payload=root/'package.tgz';bundle_copy=root/'bundle.json';trust_copy=root/'trusted-root.json'
        fd=os.open(package,os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as src, payload.open('xb') as out:
            info=os.fstat(src.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size>MAX_PACKAGE:raise ValueError('unsafe package archive')
            digest=hashlib.sha512();count=0
            while chunk:=src.read(1024*1024):
                count+=len(chunk)
                if count>MAX_PACKAGE:raise ValueError('package archive too large')
                digest.update(chunk);out.write(chunk)
        if digest.hexdigest()!=expected_sha512:raise ValueError('package digest differs')
        bundle_copy.write_bytes(bundle_raw);trust_copy.write_bytes(root_raw)
        env={'PATH':'/usr/bin:/bin:/usr/sbin:/sbin','HOME':str(root)}
        args=[str(binary),'verify-blob-attestation','--trusted-root',str(trust_copy),'--bundle',str(bundle_copy)]
        if key_pem is not None:
            key_copy=root/'registry-key.pem';key_copy.write_bytes(key_pem);args.extend(['--key',str(key_copy)])
        args.extend(arguments);args.append(str(payload))
        try:subprocess.run(args,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=90,env=env)
        except (OSError,subprocess.SubprocessError) as exc:raise ValueError('attestation verification failed') from exc
        with payload.open('rb') as stream:verified_sha512=hashlib.file_digest(stream,'sha512').hexdigest()
        if (_tool(cosign,cosign_sha256)!=binary or verified_sha512!=expected_sha512 or
                bundle_copy.read_bytes()!=bundle_raw or trust_copy.read_bytes()!=root_raw or
                key_pem is not None and key_copy.read_bytes()!=key_pem):
            raise ValueError('attestation inputs changed during verification')


def verify_workflow_attestation(*,package,bundle,name,version,expected_sha512,identity,issuer,
                                trusted_root,trusted_root_sha256,cosign,cosign_sha256):
    """Verify a frozen tarball's signed subject and exact workflow identity."""
    _purl(name,version);_https(identity);_https(issuer)
    if not isinstance(expected_sha512,str) or not re.fullmatch(r'[a-f0-9]{128}',expected_sha512):
        raise ValueError('pinned package digest required')
    if not isinstance(trusted_root_sha256,str) or not re.fullmatch(r'[a-f0-9]{64}',trusted_root_sha256):
        raise ValueError('pinned trusted root required')
    bundle_raw=_small(bundle,MAX_METADATA);root_raw=_small(trusted_root,MAX_METADATA,trusted=True)
    if hashlib.sha256(root_raw).hexdigest()!=trusted_root_sha256:raise ValueError('trusted root differs')
    validate_statement(bundle_raw,name,version,expected_sha512)
    _verify_cosign(package,bundle_raw,root_raw,expected_sha512,cosign,cosign_sha256,
        ['--certificate-identity',identity,'--certificate-oidc-issuer',issuer,'--type','slsaprovenance1'])
    return {'status':'verified-signed-workflow-subject','coverage':'incomplete',
            'registry_publisher':'unknown','build_claims':'not_evaluated',
            'package':_purl(name,version),'package_sha512':expected_sha512,
            'bundle_sha256':hashlib.sha256(bundle_raw).hexdigest(),'trusted_root_sha256':trusted_root_sha256,
            'cosign_sha256':cosign_sha256,'identity':identity,'issuer':issuer}
