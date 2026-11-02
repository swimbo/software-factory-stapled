import hashlib
import gzip
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from controlled_cargo import freeze_cargo
from isolated_cargo_build import build_cargo_candidate
from test_controlled_cargo import fixture as cargo_fixture
import isolated_cargo_build as subject
import isolated_npm_build as npm_common


def inputs(tmp_path, monkeypatch):
    lock, materials, vendor, _ = cargo_fixture(tmp_path)
    snapshot = freeze_cargo(lock, materials, vendor, tmp_path/'frozen')
    source = tmp_path/'source.tar'
    with tarfile.open(source, 'w') as tar:
        for name, data in [('Cargo.toml', b'[package]\nname="app"\nversion="0.1.0"\n'), ('Cargo.lock', lock), ('src/main.rs', b'fn main() {}')]:
            item = tarfile.TarInfo(name);item.size = len(data);tar.addfile(item, io.BytesIO(data))
    base = tmp_path/'base';base.mkdir();base_manifest = 'sha256:' + 'c'*64
    config = tmp_path/'config';config.mkdir(mode=0o700);(config/'config.json').write_text('{}')
    name = 'factory-test';cid = 'a'*64;img = 'sha256:'+'b'*64;host = 'unix:///tmp/factory.sock'
    state = {'Id': cid, 'Image': img, 'State': {'Running': True},
             'HostConfig': {'NetworkMode': 'none', 'PidMode': '', 'IpcMode': ''},
             'Config': {'Env': ['PATH=/bin', 'BUILDKIT_SETUP_CGROUPV2_ROOT=1']},
             'Args': ['--allow-insecure-entitlement=network.host'],
             'Mounts': [{'Type': 'volume', 'Destination': '/var/lib/buildkit', 'RW': True,
                         'Name': 'buildx_buildkit_factory-test0_state'}]}
    calls = []
    def run(args, env, timeout=30):
        calls.append(args)
        if args[1:3] == ['buildx', 'inspect']:
            return 'Name: factory-test\nDriver: docker-container\nNodes:\nName: factory-test0\nEndpoint: '+host+'\nStatus: running\n'
        assert args[1] == 'inspect'
        return json.dumps([state])
    def build(args, **kwargs):
        calls.append(args)
        metadata = Path(args[args.index('--metadata-file')+1])
        output_arg = args[args.index('--output')+1]
        tag, filename = output_arg.removeprefix('type=docker,name=').split(',dest=', 1)
        output = Path(filename)
        labels = {item.split('=', 1)[0]: item.split('=', 1)[1]
                  for i, item in enumerate(args) if i and args[i-1] == '--label'}
        layer_data = gzip.compress(b'compressed-layer-fixture')
        layer_name = 'blobs/sha256/' + hashlib.sha256(layer_data).hexdigest()
        recipe = (Path(args[-1])/'Dockerfile').read_text()
        cmd = json.loads(next(line[4:] for line in recipe.splitlines() if line.startswith('CMD ')))
        image = {'os': 'linux', 'architecture': 'amd64', 'config': {'Labels': labels,
                 'Cmd': cmd},
                 'rootfs': {'type': 'layers', 'diff_ids': ['sha256:' + hashlib.sha256(b'compressed-layer-fixture').hexdigest()]}}
        config_data = json.dumps(image).encode();config_name = 'config.json'
        manifest = json.dumps([{'Config': config_name, 'RepoTags': [tag], 'Layers': [layer_name]}]).encode()
        with tarfile.open(output, 'w') as tar:
            for name, data in [('manifest.json', manifest), (config_name, config_data), (layer_name, layer_data)]:
                item = tarfile.TarInfo(name);item.size = len(data);tar.addfile(item, io.BytesIO(data))
        metadata.write_text('{}')
        return None
    monkeypatch.setattr(subject, '_run', run)
    monkeypatch.setattr(subject.subprocess, 'run', build)
    monkeypatch.setattr(subject, '_verify_git_source', lambda *args: None)
    monkeypatch.setattr(npm_common, '_builder_config', lambda *args: ('d'*64, ['--allow-insecure-entitlement=network.host']))
    monkeypatch.setattr(npm_common, '_inspect_builder', lambda *args: None)
    monkeypatch.setattr(npm_common, '_freeze_base', lambda *args: 'sha256:'+'e'*64)
    monkeypatch.setattr(npm_common, 'verify_docker_tools', lambda docker, *args: (docker, '/fixture/buildx'))
    params = dict(source_repo=tmp_path, revision='f'*40, unit='test',
                  source_archive=source, source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  cargo_archive=snapshot['archive'], cargo_sha256=snapshot['archive_sha256'],
                  base_layout=base, base_manifest=base_manifest,
                  builder_name=name, builder_container_id=cid, builder_image_id=img,
                  docker_host=host, docker_config=config, output_dir=tmp_path/'build', binary_name='app',
                  docker_bin_sha256='3'*64, buildx_bin_sha256='4'*64)
    return params, state, calls


