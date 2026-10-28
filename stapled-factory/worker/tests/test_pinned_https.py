import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pinned_https


def test_connect_pins_address_and_validates_original_hostname(monkeypatch):
    calls = []
    sock = object()
    monkeypatch.setattr(pinned_https.socket, 'create_connection', lambda address, timeout, source: calls.append((address, timeout)) or sock)
    class Context:
        def wrap_socket(self, value, server_hostname):
            calls.append((value, server_hostname))
            return 'tls'
    conn = pinned_https.PinnedHTTPSConnection('gateway.example', 443, gateway_ip='100.92.236.42', timeout=3)
    conn._context = Context()
    conn.connect()
    assert calls == [(('100.92.236.42', 443), 3), (sock, 'gateway.example')]
    assert conn.sock == 'tls'


@pytest.mark.parametrize('address', ['gateway.example', '::1', '127.0.0.1', '0.0.0.0', '169.254.169.254', '224.0.0.1', '255.255.255.255'])
def test_invalid_pinned_address_rejected(address):
    with pytest.raises(ValueError):
        pinned_https.PinnedHTTPSConnection('gateway.example', 443, gateway_ip=address)


def test_refuses_http_tunnel(monkeypatch):
    conn = pinned_https.PinnedHTTPSConnection('gateway.example', 443, gateway_ip='100.92.236.42')
    conn.set_tunnel('other.example')
    monkeypatch.setattr(pinned_https.socket, 'create_connection', lambda *a: pytest.fail('connected before rejecting tunnel'))
    with pytest.raises(ValueError):
        conn.connect()
