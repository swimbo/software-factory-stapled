import base64
import hashlib
import io
import json
import re
from pathlib import Path
import sys
import tarfile
import subprocess

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from controlled_npm import freeze_npm
import isolated_npm_build as isolated


def fixture(tmp_path):
    material = b'package tarball'
    integrity = base64.b64encode(hashlib.sha512(material).digest()).decode()
    store = tmp_path/'store';store.mkdir(mode=0o700)
    (store/hashlib.sha512(material).hexdigest()).write_bytes(material)
    lock = json.dumps({'lockfileVersion':3, 'packages':{'':{'name':'app'},
        'node_modules/x':{'version':'1.0.0','resolved':'https://registry.npmjs.org/x/-/x-1.0.0.tgz',
                          'integrity':'sha512-'+integrity}}}).encode()
    private = tmp_path/'private';private.mkdir(mode=0o700)
    material_tar = private/'materials.tar';frozen = freeze_npm(lock, store, material_tar)
    repo=private/'repo';repo.mkdir()
    def git(*args):
        return subprocess.check_output(['git','-C',str(repo),*args]).strip()
    git('init','-q');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid')
    (repo/'package.json').write_bytes(b'{"name":"app"}');(repo/'package-lock.json').write_bytes(lock)
    git('add','package.json','package-lock.json');git('commit','-qm','fixture')
    revision=git('rev-parse','HEAD').decode();source = private/'source.tar';source.write_bytes(git('archive',revision))
    base = private/'base';(base/'blobs/sha256').mkdir(parents=True)
    def blob(data, media):
        digest=hashlib.sha256(data).hexdigest();(base/'blobs/sha256'/digest).write_bytes(data)
        return {'mediaType':media,'digest':'sha256:'+digest,'size':len(data)}
    cfg = blob(b'{"os":"linux","architecture":"amd64"}',isolated.OCI_CONFIG)
    layer = blob(b'layer', 'application/vnd.oci.image.layer.v1.tar')
    manifest = blob(json.dumps({'schemaVersion':2,'mediaType':isolated.OCI_MANIFEST,'config':cfg,'layers':[layer]}).encode(), isolated.OCI_MANIFEST)
    (base/'index.json').write_text(json.dumps({'schemaVersion':2,'manifests':[manifest]}))
    (base/'oci-layout').write_text('{"imageLayoutVersion":"1.0.0"}')
    docker_config = private/'docker';(docker_config/'buildx/instances').mkdir(parents=True);docker_config.chmod(0o700)
    (docker_config/'config.json').write_text('{}')
    builder='factory-npm';host='unix:///tmp/docker-test.sock';flags=['--allow-insecure-entitlement=network.host']
    instance={'Name':builder,'Driver':'docker-container','Dynamic':False,'Nodes':[{'Name':builder+'0',
        'Endpoint':host,'DriverOpts':{'network':'none','image':'fixture'},'Flags':flags,'Files':None}]}
    (docker_config/'buildx/instances'/builder).write_text(json.dumps(instance))
    container_id='a'*64;image_id='sha256:'+'b'*64
    values=dict(repo=repo,source_tar=source,source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),revision=revision,
        materials_tar=material_tar,materials_sha256=frozen['archive_sha256'],base_layout=base,
        base_manifest=manifest['digest'],docker='/usr/local/bin/docker',docker_config=docker_config,docker_host=host,
        builder=builder,builder_container_id=container_id,builder_image_id=image_id,
        tag='factory-candidate-npm:'+revision,output_tar=private/'image.tar')
    metadata=[{'Id':container_id,'Image':image_id,'State':{'Running':True},
        'HostConfig':{'NetworkMode':'none','Binds':None},'Config':{'Entrypoint':['/usr/bin/buildkitd-entrypoint'],'Cmd':flags},
        'NetworkSettings':{'Networks':{'none':{}}},'Mounts':[{'Type':'volume','Destination':'/var/lib/buildkit'}]}]
    return values, metadata, flags, instance