def test_fixed_offline_build_binds_inputs_and_rechecks_builder(tmp_path, monkeypatch):
    params, state, calls = inputs(tmp_path, monkeypatch)
    result = build_cargo_candidate(**params)
    assert result['coverage'] == 'incomplete'
    assert result['cargo_sha256'] == params['cargo_sha256']
    assert result['image_archive_sha256'] == hashlib.sha256((params['output_dir']/'image.tar').read_bytes()).hexdigest()
    command = next(c for c in calls if c[1:3] == ['buildx', 'build'])
    assert command[command.index('--network')+1] == 'none'
    assert '--no-cache' in command and not any(arg.startswith('--allow') for arg in command)
    recipe = (params['output_dir']/'context'/'Dockerfile').read_text()
    assert 'RUN --network=none cargo build --release --locked --offline --bin app' in recipe
    assert 'ENTRYPOINT []' in recipe
    assert 'CMD ["/factory/source/target/release/app"]' in recipe
    assert result['cargo_binary'] == 'app' and result['docker_bin_sha256'] == '3'*64
    assert set(p.name for p in (params['output_dir']/'context').iterdir()) == {'Dockerfile', 'source.tar', 'cargo-inputs.tar'}
    assert len([c for c in calls if c[1:3] == ['buildx', 'inspect']]) == 2
    alias = tmp_path/'image-symlink.tar';alias.symlink_to(params['output_dir']/'image.tar')
    with pytest.raises(ValueError, match='invalid Docker image archive'):
        subject.verify_docker_image_archive(alias, result['tag'], result['revision'], result['source_sha256'])
    with pytest.raises(FileExistsError):build_cargo_candidate(**params)


def test_nested_component_runner_uses_selected_source_and_output(tmp_path, monkeypatch):
    params,_,_=inputs(tmp_path,monkeypatch)
    with tarfile.open(params['source_archive']) as tar:
        original=[(item.name,tar.extractfile(item).read()) for item in tar if item.isfile()]
    with tarfile.open(params['source_archive'],'w') as tar:
        for name,data in original:
            item=tarfile.TarInfo('backend/'+name);item.size=len(data);tar.addfile(item,io.BytesIO(data))
    params['source_sha256']=hashlib.sha256(params['source_archive'].read_bytes()).hexdigest()
    params['source_component']='backend'
    result=build_cargo_candidate(**params)
    assert result['source_component']=='backend' and result['coverage']=='incomplete'
    recipe=(params['output_dir']/'context/Dockerfile').read_text()
    assert 'WORKDIR /factory/source/backend\n' in recipe
    assert 'CMD ["/factory/source/backend/target/release/app"]' in recipe


def test_builder_network_socket_and_insecure_entitlement_reject_before_build(tmp_path, monkeypatch):
    params, state, calls = inputs(tmp_path, monkeypatch)
    for change in ('network', 'socket', 'entitlement'):
        current = json.loads(json.dumps(state))
        if change == 'network':state['HostConfig']['NetworkMode'] = 'host'
        elif change == 'socket':state['Mounts'].append({'Type': 'bind', 'Destination': '/run/docker.sock'})
        else:state['Args'].append('--allow-insecure-entitlement=security.insecure')
        with pytest.raises(ValueError):build_cargo_candidate(**params)
        assert not params['output_dir'].exists()
        state.clear();state.update(current)
    assert not any(c[1:3] == ['buildx', 'build'] for c in calls)


