import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_entrypoint


def test_bridge_failure_stops_server(monkeypatch):
    class Child:
        def __init__(self, status):
            self.status = status
            self.terminated = False

        def poll(self):
            return self.status

        def terminate(self):
            self.terminated = True
            self.status = -15

        def wait(self, timeout=None):
            return self.status

    bridge, server = Child(1), Child(None)
    children = iter([bridge, server])
    commands = []

    def start(command):
        commands.append(command)
        return next(children)

    monkeypatch.setattr(agent_entrypoint.subprocess, 'Popen', start)
    monkeypatch.setattr(agent_entrypoint.signal, 'signal', lambda *_: None)
    assert agent_entrypoint.main() == 1
    assert commands[0][1:] == ['/opt/factory/egress_bridge.py', '--socket', '/gateway/api.sock', '--port', '8123']
    assert commands[1] == ['/usr/local/bin/openhands-agent-server']
    assert server.terminated
