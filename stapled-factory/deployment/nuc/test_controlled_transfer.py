"""Controlled materials bind to a signed original and a frozen verifier release."""
import hashlib
import base64
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import pytest

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE/'security'))
sys.path.insert(0, str(BASE/'security/tests'))
from controlled_cargo import freeze_cargo
from controlled_npm import freeze_npm
from test_controlled_cargo import fixture as cargo_inputs
from test_controlled_npm import inputs as npm_inputs

spec = importlib.util.spec_from_file_location('controlled_nuc_fixture', BASE/'operator/nuc.py')
nuc = importlib.util.module_from_spec(spec);spec.loader.exec_module(nuc)


def fixture(tmp_path, monkeypatch, profile, registry=False):
    root = tmp_path/'control';root.mkdir(mode=0o700)
    monkeypatch.setattr(nuc, 'ROOT', root)
    release = root/'releases/frozen';security = release/'stapled-factory/security';security.mkdir(parents=True)
    files = {}
    modules = nuc.MATERIAL_MODULES + (('stapled-factory/security/npm_registry_policy.py',) if registry else ())
    for name in modules:
        source = BASE.parent/name; dest = release/name
        shutil.copyfile(source, dest);files[name] = hashlib.sha256(dest.read_bytes()).hexdigest()
    (release/'release-manifest.json').write_text(json.dumps({'files': files}))
    monkeypatch.setattr(nuc, 'release', lambda: nuc.verify_release(release))
    monkeypatch.setattr(nuc, 'post', lambda route, body: {'stage': 'needs_validation',
        'build': {'status':'completed', 'attempt_id':'unit-1:0', 'git_revision':'a'*40}})
    unit, revision, baseline = 'unit-1', 'a'*40, 'b'*40
    approval = root/'approvals'/unit;approval.mkdir(parents=True)
    (approval/'result.json').write_text(json.dumps({'revision': revision, 'attempt_id': unit+':0', 'binding_restored': True}))
    (approval/'operator.json').write_text(json.dumps({'baseline':baseline}))
    material_root = tmp_path/'material-fixture';material_root.mkdir()
    if profile == 'npm':
        lock, store, out = npm_inputs(material_root)
        policy = None
        if registry:
            private=tmp_path/'registry-key';public=tmp_path/'registry-key.der';signature=tmp_path/'registry.sig'
            subprocess.run(['openssl','ecparam','-name','prime256v1','-genkey','-noout','-out',str(private)],check=True,capture_output=True)
            subprocess.run(['openssl','pkey','-in',str(private),'-pubout','-outform','DER','-out',str(public)],check=True,capture_output=True)
            key=public.read_bytes()
            from npm_materials import registry_key_fingerprint
            keyid=registry_key_fingerprint(key);policy={keyid:base64.b64encode(key).decode()}
            package=json.loads(lock)['packages']['node_modules/example']
            subprocess.run(['openssl','dgst','-sha256','-sign',str(private),'-out',str(signature)],
                input=('example@'+package['version']+':'+package['integrity']).encode(),check=True,capture_output=True)
            evidence={'node_modules/example':{'keyid':keyid,'sig':base64.b64encode(signature.read_bytes()).decode()}}
            report=freeze_npm(lock,store,out,registry_evidence=evidence,registry_keys={keyid:key})
        else:report = freeze_npm(lock, store, out)
        material = out
        lock_name = 'package-lock.json'
    else:
        lock, store, vendor, _ = cargo_inputs(material_root)
        report = freeze_cargo(lock, store, vendor, material_root/'out');material = Path(report['archive'])
        lock_name = 'Cargo.lock'
    stage = tmp_path/'stage';stage.mkdir()
    with tarfile.open(stage/'source.tar', 'w') as tar:
        item = tarfile.TarInfo(lock_name);item.size = len(lock);tar.addfile(item, io.BytesIO(lock))
    (stage/'diff').write_bytes(b'fixture diff');(stage/'Dockerfile').write_bytes(b'fixture recipe')
    (stage/'result.bundle').write_bytes(b'fixture bundle');shutil.copyfile(material, stage/'materials.tar')
    source_sha = hashlib.sha256((stage/'source.tar').read_bytes()).hexdigest()
    recipe_sha = hashlib.sha256((stage/'Dockerfile').read_bytes()).hexdigest()
    cmd = ['node', '/workspace/server.js'] if profile == 'npm' else ['/factory/source/target/release/app']
    config = json.dumps({'os':'linux','architecture':'amd64','config':{'Labels':{
        'factory.git-revision':revision,'factory.source-archive-sha256':source_sha},'Cmd':cmd}}).encode()
    tag = 'factory-candidate-unit-1:'+revision
    image_manifest = json.dumps([{'Config':'config.json','RepoTags':[tag],'Layers':[]}]).encode()
    with tarfile.open(stage/'image.tar', 'w') as tar:
        for name, raw in [('manifest.json', image_manifest),('config.json',config)]:
            item = tarfile.TarInfo(name);item.size=len(raw);tar.addfile(item,io.BytesIO(raw))
    controlled = {'profile':profile, 'coverage':'incomplete', 'source_sha256':source_sha,
        'materials_sha256':report['archive_sha256'], 'lock_sha256':report['lock_sha256'],
        'base_manifest':'sha256:'+'c'*64, 'base_config':'sha256:'+'d'*64,
        'builder_container_id':'e'*64, 'builder_image_id':'sha256:'+'f'*64,
        'builder_config_sha256':'1'*64, 'recipe_sha256':recipe_sha, 'docker_bin_sha256':'3'*64,
        'buildx_bin_sha256':'4'*64, 'unit':unit, 'tag':tag}
    if profile == 'npm':
        controlled.update(materials_manifest_sha256=report['manifest_sha256'], cache_recipe_sha256='2'*64, npm_entrypoint='server.js')
        if registry:controlled.update(registry_policy=policy,registry_evidence_sha256=report['registry_evidence_sha256'])
    else:controlled['cargo_binary']='app'
    binding = {'schema':'factory.build.v2','revision':revision,'baseline':baseline,'archive_sha256':source_sha,
        'diff_sha256':hashlib.sha256((stage/'diff').read_bytes()).hexdigest(),'dockerfile_sha256':recipe_sha,
        'config_digest':'sha256:'+hashlib.sha256(config).hexdigest(),
        'image_tar_sha256':hashlib.sha256((stage/'image.tar').read_bytes()).hexdigest(), 'controlled':controlled}
    key=tmp_path/'key'; public=root/'operator-build-public.pem'
    subprocess.run(['openssl','genpkey','-algorithm','ed25519','-out',str(key)],check=True,capture_output=True)
    subprocess.run(['openssl','pkey','-in',str(key),'-pubout','-out',str(public)],check=True,capture_output=True)
    def sign(directory, value):
        (directory/'build.json').write_text(json.dumps(value))
        subprocess.run(['openssl','pkeyutl','-sign','-inkey',str(key),'-rawin','-in',str(directory/'build.json'),
                        '-out',str(directory/'build.sig')],check=True,capture_output=True)
    sign(stage,binding)
    names = ('image.tar','source.tar','diff','Dockerfile','build.json','build.sig','result.bundle','materials.tar')
    intent = {'unit':unit,'revision':revision,'baseline':baseline,'attempt_id':unit+':0',
              'files':{name:hashlib.sha256((stage/name).read_bytes()).hexdigest() for name in names}}
    preflight = nuc.main('validation_transfer_preflight', {'unit':unit})
    assert (preflight['unit'], preflight['revision'], preflight['baseline'], preflight['attempt_id']) == (unit, revision, baseline, unit+':0')
    intent['material_verifier'] = preflight['material_verifier']
    nuc.main('validation_transfer_begin',intent)
    target = approval/'validation-inputs'
    for name in names:shutil.copyfile(stage/name,target/name)
    return {'target':target,'unit':unit,'binding':binding,'sign':sign,'release':release,
            'profile':profile,'stage':stage,'root':root}


