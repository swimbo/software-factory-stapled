"""Minimal OpenHands 1.58 conversation HTTP client; no automatic retries."""
import json
import ipaddress
import re
from urllib.parse import urlsplit
import urllib.error
import urllib.request
import uuid


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class OpenHandsAdapter:
    def __init__(self, server_url, session_key, verify_revision, timeout=10, transport=None):
        if not session_key:
            raise ValueError("Session key required")
        parsed = urlsplit(server_url)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
                parsed.username is not None or parsed.password is not None or
                parsed.query or parsed.fragment):
            raise ValueError('Invalid Agent Server URL')
        try:
            loopback = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            loopback = parsed.hostname.lower() == 'localhost'
        if parsed.scheme == 'http' and not loopback:
            raise ValueError('Nonlocal Agent Server requires HTTPS')
        self.server_url = server_url.rstrip('/')
        self.session_key = session_key
        self.verify_revision = verify_revision
        self.timeout = timeout
        self.transport = transport
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def _request(self, method, path, payload=None):
        if self.transport is not None:
            return self.transport.request(method, path, payload)
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(self.server_url + path, data=data, method=method,
            headers={'X-Session-API-Key': self.session_key, 'Content-Type': 'application/json'})
        with self.opener.open(request, timeout=self.timeout) as response:
            return json.load(response)

    def create(self, conversation_id, payload):
        # Initial message causes server-side execution; never follow with /run.
        uuid.UUID(conversation_id)
        message = payload.get('initial_message')
        if not isinstance(message, dict) or message.get('run') is not True or not message.get('content'):
            raise ValueError('An initial message with run=true and content is required')
        if 'conversation_id' in payload and str(payload['conversation_id']) != conversation_id:
            raise ValueError('Conversation identity mismatch')
        info = self._request('POST', '/api/conversations', {**payload, 'conversation_id': conversation_id})
        if str(info.get('id')) != conversation_id:
            raise ValueError('Conversation identity mismatch')
        return self._classify(conversation_id, info)

    def read(self, conversation_id, _payload):
        uuid.UUID(conversation_id)
        info = self._request('GET', '/api/conversations/' + conversation_id)
        if str(info.get('id')) != conversation_id:
            raise ValueError('Conversation identity mismatch')
        return self._classify(conversation_id, info)

    def _classify(self, conversation_id, info):
        # Finished alone is not proof of a successful or committed build.
        status = info.get('execution_status')
        # An error or stuck state can follow a crash with unknown tool effects.
        # Neither state proves that a build had no side effects.
        if status != 'finished':
            return None
        revision = self.verify_revision(conversation_id, info)
        if not isinstance(revision, str) or not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', revision):
            return None
        return {'status': 'completed', 'git_revision': revision}
