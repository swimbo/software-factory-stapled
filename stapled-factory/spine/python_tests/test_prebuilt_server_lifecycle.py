"""The private wall bridge must close its real socket even if thread startup fails."""
import importlib.util
from pathlib import Path
import threading

import pytest

spec = importlib.util.spec_from_file_location(
    'prebuilt_server_lifecycle_test', Path(__file__).resolve().parents[1] / 'prebuilt_validation.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_thread_start_failure_closes_bound_socket_without_shutdown_deadlock(monkeypatch):
    real_server = m.ThreadingHTTPServer
    servers = []
    def server(*args, **kwargs):
        value = real_server(*args, **kwargs)
        servers.append(value)
        return value
    class NoThread:
        def __init__(self, **kwargs):
            pass
        def start(self):
            raise RuntimeError('thread unavailable')
    monkeypatch.setattr(m, 'ThreadingHTTPServer', server)
    monkeypatch.setattr(m.threading, 'Thread', NoThread)
    try:
        with pytest.raises(RuntimeError, match='thread unavailable'):
            with m.candidate_http_server(object(), threading.Lock()):
                pytest.fail('cannot evaluate without a running bridge')
        assert servers[0].fileno() == -1
    finally:
        for value in servers:
            value.server_close()


def test_normal_bridge_closes_its_socket_after_caller_error():
    server = None
    with pytest.raises(RuntimeError, match='evaluation failed'):
        with m.candidate_http_server(object(), threading.Lock()) as server:
            assert server.fileno() >= 0
            raise RuntimeError('evaluation failed')
    assert server.fileno() == -1
