"""Controlled builds sign only independently verified one-shot runner outputs."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile

import pytest

PATH=Path(__file__).with_name('build_controlled.py')
spec=importlib.util.spec_from_file_location('build_controlled_test',PATH)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def inputs(tmp_path):
    private=tmp_path/'private';private.mkdir(mode=0o700)
    repo=tmp_path/'repo';repo.mkdir()
    def git(*args):return subprocess.check_output(['git','-C',str(repo),*args]).decode().strip()
    git('init','-q');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid')
    (repo/'file').write_text('old');git('add','file');git('commit','-qm','baseline');baseline=git('rev-parse','HEAD')
    (repo/'file').write_text('new');git('commit','-qam','candidate');revision=git('rev-parse','HEAD')
    key=private/'key.pem';public=private/'public.pem'
    subprocess.run(['openssl','genpkey','-algorithm','ed25519','-out',str(key)],check=True)
    key.chmod(0o600);subprocess.run(['openssl','pkey','-in',str(key),'-pubout','-out',str(public)],check=True)
    materials=private/'materials.tar';materials.write_bytes(b'fixture inputs')
    base=private/'base';base.mkdir();config=private/'docker';config.mkdir()
    values=dict(repo=repo,revision=revision,baseline=baseline,unit='unit-1',materials_archive=materials,
        materials_sha256=hashlib.sha256(materials.read_bytes()).hexdigest(),base_layout=base,
        base_manifest='sha256:'+'a'*64,builder_name='fixture',builder_container_id='b'*64,
        builder_image_id='sha256:'+'c'*64,docker_host='unix:///tmp/test.sock',docker_config=config,
        signing_key=key,output_dir=private/'output')
    return values,public


def fake_image(path, tag, revision, source_sha):
    layer=b'image layer'; diff='sha256:'+hashlib.sha256(layer).hexdigest()
    config=json.dumps({'os':'linux','architecture':'amd64','rootfs':{'type':'layers','diff_ids':[diff]},
        'config':{'Labels':{'factory.git-revision':revision,'factory.source-archive-sha256':source_sha}}}).encode()
    manifest=json.dumps([{'Config':'config.json','RepoTags':[tag],'Layers':['layer.tar']}]).encode()
    with tarfile.open(path,'w') as tar:
        for name,raw in [('manifest.json',manifest),('config.json',config),('layer.tar',layer)]:
            item=tarfile.TarInfo(name);item.size=len(raw);tar.addfile(item,io.BytesIO(raw))
    return hashlib.sha256(path.read_bytes()).hexdigest(),'sha256:'+hashlib.sha256(config).hexdigest()


def fake_runners(monkeypatch, values, *, wrong_image=False):
    calls=[]
    def npm(**args):
        calls.append('npm');sha,config=fake_image(args['output_tar'],args['tag'],args['revision'],args['source_sha256'])
        return {'status':'isolated-npm-ci-image','coverage':'incomplete','revision':args['revision'],
            'source_archive_sha256':args['source_sha256'],'materials_archive_sha256':args['materials_sha256'],
            'lock_sha256':'d'*64,'materials_manifest_sha256':'e'*64,'base_manifest':args['base_manifest'],
            'base_config':'sha256:'+'f'*64,'builder_container_id':args['builder_container_id'],
            'builder_image_id':args['builder_image_id'],'builder_config_sha256':'1'*64,
            'recipe_sha256':hashlib.sha256(m.RECIPE).hexdigest(),'cache_recipe_sha256':hashlib.sha256(m.POPULATE).hexdigest(),
            'image_tar_sha256':'0'*64 if wrong_image else sha,'config_digest':config}
    def cargo(**args):
        calls.append('cargo');out=args['output_dir'];(out/'context').mkdir(parents=True)
        recipe=b'FROM base\n';(out/'context/Dockerfile').write_bytes(recipe)
        tag='factory-candidate-'+args['unit'].lower()+':'+args['revision']
        sha,config=fake_image(out/'image.tar',tag,args['revision'],args['source_sha256'])
        return {'status':'isolated-cargo-image-built','coverage':'incomplete','revision':args['revision'],
            'tag':tag,'source_sha256':args['source_sha256'],'cargo_sha256':args['cargo_sha256'],
            'cargo_lock_sha256':'d'*64,'base_manifest_digest':args['base_manifest'],
            'base_config_digest':'sha256:'+'f'*64,'builder_config_sha256':'1'*64,
            'recipe_sha256':hashlib.sha256(recipe).hexdigest(),'image_archive':str(out/'image.tar'),
            'image_archive_sha256':sha,'image_config_digest':config,
            'builder':{'container_id':args['builder_container_id'],'image_id':args['builder_image_id'],'network_mode':'none'}}
    monkeypatch.setattr(m,'isolated_npm_build',npm);monkeypatch.setattr(m,'build_cargo_candidate',cargo)
    return calls


@pytest.mark.parametrize('profile',['npm','cargo'])
def test_signs_exact_runner_output_and_source(tmp_path,monkeypatch,profile):
    values,public=inputs(tmp_path);calls=fake_runners(monkeypatch,values)
    manifest=m.build_controlled(profile=profile,**values);out=values['output_dir']
    assert calls == [profile] and manifest['schema'] == 'factory.build.v2'
    assert manifest['controlled']['coverage'] == 'incomplete'
    assert manifest['controlled']['profile'] == profile
    assert manifest['archive_sha256'] == hashlib.sha256((out/'source.tar').read_bytes()).hexdigest()
    assert manifest['image_tar_sha256'] == hashlib.sha256((out/'image.tar').read_bytes()).hexdigest()
    assert manifest['dockerfile_sha256'] == hashlib.sha256((out/'Dockerfile').read_bytes()).hexdigest()
    assert json.loads((out/'build.json').read_text()) == manifest
    subprocess.run(['openssl','pkeyutl','-verify','-pubin','-inkey',str(public),'-rawin',
        '-in',str(out/'build.json'),'-sigfile',str(out/'build.sig')],check=True,capture_output=True)


def test_mismatched_image_preserves_failed_original_and_refuses_replay(tmp_path,monkeypatch):
    values,_=inputs(tmp_path);calls=fake_runners(monkeypatch,values,wrong_image=True)
    with pytest.raises(ValueError,match='runner image binding'):
        m.build_controlled(profile='npm',**values)
    out=values['output_dir'];assert (out/'build-intent.json').exists() and (out/'image.tar').exists()
    assert not (out/'build.sig').exists()
    with pytest.raises(FileExistsError):m.build_controlled(profile='npm',**values)
    assert calls == ['npm']


def test_reject_signing_key_in_candidate_before_build(tmp_path, monkeypatch):
    values,_=inputs(tmp_path);calls=fake_runners(monkeypatch,values)
    inside=values['repo']/'key.pem';inside.write_bytes(values['signing_key'].read_bytes());inside.chmod(0o600)
    values['signing_key']=inside
    with pytest.raises(ValueError,match='outside build'):
        m.build_controlled(profile='npm',**values)
    assert not values['output_dir'].exists() and calls == []
