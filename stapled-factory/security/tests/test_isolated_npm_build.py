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
    (repo/'server.js').write_text('require("http").createServer().listen(8000)')
    git('add','package.json','package-lock.json','server.js');git('commit','-qm','fixture')
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
        docker_bin_sha256='6'*64,buildx_bin_sha256='7'*64,npm_entrypoint='server.js',tag='factory-candidate-npm:'+revision,output_tar=private/'image.tar')
    metadata=[{'Id':container_id,'Image':image_id,'State':{'Running':True},
        'HostConfig':{'NetworkMode':'none','Binds':None},'Config':{'Entrypoint':['/usr/bin/buildkitd-entrypoint'],'Cmd':flags},
        'NetworkSettings':{'Networks':{'none':{}}},'Mounts':[{'Type':'volume','Destination':'/var/lib/buildkit'}]}]
    return values, metadata, flags, instance


def signed_materials(values, tmp_path):
    lock = (values['repo']/'package-lock.json').read_bytes()
    entry = json.loads(lock)['packages']['node_modules/x']
    private = tmp_path/'registry-key.pem'; public = tmp_path/'registry-key.der'; signature = tmp_path/'registry.sig'
    subprocess.run(['openssl','ecparam','-name','prime256v1','-genkey','-noout','-out',str(private)],check=True,capture_output=True)
    subprocess.run(['openssl','pkey','-in',str(private),'-pubout','-outform','DER','-out',str(public)],check=True,capture_output=True)
    key = public.read_bytes()
    def field(value):return len(value).to_bytes(4,'big')+value
    ssh = field(b'ecdsa-sha2-nistp256')+field(b'nistp256')+field(key[-65:])
    keyid = 'SHA256:'+base64.b64encode(hashlib.sha256(ssh).digest()).decode().rstrip('=')
    subprocess.run(['openssl','dgst','-sha256','-sign',str(private),'-out',str(signature)],
        input=('x@'+entry['version']+':'+entry['integrity']).encode(),check=True,capture_output=True)
    evidence={'node_modules/x':{'keyid':keyid,'sig':base64.b64encode(signature.read_bytes()).decode()}}
    archive=values['materials_tar'].parent/'signed-materials.tar';keys={keyid:key}
    report=freeze_npm(lock,tmp_path/'store',archive,registry_evidence=evidence,registry_keys=keys)
    return archive,report,keys


def test_fixed_offline_build_and_input_binding(tmp_path, monkeypatch):
    values, metadata, flags, _ = fixture(tmp_path);calls=[]; real_run = isolated._run
    archive, signed, keys = signed_materials(values, tmp_path)
    values.update(materials_tar=archive,materials_sha256=signed['archive_sha256'],registry_keys=keys)
    monkeypatch.setattr(isolated,'verify_docker_tools',lambda docker,*a:(docker,'/plugin'))
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
        context=Path(args[-1]);assert (context/'Dockerfile').read_bytes() == isolated.npm_recipe('server.js')
        assert '--network=none' in isolated.RECIPE.decode()
        image = Path(args[args.index('--output')+1].split('dest=',1)[1])
        layer=b'known layer bytes';diff='sha256:'+hashlib.sha256(layer).hexdigest()
        config = json.dumps({'os':'linux','architecture':'amd64','rootfs':{'type':'layers','diff_ids':[diff]},'config':{'Cmd':['node','/workspace/server.js'],'Entrypoint':[],'Labels':{
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
    assert result['registry_evidence_sha256']==signed['registry_evidence_sha256']
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
    monkeypatch.setattr(isolated,'verify_docker_tools',lambda docker,*a:(docker,'/plugin'))
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
    monkeypatch.setattr(preflight, 'verify_docker_tools',lambda docker,*a:(docker,'/plugin'))
    args = dict(profile='npm', repo=values['repo'], revision=values['revision'],
        source_tar=values['source_tar'], source_sha256=values['source_sha256'],
        materials_archive=values['materials_tar'], materials_sha256=values['materials_sha256'],
        base_layout=values['base_layout'], base_manifest=values['base_manifest'],
        docker_bin=values['docker'], docker_bin_sha256=values['docker_bin_sha256'],buildx_bin_sha256=values['buildx_bin_sha256'],
        npm_entrypoint='server.js', docker_config=values['docker_config'], docker_host=values['docker_host'],
        builder_name=values['builder'], builder_container_id=values['builder_container_id'],
        builder_image_id=values['builder_image_id'])
    result = preflight.preflight_controlled(**args)
    assert result['coverage'] == 'incomplete'
    assert result['materials_sha256'] == values['materials_sha256']
    archive,signed,keys=signed_materials(values,tmp_path)
    args.update(materials_archive=archive,materials_sha256=signed['archive_sha256'])
    with pytest.raises(ValueError,match='pinned'):
        preflight.preflight_controlled(**args)
    result=preflight.preflight_controlled(**args,registry_keys=keys)
    assert result['registry_evidence_sha256']==signed['registry_evidence_sha256']
    args.update(registry_keys=keys)
    with archive.open('ab') as stream:
        stream.write(b'tamper')
    with pytest.raises(ValueError, match='digest differs'):
        preflight.preflight_controlled(**args)


def test_npm_runtime_requires_regular_existing_source_entrypoint(tmp_path):
    values, _, _, _ = fixture(tmp_path)
    assert b'ENTRYPOINT []' in isolated.npm_recipe('server.js')
    assert b'CMD ["node","/workspace/server.js"]' in isolated.npm_recipe('server.js')
    with pytest.raises(ValueError,match='root npm package files required'):
        isolated._source_lock(values['source_tar'],values['revision'],'missing.js')
    with pytest.raises(ValueError,match='safe relative'):
        isolated.npm_recipe('../server.js')


def test_docker_and_buildx_tools_are_pinned_and_outside_source(tmp_path):
    binary=tmp_path/'tool';binary.write_bytes(b'#!/bin/sh\nexit 0\n');binary.chmod(0o700)
    config=tmp_path/'docker';(config/'cli-plugins').mkdir(parents=True);config.chmod(0o700)
    (config/'cli-plugins/docker-buildx').symlink_to(binary)
    digest=hashlib.sha256(binary.read_bytes()).hexdigest()
    isolated.verify_docker_tools(str(binary),config,digest,digest,[tmp_path/'source'])
    with pytest.raises(ValueError,match='inside source'):
        isolated.verify_docker_tools(str(binary),config,digest,digest,[tmp_path])
    binary.write_bytes(b'#!/bin/sh\nexit 1\n')
    with pytest.raises(ValueError,match='digest differs'):
        isolated.verify_docker_tools(str(binary),config,digest,digest,[tmp_path/'source'])
