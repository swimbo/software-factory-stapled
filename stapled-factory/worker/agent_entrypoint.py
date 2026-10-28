"""Start the fixed gateway bridge and OpenHands server as one worker service.

This file and egress_bridge.py must be baked into the trusted worker image.
The worker remains networkless; the bridge exposes only the mounted socket.
"""
import signal
import subprocess
import sys
import time


def main():
    children = []
    stopping = False

    def stop(_signal, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        children.append(subprocess.Popen([sys.executable, '/opt/factory/egress_bridge.py',
                                          '--socket', '/gateway/api.sock', '--port', '8123']))
        children.append(subprocess.Popen(['/usr/local/bin/openhands-agent-server']))
        while not stopping:
            for child in children:
                status = child.poll()
                if status is not None:
                    return status or 1
            time.sleep(0.2)
        return 0
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == '__main__':
    sys.exit(main())