def test_fixed_offline_build_and_input_binding(tmp_path, monkeypatch):
    values, metadata, flags, _ = fixture(tmp_path);calls=[]; real_run = isolated._run
    def run(args, env, timeout=30):
        if args[0] == '/usr/bin/git':return real_run(args, env, timeout)
        calls.append(args)
        if args[1] == 'inspect':return json.dumps(metadata).encode()
        if args[1] == 'exec':
            if args[-1] == '/proc/1/cmdline':return b'/sbin/docker-init\0--\0/usr/bin/buildkitd-entrypoint\0' + b'\0'.join(x.encode() for x in flags) + b'\0'
            if args[-1] == '/proc/1/task/1/children':return b'6 '
            if args[-1] == '/proc/6/cmdline':return b'/usr/bin/buildkitd\0' + b'\0'.join(x.encode() for x in flags) + b'\0'
            return b''
        assert args[1:3] == ['buildx','build']
        assert '--allow' not in args and args[args.index('--network')+1] == 'none' and '--no-cache' in args
        context=Path(args[-1]);assert (context/'Dockerfile').read_bytes() == isolated.RECIPE
        assert '--network=none' in isolated.RECIPE.decode()
        image = Path(args[args.index('--output')+1].split('dest=',1)[1])
        layer=b'known layer bytes';diff='sha256:'+hashlib.sha256(layer).hexdigest()
        config = json.dumps({'os':'linux','architecture':'amd64','rootfs':{'type':'layers','diff_ids':[diff]},'config':{'Labels':{
            'factory.git-revision':values['revision'],'factory.source-archive-sha256':values['source_sha256']}}}).encode()
        manifest = json.dumps([{'Config':'config.json','RepoTags':[values['tag']],'Layers':['layer.tar']}]).encode()
        with tarfile.open(image,'w') as tar:
            for name, raw in [('manifest.json',manifest),('config.json',config),('layer.tar',layer)]:
                item=tarfile.TarInfo(name);item.size=len(raw);tar.addfile(item,io.BytesIO(raw))
        return b''
    monkeypatch.setattr(isolated,'_run',run)
    result=isolated.isolated_npm_build(**values)
    assert result['coverage']=='incomplete'
    assert result['source_archive_sha256']==values['source_sha256']
    assert result['materials_archive_sha256']==values['materials_sha256']
    assert result['image_tar_sha256']==hashlib.sha256(values['output_tar'].read_bytes()).hexdigest()
    assert result['config_digest'].startswith('sha256:')
    assert sum(args[1:3]==['buildx','build'] for args in calls)==1
    # A changed layer with the original config and labels must be rejected.
    with tarfile.open(values['output_tar']) as tar:
        offset=tar.getmember('layer.tar').offset_data
    with values['output_tar'].open('r+b') as stream:
        stream.seek(offset);stream.write(b'X')
    from isolated_cargo_build import verify_docker_image_archive
    with pytest.raises(ValueError, match='layer digest differs'):
        verify_docker_image_archive(values['output_tar'],values['tag'],values['revision'],values['source_sha256'])


def test_reject_insecure_builder_before_build(tmp_path, monkeypatch):
    values, _, _, instance = fixture(tmp_path)
    instance['Nodes'][0]['Flags'].append('--allow-insecure-entitlement=security.insecure')
    (values['docker_config']/'buildx/instances'/values['builder']).write_text(json.dumps(instance))
    real_run = isolated._run
    def run(args, env, timeout=30):
        if args[0] == '/usr/bin/git':return real_run(args, env, timeout)
        pytest.fail('unsafe builder must not run')
    monkeypatch.setattr(isolated,'_run',run)
    with pytest.raises(ValueError,match='unsafe builder'):
        isolated.isolated_npm_build(**values)
    assert not values['output_tar'].exists()


def test_npm_cache_writer_matches_npm_ci_cache_directory():
    recipe = isolated.RECIPE.decode();populate = isolated.POPULATE.decode()
    match = re.search(r'npm ci [^\n]*--cache ([^ ]+)', recipe)
    assert match is not None
    assert "cacache.put.stream('" + match.group(1) + "/_cacache',key,{metadata})" in populate


def test_controlled_preflight_binds_frozen_material_before_dispatch(tmp_path, monkeypatch):
    import controlled_preflight as preflight
    values, _, _, _ = fixture(tmp_path)
    monkeypatch.setattr(preflight, '_inspect_builder', lambda *args: None)
    args = dict(profile='npm', repo=values['repo'], revision=values['revision'],
        source_tar=values['source_tar'], source_sha256=values['source_sha256'],
        materials_archive=values['materials_tar'], materials_sha256=values['materials_sha256'],
        base_layout=values['base_layout'], base_manifest=values['base_manifest'],
        docker_bin=values['docker'], docker_config=values['docker_config'], docker_host=values['docker_host'],
        builder_name=values['builder'], builder_container_id=values['builder_container_id'],
        builder_image_id=values['builder_image_id'])
    result = preflight.preflight_controlled(**args)
    assert result['coverage'] == 'incomplete'
    assert result['materials_sha256'] == values['materials_sha256']
    with values['materials_tar'].open('ab') as stream:
        stream.write(b'tamper')
    with pytest.raises(ValueError, match='digest differs'):
        preflight.preflight_controlled(**args)
