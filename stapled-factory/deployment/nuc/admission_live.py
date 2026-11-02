"""Read-only collection of pinned NUC process and nft admission evidence.

This does not install a fence or authorize a restart. The caller must establish
the startup hold, authenticate the expected identities and exclude concurrent
privileged firewall writers throughout the transition.
"""
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from admission_identity import read_identity
from admission_nft import verify_live_evidence, verify_snapshot, verify_hook_order

DOCKER=Path('/usr/bin/docker')
NFT=Path('/usr/sbin/nft')


def _tool(path, expected):
    if not isinstance(expected,str) or not re.fullmatch(r'[a-f0-9]{64}',expected):
        raise ValueError('pinned tool digest required')
    path=path.resolve(strict=True)
    fd=os.open(path,os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in (0,os.getuid()) or
                info.st_mode & 0o022 or not info.st_mode & 0o111 or
                hashlib.file_digest(stream,'sha256').hexdigest()!=expected):
            raise ValueError('trusted admission tool differs')
    return str(path)


def _run(args):
    env={'PATH':'/usr/bin:/bin:/usr/sbin:/sbin','HOME':str(Path.home()),
         'DOCKER_HOST':'unix:///var/run/docker.sock'}
    try:
        result=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                              check=True,timeout=30,env=env)
    except (OSError,subprocess.SubprocessError) as exc:
        raise ValueError('admission readback unavailable') from exc
    if len(result.stdout)>16*1024*1024:raise ValueError('oversized admission readback')
    return result.stdout.decode('utf-8')


def _verify(identifier, expected_identities, *, helper_image, docker_sha256, nft_sha256, observe_ingress):
    names=('probe','ingress','restate')
    if (not isinstance(identifier,str) or not re.fullmatch(r'[a-f0-9]{16}',identifier) or
            not isinstance(helper_image,str) or not re.fullmatch(r'sha256:[a-f0-9]{64}',helper_image) or
            not isinstance(expected_identities,dict) or set(expected_identities)!=set(names)):
        raise ValueError('pinned admission identity required')
    for name in names:
        item=expected_identities[name]
        if not isinstance(item,dict) or type(item.get('pid')) is not int or item['pid']<=0:
            raise ValueError('pinned admission process required')
    docker=_tool(DOCKER,docker_sha256);nft=_tool(NFT,nft_sha256)
    if _run([docker,'image','inspect','--format','{{.Id}}',helper_image]).strip()!=helper_image:
        raise ValueError('admission helper image differs')
    def identities():
        return {name:(dict(expected_identities[name]) if name=='ingress' and not observe_ingress else
                      read_identity(expected_identities[name]['pid'],os.getuid())) for name in names}
    before=identities()
    if before!=expected_identities:raise ValueError('admission process identity differs')
    prefix=[docker,'run','--rm','--pull=never','--network=host','--cgroupns=host',
        '--cap-drop=ALL','--cap-add=NET_ADMIN','--cap-add=SYS_CHROOT','--read-only',
        '--mount=type=bind,src=/,dst=/host,readonly','--security-opt=no-new-privileges',
        '--entrypoint=/usr/sbin/chroot',helper_image,'/host','/usr/sbin/nft']
    table='factory_hold_'+identifier
    table_args=['list','table','inet',table]
    snapshot=_run(prefix+['-a']+table_args)
    debug=_run(prefix+['--debug','netlink']+table_args)
    hooks=_run(prefix+['-j','list','ruleset'])
    second=_run(prefix+['-a']+table_args)
    later_hooks=_run(prefix+['-j','list','ruleset'])
    after=identities()
    if second!=snapshot:raise ValueError('nft table changed during readback')
    result=verify_live_evidence(identifier,before,after,snapshot,debug,hooks)
    verify_snapshot(second,identifier,*[after[name]['cgroup'] for name in names])
    verify_hook_order(later_hooks,identifier)
    if _tool(DOCKER,docker_sha256)!=docker or _tool(NFT,nft_sha256)!=nft:
        raise ValueError('admission tool changed during readback')
    if _run([docker,'image','inspect','--format','{{.Id}}',helper_image]).strip()!=helper_image:
        raise ValueError('admission helper image changed')
    return {**result,'nft_snapshot_sha256':hashlib.sha256(snapshot.encode()).hexdigest(),
            'helper_image':helper_image,'nft_sha256':nft_sha256,'ingress_observed':observe_ingress}


def verify_live(identifier, expected_identities, *, helper_image, docker_sha256, nft_sha256):
    """Independently check the live table and all three original processes."""
    return _verify(identifier,expected_identities,helper_image=helper_image,docker_sha256=docker_sha256,
                   nft_sha256=nft_sha256,observe_ingress=True)


def verify_restart_fence(identifier, expected_identities, *, helper_image, docker_sha256, nft_sha256):
    """Check a retained ingress rule while that process may be stopped for restart.

    The caller must bind the ingress identity to an earlier successful full
    live verification and must rebind and reverify the new ingress afterward.
    """
    return _verify(identifier,expected_identities,helper_image=helper_image,docker_sha256=docker_sha256,
                   nft_sha256=nft_sha256,observe_ingress=False)