def test_changed_source_and_secret_paths_rejected_before_dispatch(tmp_path, monkeypatch):
    params, _, calls = inputs(tmp_path, monkeypatch)
    params['source_sha256'] = '0'*64
    with pytest.raises(ValueError, match='digest differs'):build_cargo_candidate(**params)
    with tarfile.open(params['source_archive'], 'a') as tar:
        item = tarfile.TarInfo('.env');item.size = 7;tar.addfile(item, io.BytesIO(b'fixture'))
    params['source_sha256'] = hashlib.sha256(params['source_archive'].read_bytes()).hexdigest()
    params['output_dir'] = tmp_path/'second-build'
    with pytest.raises(ValueError, match='restricted credential path'):build_cargo_candidate(**params)
    different = tmp_path/'different.tar'
    with tarfile.open(different, 'w') as tar:
        for name, data in [('Cargo.toml', b'[package]\nname="app"\nversion="0.1.0"\n'), ('Cargo.lock', b'version = 4\n'), ('src/main.rs', b'fn main() {}')]:
            item = tarfile.TarInfo(name);item.size = len(data);tar.addfile(item, io.BytesIO(data))
    params['source_archive'] = different
    params['source_sha256'] = hashlib.sha256(different.read_bytes()).hexdigest()
    params['output_dir'] = tmp_path/'third-build'
    with pytest.raises(ValueError, match='source and frozen Cargo lock differ'):build_cargo_candidate(**params)
    assert not any(c[1:3] == ['buildx', 'build'] for c in calls)


def test_git_archive_matches_exact_selected_commit(tmp_path):
    repo = tmp_path/'repo';repo.mkdir()
    def git(*args):
        return subprocess.check_output(['/usr/bin/git', '-C', str(repo), *args])
    git('init', '-q');git('config', 'user.name', 'Fixture');git('config', 'user.email', 'fixture@example.invalid')
    (repo/'app').write_text('original\n');git('add', 'app');git('commit', '-qm', 'fixture')
    revision = git('rev-parse', 'HEAD').decode().strip()
    source = tmp_path/'source.tar';source.write_bytes(git('archive', revision))
    subject._verify_git_source(repo, revision, source)
    with source.open('ab') as stream:stream.write(b'tampered')
    with pytest.raises(ValueError, match='source archive differs'):
        subject._verify_git_source(repo, revision, source)


def test_nested_cargo_component_binds_its_lock_and_runtime_path(tmp_path):
    source=tmp_path/'source.tar';lock=b'version = 4\n'
    def write_source(*extra):
        with tarfile.open(source,'w') as tar:
            entries=[('backend/Cargo.toml',b'[package]\nname="app"\nversion="0.1.0"\n'),
                     ('backend/Cargo.lock',lock),('backend/src/main.rs',b'fn main() {}'),*extra]
            for name,data in entries:
                item=tarfile.TarInfo(name);item.size=len(data);tar.addfile(item,io.BytesIO(data))
    write_source()
    result=subject.cargo_source_preflight(source,'app','backend')
    assert result['lock_sha256']==hashlib.sha256(lock).hexdigest()
    assert result['source_component']=='backend'
    recipe=subject._recipe('a'*64,'b'*64,'app','backend').decode()
    assert 'WORKDIR /factory/source/backend\n' in recipe
    assert 'CMD ["/factory/source/backend/target/release/app"]' in recipe
    with pytest.raises(ValueError,match='component'):
        subject.cargo_source_preflight(source,'app','../backend')
    write_source(('Cargo.toml',b'[workspace]\nmembers=["backend"]\n'))
    with pytest.raises(ValueError,match='parent Cargo workspace'):
        subject.cargo_source_preflight(source,'app','backend')
