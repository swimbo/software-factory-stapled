"""Install operator-owned NUC services from an already built release. No builds.
Run as bo on trusted host. Never place release/config paths in worker mounts.
"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess

p = argparse.ArgumentParser()
p.add_argument('--release', required=True, type=Path)
p.add_argument('--image', required=True)
p.add_argument('--home', required=True, type=Path)
p.add_argument('--identity-key', required=True)
a = p.parse_args()
if not re.fullmatch(r'sha256:[0-9a-f]{64}', a.image):
    p.error('immutable preloaded local Restate image ID required')
if not re.fullmatch(r'publickeyv1_[A-Za-z0-9]+', a.identity_key):
    p.error('request signing public key required')
release = a.release.resolve(strict=True)
for path in ('spine/dist/main.js', 'spine/dist/ingress_main.js', 'spine/node_modules/@restatedev/restate-sdk/package.json'):
    if not (release / path).is_file():
        p.error('prebuilt runtime missing ' + path)
root = a.home.resolve()
root.mkdir(mode=0o700, parents=True, exist_ok=True)
if root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077:
    p.error('owner-only state directory required')
key = root / 'request-signing.pem'
if not key.is_file() or key.stat().st_uid != os.getuid() or key.stat().st_mode & 0o077:
    p.error('owner-only request-signing.pem required')
(root / 'data').mkdir(mode=0o700, exist_ok=True)
token = root / 'ingress.token'
if not token.exists():
    fd = os.open(token, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(secrets.token_urlsafe(32))
units = Path.home() / '.config/systemd/user'
units.mkdir(parents=True, exist_ok=True)
node = subprocess.check_output(['which', 'node'], text=True).strip()
common = 'Restart=on-failure\nRestartSec=3\nUMask=0077\n'
services = {
    'factory-restate': '[Service]\n' + common +
        f'ExecStart=/usr/bin/docker run --rm --name factory-restate-control --hostname factory-restate-control --network host --user {os.getuid()}:{os.getgid()} --read-only --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp:rw,nosuid,nodev --mount type=bind,src={root},dst=/factory -e RESTATE_WORKER__INVOKER__REQUEST_IDENTITY_PRIVATE_KEY_PEM_FILE=/factory/request-signing.pem {a.image} --base-dir /factory/data --bind-ip 127.0.0.1 --listen-mode tcp --no-logo\n' +
        'Environment=RESTATE_ADMIN__BIND_ADDRESS=127.0.0.1:19070\n',
    'factory-endpoint': '[Service]\n' + common + f'WorkingDirectory={release}/spine\nExecStart={node} dist/main.js\nEnvironment=FACTORY_RESTATE_IDENTITY_KEYS={a.identity_key}\nEnvironment=FACTORY_ENDPOINT_PORT=19080\n',
    'factory-ingress': '[Service]\n' + common + f'WorkingDirectory={release}/spine\nExecStart={node} dist/ingress_main.js\nEnvironment=FACTORY_INGRESS_TOKEN_FILE={token}\nEnvironment=FACTORY_INGRESS_GATEWAY_PORT=19081\nEnvironment=FACTORY_RESTATE_INGRESS_PORT=18080\n'
}
# Restate environment must be inside container, not just systemd process.
services['factory-restate'] = services['factory-restate'].replace(' -e RESTATE_WORKER__INVOKER__REQUEST', ' -e RESTATE_ADMIN__BIND_ADDRESS=127.0.0.1:19070 -e RESTATE_INGRESS__BIND_ADDRESS=127.0.0.1:18080 -e RESTATE_WORKER__INVOKER__REQUEST')
for name, service in services.items():
    target = units / (name + '.service')
    if target.exists():
        raise SystemExit('refusing overwrite: ' + str(target))
    target.write_text('[Unit]\nDescription=Stapled factory sandbox ' + name + '\n' + service + '[Install]\nWantedBy=default.target\n')
subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
print(json.dumps({'installed': list(services), 'started': False, 'release': str(release)}))
