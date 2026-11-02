"""Generate isolated nftables transactions for a factory transition.

These transactions are not an admission fence by themselves. The caller must
verify exact cgroup identities, installed rules, startup guards, drained work,
and recovery before using them on a shared host.
"""
import hashlib
import json
import re

PORTS = '5122, 18080, 19070, 19080, 19081'


def _inputs(identifier, *paths):
    if not isinstance(identifier,str) or not re.fullmatch(r'[a-f0-9]{16}',identifier):
        raise ValueError('invalid fence identity')
    result=[]
    for path in paths:
        if not isinstance(path,str) or not re.fullmatch(r'/[A-Za-z0-9_.@:/-]+',path):
            raise ValueError('invalid cgroup path')
        parts=path[1:].split('/')
        if any(part in ('','.','..') for part in parts):raise ValueError('invalid cgroup path')
        result.append('/'.join(parts))
    for index,left in enumerate(result):
        for right in result[index+1:]:
            if left==right or left.startswith(right+'/') or right.startswith(left+'/'):
                raise ValueError('overlapping cgroups')
    return 'factory_hold_'+identifier,result


def render_install(identifier, probe, ingress, restate):
    table,paths=_inputs(identifier,probe,ingress,restate)
    probe,ingress,restate=paths
    def match(path):return f'socket cgroupv2 level {len(path.split("/"))} "{path}"'
    return f'''table inet {table} {{
 chain output {{
  type filter hook output priority -151; policy accept;
  ip daddr 127.0.0.0/8 tcp dport {{ {PORTS} }} jump guard
  ip6 daddr ::1 tcp dport {{ {PORTS} }} jump guard
 }}
 chain guard {{
  {match(probe)} return
  tcp dport 18080 {match(ingress)} return
  tcp dport {{ 5122, 19080 }} {match(restate)} return
  reject with tcp reset
 }}
}}
'''


def render_rebind(identifier, probe, ingress, restate):
    table,_=_inputs(identifier,probe,ingress,restate)
    return 'delete table inet '+table+'\n'+render_install(identifier,probe,ingress,restate)


def verify_snapshot(snapshot, identifier, probe, ingress, restate):
    """Check pinned nft text output; cgroup identity still requires separate proof."""
    table,paths=_inputs(identifier,probe,ingress,restate)
    if not isinstance(snapshot,str) or len(snapshot)>1024*1024:
        raise ValueError('invalid nft snapshot')
    probe,ingress,restate=paths
    def match(path):return f'socket cgroupv2 level {len(path.split("/"))} "{path}"'
    expected=[f'table inet {table} {{','chain output {',
        'type filter hook output priority mangle - 1; policy accept;',
        f'ip daddr 127.0.0.0/8 tcp dport {{ {PORTS} }} jump guard',
        f'ip6 daddr ::1 tcp dport {{ {PORTS} }} jump guard','}',
        'chain guard {',f'{match(probe)} return',
        f'tcp dport 18080 {match(ingress)} return',
        f'tcp dport {{ 5122, 19080 }} {match(restate)} return',
        'meta l4proto tcp reject with tcp reset','}','}']
    lines=[re.sub(r'\s+# handle [0-9]+$', '', line.strip())
           for line in snapshot.splitlines() if line.strip()]
    if lines!=expected:raise ValueError('nft admission rules differ')


def verify_kernel_ids(debug_output, expected_ids):
    """Compare kernel rule operands to independently read cgroup-v2 inode IDs."""
    if (not isinstance(debug_output,str) or len(debug_output)>4*1024*1024 or
            not isinstance(expected_ids,list) or len(expected_ids)!=3 or
            any(type(value) is not int or not 0 < value < 2**64 for value in expected_ids)):
        raise ValueError('invalid cgroup kernel evidence')
    pattern=r'\[ socket load cgroupv2 => reg 1 \]\s*\[ cmp eq reg 1 0x([0-9a-f]{8}) 0x([0-9a-f]{8}) \]'
    matches=re.findall(pattern,debug_output)
    if debug_output.count('[ socket load cgroupv2') != 3 or len(matches)!=3:
        raise ValueError('cgroup kernel operands unavailable')
    actual=[int(low,16) | int(high,16)<<32 for low,high in matches]
    if actual != expected_ids:raise ValueError('cgroup kernel identities differ')


def verify_hook_order(snapshot, identifier):
    """Reject earlier or equally prioritized output hooks in any nft family."""
    table,_=_inputs(identifier)
    if not isinstance(snapshot,str) or len(snapshot)>16*1024*1024:
        raise ValueError('invalid nft ruleset snapshot')
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('duplicate nft ruleset key')
            result[key]=value
        return result
    try:data=json.loads(snapshot,object_pairs_hook=unique)
    except (ValueError,TypeError) as exc:raise ValueError('invalid nft ruleset snapshot') from exc
    if not isinstance(data,dict) or not isinstance(data.get('nftables'),list):
        raise ValueError('invalid nft ruleset snapshot')
    own=0
    for item in data['nftables']:
        if not isinstance(item,dict):raise ValueError('invalid nft ruleset entry')
        if 'chain' not in item:continue
        chain=item['chain']
        if not isinstance(chain,dict):raise ValueError('invalid nft chain')
        if chain.get('hook')!='output':continue
        priority=chain.get('prio')
        if type(priority) is not int:raise ValueError('nft output priority unavailable')
        is_own=(chain.get('family')=='inet' and chain.get('table')==table and chain.get('name')=='output')
        if is_own:
            if priority!=-151:raise ValueError('owned nft priority differs')
            own+=1
        elif priority<=-151:raise ValueError('another nft output hook precedes admission')
    if own!=1:raise ValueError('owned nft output hook unavailable')


def verify_live_evidence(identifier, before, after, snapshot, debug_output, ruleset):
    """Cross-check caller-observed identities and nft readback; this does not collect them."""
    names=('probe','ingress','restate')
    if not isinstance(before,dict) or set(before)!=set(names) or before!=after:
        raise ValueError('process identities changed during nft readback')
    for name in names:
        item=before[name]
        if (not isinstance(item,dict) or set(item)!={'pid','start_ticks','cgroup','inode'} or
                any(type(item[k]) is not int or item[k]<=0 for k in ('pid','start_ticks','inode'))):
            raise ValueError('incomplete process identity')
    if len({before[name]['pid'] for name in names})!=3 or len({before[name]['inode'] for name in names})!=3:
        raise ValueError('process or cgroup identities overlap')
    paths=[before[name]['cgroup'] for name in names]
    verify_snapshot(snapshot,identifier,*paths)
    verify_kernel_ids(debug_output,[before[name]['inode'] for name in names])
    verify_hook_order(ruleset,identifier)
    raw=json.dumps(before,sort_keys=True,separators=(',',':')).encode()
    return {'verified':True,'identities_sha256':hashlib.sha256(raw).hexdigest()}
