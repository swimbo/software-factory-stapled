"""Bound host buffers while exchanging one request with the original candidate."""
import os
import selectors
import signal
import subprocess
import time


def capture(args, payload, *, timeout=15):
    """Send bytes while draining capped stdout/stderr; reap this client on error.

    This does not stop an in-container process. The caller's original-container
    lifecycle and independent expiry own containment of candidate execution.
    """
    deadline = time.monotonic() + timeout
    child = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, start_new_session=True)
    output = bytearray()
    errors = bytearray()
    streams = {'stdout': (output, 1500000), 'stderr': (errors, 65536)}
    offset = 0
    try:
        with selectors.DefaultSelector() as ready:
            for stream, name, event in ((child.stdin, 'stdin', selectors.EVENT_WRITE),
                                        (child.stdout, 'stdout', selectors.EVENT_READ),
                                        (child.stderr, 'stderr', selectors.EVENT_READ)):
                os.set_blocking(stream.fileno(), False)
                ready.register(stream, event, name)
            while ready.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(args, timeout)
                for item, _ in ready.select(min(remaining, .1)):
                    stream, name = item.fileobj, item.data
                    if name == 'stdin':
                        try:
                            offset += os.write(stream.fileno(), payload[offset:offset + 65536])
                        except BrokenPipeError:
                            offset = len(payload)
                        except BlockingIOError:
                            continue
                        if offset == len(payload):
                            ready.unregister(stream)
                            stream.close()
                    else:
                        try:
                            chunk = os.read(stream.fileno(), 65536)
                        except BlockingIOError:
                            continue
                        if not chunk:
                            ready.unregister(stream)
                            stream.close()
                            continue
                        buffer, limit = streams[name]
                        if len(buffer) + len(chunk) > limit:
                            raise ValueError('validation transport ' + name + ' too large')
                        buffer.extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(args, timeout)
        code = child.wait(timeout=remaining)
        if code:
            raise subprocess.CalledProcessError(code, args, bytes(output), bytes(errors))
        return bytes(output)
    finally:
        # An unreaped child retains its PID, so this process group cannot be a
        # replacement belonging to another operation.
        if child.returncode is None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=5)
        for stream in (child.stdin, child.stdout, child.stderr):
            stream.close()