def refresh_intent(target, *names):
    path = target/'transfer-intent.json';intent = json.loads(path.read_text())
    for name in names:intent['files'][name] = hashlib.sha256((target/name).read_bytes()).hexdigest()
    path.write_text(json.dumps(intent))


def test_signed_registry_policy_and_material_are_verified_on_transfer(tmp_path, monkeypatch):
    data=fixture(tmp_path,monkeypatch,'npm',registry=True)
    result=nuc.main('validation_verify_transfer',{'unit':data['unit']})
    assert result['verified'] is True
    assert result['controlled_registry_evidence_sha256']==data['binding']['controlled']['registry_evidence_sha256']
    target=data['target'];intent=json.loads((target/'transfer-intent.json').read_text())
    altered=json.loads(json.dumps(data['binding']))
    altered['controlled']['registry_evidence_sha256']='0'*64
    with pytest.raises(ValueError,match='materials differ'):
        nuc.verify_controlled_materials(target,intent,altered,data['unit'])


def test_attestation_verifier_binds_signed_policy_and_independent_local_tools(tmp_path, monkeypatch):
    from types import SimpleNamespace
    data=fixture(tmp_path,monkeypatch,'npm'); release=data['release']
    manifest_path=release/'release-manifest.json'; manifest=json.loads(manifest_path.read_text())
    for name in nuc.ATTESTATION_MODULES:
        dest=release/name;shutil.copyfile(BASE.parent/name,dest)
        manifest['files'][name]=hashlib.sha256(dest.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    pin=nuc.material_verifier_release(release,registry_policy=True,attestations=True)
    policy={'node_modules/example':{'workflow':{'identity':'https://example.invalid/workflow','issuer':'https://example.invalid'}}}
    policy_digest=hashlib.sha256(json.dumps(policy,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    att={'bundle_sha256':'a'*64,'policy_sha256':policy_digest,'trusted_root_sha256':'b'*64,'cosign_sha256':'c'*64,'policy':policy}
    controlled={**data['binding']['controlled'],'npm_attestation':att}
    trust={'trusted_root':'/pinned/root','trusted_root_sha256':'b'*64,'cosign':'/pinned/cosign','cosign_sha256':'d'*64}
    def run(args,**kwargs):
        request=json.loads(kwargs['input'])
        assert request['cosign_sha256']=='d'*64 and request['policy']==policy
        return SimpleNamespace(stdout=json.dumps({'status':'verified-selected-frozen-npm-attestations','coverage':'incomplete',
            'archive_sha256':controlled['materials_sha256'],'lock_sha256':controlled['lock_sha256'],
            'manifest_sha256':controlled['materials_manifest_sha256'],'attestation_bundle_sha256':'a'*64,
            'attestation_policy_sha256':policy_digest}))
    monkeypatch.setattr(nuc.subprocess,'run',run)
    assert nuc.verify_controlled_npm_attestation(data['target'],controlled,pin,trust)['coverage']=='incomplete'
    with pytest.raises(ValueError,match='policy differs'):
        nuc.verify_controlled_npm_attestation(data['target'],{**controlled,'npm_attestation':{**att,'policy':{}}},pin,trust)
    with pytest.raises(ValueError,match='trust'):
        nuc.verify_controlled_npm_attestation(data['target'],controlled,pin,{**trust,'trusted_root_sha256':'0'*64})
    (release/nuc.ATTESTATION_MODULES[0]).write_text('changed')
    with pytest.raises(ValueError,match='changed'):
        nuc.verify_controlled_npm_attestation(data['target'],controlled,pin,trust)


@pytest.mark.parametrize('profile', ['npm','cargo'])
def test_controlled_transfer_verifies_signed_material_and_original_release(tmp_path, monkeypatch, profile):
    data=fixture(tmp_path,monkeypatch,profile)
    status=nuc.main('validation_transfer_status',{'unit':data['unit']})
    assert status['complete'] is True and status['files']['materials.tar'] is True
    assert status['material_verifier']['release'] == str(data['release'])
    result=nuc.main('validation_verify_transfer',{'unit':data['unit']})
    assert result['verified'] is True and result['loaded'] is False
    assert result['controlled_profile'] == profile
    assert result['controlled_materials_sha256'] == data['binding']['controlled']['materials_sha256']
    assert result['controlled_lock_sha256'] == data['binding']['controlled']['lock_sha256']
    assert result['material_verifier']['release'] == str(data['release'])
    with pytest.raises(FileExistsError):
        nuc.main('validation_transfer_begin',json.loads((data['target']/'transfer-intent.json').read_text()))


@pytest.mark.parametrize('kind', ['missing','changed','profile','signature','legacy-extra','lock','release','command','entrypoint'])
def test_controlled_transfer_fails_closed_without_loading(tmp_path, monkeypatch, kind):
    data=fixture(tmp_path,monkeypatch,'npm');target=data['target'];binding=data['binding'];unit=data['unit']
    if kind == 'missing':(target/'materials.tar').unlink()
    elif kind == 'changed':
        with (target/'materials.tar').open('ab') as stream:stream.write(b'changed')
        refresh_intent(target,'materials.tar')
    elif kind == 'signature':
        (target/'build.sig').write_bytes(b'forged');refresh_intent(target,'build.sig')
    elif kind == 'release':
        with (data['release']/'stapled-factory/security/controlled_npm.py').open('a') as stream:stream.write('\n# changed')
    elif kind in ('command', 'entrypoint'):
        with tarfile.open(target/'image.tar', 'r:') as tar:
            manifest=tar.extractfile('manifest.json').read(); config=json.load(tar.extractfile('config.json'))
        if kind == 'command':config['config']['Cmd']=['node','/workspace/wrong.js']
        else:config['config']['Entrypoint']=['/bin/sh','-c']
        raw=json.dumps(config).encode()
        with tarfile.open(target/'image.tar', 'w') as tar:
            for name, content in [('manifest.json',manifest),('config.json',raw)]:
                item=tarfile.TarInfo(name);item.size=len(content);tar.addfile(item,io.BytesIO(content))
        binding['config_digest']='sha256:'+hashlib.sha256(raw).hexdigest()
        binding['image_tar_sha256']=hashlib.sha256((target/'image.tar').read_bytes()).hexdigest()
        data['sign'](target,binding);refresh_intent(target,'image.tar','build.json','build.sig')
    else:
        if kind == 'profile':binding['controlled']['profile']='unsupported'
        elif kind == 'legacy-extra':binding.pop('controlled')
        else:binding['controlled']['lock_sha256']='0'*64
        data['sign'](target,binding);refresh_intent(target,'build.json','build.sig')
    with pytest.raises((ValueError, subprocess.CalledProcessError, FileNotFoundError)):
        nuc.main('validation_verify_transfer',{'unit':unit})
    assert not (target/'load-intent.json').exists()


def test_signed_controlled_build_without_original_material_intent_is_rejected(tmp_path, monkeypatch):
    data=fixture(tmp_path,monkeypatch,'cargo');target=data['target']
    path=target/'transfer-intent.json';intent=json.loads(path.read_text())
    intent['files'].pop('materials.tar');intent.pop('material_verifier');path.write_text(json.dumps(intent))
    with pytest.raises(ValueError, match='materials missing'):
        nuc.main('validation_verify_transfer',{'unit':data['unit']})
    assert not (target/'load-intent.json').exists()


def test_controlled_begin_retains_preflight_release_after_active_release_changes(tmp_path, monkeypatch):
    data=fixture(tmp_path,monkeypatch,'npm');target=data['target'];release=data['release']
    intent=json.loads((target/'transfer-intent.json').read_text())
    shutil.rmtree(target)
    other=data['root']/'releases/new-active';shutil.copytree(release,other)
    manifest=other/'release-manifest.json';record=json.loads(manifest.read_text())
    record['other-release'] = True;manifest.write_text(json.dumps(record))
    monkeypatch.setattr(nuc,'release',lambda: nuc.verify_release(other))
    assert nuc.main('validation_transfer_begin',intent) == {'created':True}
    assert json.loads((target/'transfer-intent.json').read_text())['material_verifier'] == intent['material_verifier']


def test_controlled_begin_rejects_mutated_pinned_release_and_preflight_requires_modules(tmp_path, monkeypatch):
    data=fixture(tmp_path,monkeypatch,'npm');target=data['target'];release=data['release']
    intent=json.loads((target/'transfer-intent.json').read_text())
    shutil.rmtree(target)
    manifest=release/'release-manifest.json';record=json.loads(manifest.read_text())
    record['changed'] = True;manifest.write_text(json.dumps(record))
    with pytest.raises(ValueError, match='original material verifier release changed'):
        nuc.main('validation_transfer_begin',intent)
    assert not target.exists()
    record['files'].pop(nuc.MATERIAL_MODULES[0]);manifest.write_text(json.dumps(record))
    with pytest.raises(ValueError, match='lacks material verifier modules'):
        nuc.main('validation_transfer_preflight',{'unit':data['unit']})
    assert not target.exists()


def test_dispatch_status_and_missing_observation_are_read_only(tmp_path, monkeypatch):
    root=tmp_path/'control';root.mkdir();monkeypatch.setattr(nuc,'ROOT',root)
    approval=root/'approvals/unit-1';approval.mkdir(parents=True)
    lock=root/'operator.lock';lock.write_bytes(b'')
    monkeypatch.setattr(nuc,'post',lambda *a, **kw: pytest.fail('observation dispatched a request'))
    monkeypatch.setattr(nuc,'release',lambda: pytest.fail('observation attempted binding'))
    assert nuc.main('dispatch_status',{'unit':'unit-1'}) == {'intent_exists':False,'result_exists':False}
    assert nuc.main('run',{'unit':'unit-1','observe_only':True}) == {
        'status':'unresolved','reason':'original dispatch intent not established'}
    assert set(p.name for p in approval.iterdir()) == set()
    assert not (root/'operator-active.json').exists()
    assert lock.read_bytes() == b''
    (approval/'dispatch-intent.json').write_text('{}')
    (approval/'result.json').write_text('{}')
    assert nuc.main('dispatch_status',{'unit':'unit-1'}) == {'intent_exists':True,'result_exists':True}
    lock.unlink()
    assert nuc.main('run',{'unit':'unit-1','observe_only':True}) == {
        'status':'unresolved','reason':'operator lock unavailable for read-only observation'}
    assert not lock.exists()


def test_observation_never_recreates_or_cleans_active_binding(tmp_path, monkeypatch):
    root=tmp_path/'control';root.mkdir();monkeypatch.setattr(nuc,'ROOT',root)
    approval=root/'approvals/unit-1';approval.mkdir(parents=True)
    (root/'operator.lock').write_bytes(b'')
    (approval/'dispatch-intent.json').write_text('{}')
    monkeypatch.setattr(nuc,'post',lambda *a, **kw: pytest.fail('observation called runtime'))
    monkeypatch.setattr(nuc,'release',lambda: pytest.fail('observation rebound service'))
    assert nuc.main('run',{'unit':'unit-1','observe_only':True}) == {
        'status':'unresolved','reason':'original active binding unavailable for read-only observation'}
    assert not (root/'operator-active.json').exists()
    assert not (approval/'previous.service').exists()
    result={'binding_restored':True,'revision':'a'*40}
    (approval/'result.json').write_text(json.dumps(result))
    assert nuc.main('run',{'unit':'unit-1','observe_only':True}) == result
    marker=root/'operator-active.json';marker.write_text('{"unit":"unit-1"}')
    assert nuc.main('run',{'unit':'unit-1','observe_only':True}) == {
        'status':'unresolved','reason':'restored result has an active marker requiring reconciliation'}
    assert marker.read_text() == '{"unit":"unit-1"}'
