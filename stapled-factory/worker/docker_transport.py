"""Host-to-Agent-Server HTTP over docker exec stdin, without port publishing.

The caller must authenticate the Docker daemon and independently bind the full
container ID to its isolated attempt. This transport does not establish either.
"""
import json
import re
import subprocess
import uuid

_CONTAINER_ID = re.compile(r'[0-9a-f]{64}\Z')
_CLIENT = r'''
import json, sys, urllib.request
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None
try:
    req = json.load(sys.stdin)
    data = None if req['payload'] is None else json.dumps(req['payload']).encode()
    request = urllib.request.Request('http://127.0.0.1:8000' + req['path'], data=data,
        method=req['method'], headers={'X-Session-API-Key': req['key'], 'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=10) as response:
        body = response.read(1048577)
        if len(body) > 1048576: raise ValueError('response too large')
        result = json.loads(body)
        if not isinstance(result, dict): raise ValueError('invalid response')
    print(json.dumps({'ok': True, 'body': result}))
except Exception:
    print(json.dumps({'ok': False}))
'''


class DockerTransportError(Exception):
    """An error with no remote response details or credentials attached."""


class DockerTransport:
    def __init__(self, container_id, session_key, timeout=20):
        if not isinstance(container_id, str) or not _CONTAINER_ID.fullmatch(container_id):
            raise ValueError('Full Docker container ID required')
        if not isinstance(session_key, str) or not session_key:
            raise ValueError('Session key required')
        self.container_id = container_id
        self.session_key = session_key
        self.timeout = timeout

    def request(self, method, path, payload=None):
        if method == 'POST' and path == '/api/conversations':
            if not isinstance(payload, dict):
                raise ValueError('POST payload must be an object')
        elif method == 'GET' and path.startswith('/api/conversations/') and payload is None:
            identifier = path.removeprefix('/api/conversations/')
            try:
                if str(uuid.UUID(identifier)) != identifier:
                    raise ValueError()
            except (ValueError, AttributeError):
                raise ValueError('Invalid conversation ID') from None
        else:
            raise ValueError('Unsupported request')
        envelope = json.dumps({'method': method, 'path': path, 'payload': payload, 'key': self.session_key})
        try:
            result = subprocess.run(['docker', 'exec', '-i', self.container_id, 'python3', '-c', _CLIENT],
                input=envelope, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                check=True, timeout=self.timeout)
            if len(result.stdout) > 2 * 1048576:
                raise ValueError()
            decoded = json.loads(result.stdout)
            if not isinstance(decoded, dict) or decoded.get('ok') is not True or not isinstance(decoded.get('body'), dict):
                raise ValueError()
            return decoded['body']
        except (OSError, subprocess.SubprocessError, ValueError, TypeError):
            raise DockerTransportError('Agent Server request outcome is uncertain') from None
