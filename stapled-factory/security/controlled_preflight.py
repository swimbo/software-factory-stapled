"""Read-only preflight for an opt-in, incomplete controlled-build profile.

The source archive and dependency snapshot are checked before a build intent;
the runner rechecks and privately copies them. This does not establish publisher
identity, future build behavior, or complete dependency provenance.
"""
import hashlib
import os
from pathlib import Path
import re

from controlled_npm import verify_frozen_npm
from controlled_cargo import verify_frozen_cargo
from isolated_npm_build import (MAX_SOURCE, POPULATE, npm_recipe, verify_docker_tools, _builder_config,
                                _freeze_base, _hash_file, _inspect_builder,
                                _source_lock, _verify_source_revision)
from isolated_cargo_build import MAX_SOURCE_BYTES, _component, _recipe, cargo_source_preflight


def preflight_controlled(*, profile, repo, revision, source_tar, source_sha256,
                         materials_archive, materials_sha256, base_layout, base_manifest,
                         docker_bin, docker_bin_sha256, buildx_bin_sha256, docker_config, docker_host, builder_name,
                         builder_container_id, builder_image_id, npm_entrypoint=None, cargo_binary=None, registry_keys=None,
                         source_component=''):
    if profile not in ('npm', 'cargo'):
        raise ValueError('unsupported controlled build profile')
    for value in (source_sha256, materials_sha256):
        if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value):
            raise ValueError('pinned input digest required')
    if (not isinstance(revision, str) or not re.fullmatch(r'[a-f0-9]{40}', revision) or
            not isinstance(builder_container_id, str) or not re.fullmatch(r'[a-f0-9]{64}', builder_container_id) or
            not isinstance(builder_image_id, str) or not re.fullmatch(r'sha256:[a-f0-9]{64}', builder_image_id) or
            not isinstance(docker_bin, str) or not os.path.isabs(docker_bin)):
        raise ValueError('invalid controlled build identity')
    source_tar = Path(source_tar)
    _hash_file(source_tar, source_sha256, MAX_SOURCE if profile == 'npm' else MAX_SOURCE_BYTES)
    _verify_source_revision(repo, revision, source_sha256)
    if profile == 'npm':
        if cargo_binary is not None or source_component:raise ValueError('unsupported mixed runtime contract')
        frozen = verify_frozen_npm(materials_archive, materials_sha256, registry_keys=registry_keys)
        lock_sha = hashlib.sha256(_source_lock(source_tar, revision, npm_entrypoint)).hexdigest()
        recipe = npm_recipe(npm_entrypoint)
    else:
        if npm_entrypoint is not None or registry_keys is not None:raise ValueError('unsupported mixed runtime contract')
        frozen = verify_frozen_cargo(materials_archive, materials_sha256)
        source_component = _component(source_component)
        lock_sha = cargo_source_preflight(source_tar, cargo_binary, source_component)['lock_sha256']
        recipe = _recipe(source_sha256, materials_sha256, cargo_binary, source_component)
    if frozen['lock_sha256'] != lock_sha:
        raise ValueError('source and frozen dependency lock differ')
    base_config = _freeze_base(base_layout, base_manifest, None)
    config_sha, flags = _builder_config(docker_config, builder_name, docker_host)
    env = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin',
           'DOCKER_CONFIG': str(Path(docker_config).absolute()), 'DOCKER_HOST': docker_host}
    forbidden = [repo,Path(__file__).resolve().parents[2]]
    docker_target, plugin = verify_docker_tools(docker_bin,docker_config,docker_bin_sha256,buildx_bin_sha256,forbidden)
    _inspect_builder(docker_bin, builder_name, builder_container_id, builder_image_id, flags, env)
    if verify_docker_tools(docker_bin,docker_config,docker_bin_sha256,buildx_bin_sha256,forbidden) != (docker_target,plugin):
        raise ValueError('Docker tool identity changed during preflight')
    result = {'profile': profile, 'coverage': 'incomplete', 'source_sha256': source_sha256,
              'materials_sha256': materials_sha256, 'lock_sha256': lock_sha,
              'base_manifest': base_manifest, 'base_config': base_config,
              'builder_container_id': builder_container_id, 'builder_image_id': builder_image_id,
              'builder_config_sha256': config_sha, 'recipe_sha256': hashlib.sha256(recipe).hexdigest(),
              'docker_bin_sha256':docker_bin_sha256, 'buildx_bin_sha256':buildx_bin_sha256}
    if profile == 'npm':
        result.update(npm_entrypoint=npm_entrypoint, materials_manifest_sha256=frozen['manifest_sha256'],
                      cache_recipe_sha256=hashlib.sha256(POPULATE).hexdigest())
        if 'registry_evidence_sha256' in frozen:
            result['registry_evidence_sha256'] = frozen['registry_evidence_sha256']
    else:
        result['cargo_binary']=cargo_binary
        if source_component:result['source_component']=source_component
    return result
