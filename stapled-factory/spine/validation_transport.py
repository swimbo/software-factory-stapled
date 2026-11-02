"""Bound host buffers while exchanging one request with the original candidate."""
import os
import selectors
import signal
import subprocess
import time


class Session:
    """One bounded, newline-framed client; failure permanently closes it.

    Keep Docker/Python startup outside the intervals between scenario requests.
    Candidate execution containment still belongs to the original container.
    """
    def __init__(self, args):
        self.args = args
        self.child = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, start_new_session=True)
        self.closed = False
        self.errors = bytearray()
        for stream in (self.child.stdin, self.child.stdout, self.child.stderr):
            os.set_blocking(stream.fileno(), False)

    def exchange(self, payload, *, timeout=15):
        if self.closed:
            raise ValueError('validation transport closed; original request must not be resent')
        deadline = time.monotonic() + timeout
        output = bytearray()
        offset = 0
        complete = False
        try:
            with selectors.DefaultSelector() as ready:
                ready.register(self.child.stdin, selectors.EVENT_WRITE, 'stdin')
                ready.register(self.child.stdout, selectors.EVENT_READ, 'stdout')
                ready.register(self.child.stderr, selectors.EVENT_READ, 'stderr')
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(self.args, timeout)
                    for item, _ in ready.select(min(remaining, .1)):
                        stream, name = item.fileobj, item.data
                        if name == 'stdin':
                            try:
                                offset += os.write(stream.fileno(), payload[offset:offset + 65536])
                            except BlockingIOError:
                                continue
                            if offset == len(payload):
                                ready.unregister(stream)
                        else:
                            try:
                                chunk = os.read(stream.fileno(), 65536)
                            except BlockingIOError:
                                continue
                            if not chunk:
                                ready.unregister(stream)
                                if name == 'stdout' and not complete:
                                    code = self.child.wait(timeout=max(.001, deadline - time.monotonic()))
                                    if code:
                                        raise subprocess.CalledProcessError(code, self.args, bytes(output), bytes(self.errors))
                                    raise ValueError('validation transport response incomplete')
                                continue
                            buffer, limit = (output, 1500000) if name == 'stdout' else (self.errors, 65536)
                            if len(buffer) + len(chunk) > limit:
                                raise ValueError('validation transport ' + name + ' too large')
                            buffer.extend(chunk)
                            if name == 'stdout' and b'\n' in output:
                                if output.find(b'\n') != len(output) - 1:
                                    raise ValueError('validation transport unexpected response data')
                                complete = True
                    if complete and offset == len(payload):
                        return bytes(output)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.closed:
            return
        self.closed = True
        # Reap only the owned host client; never claim this kills its container.
        if self.child.poll() is None:
            try:
                os.killpg(self.child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError as error:
                try:
                    self.child.wait(timeout=.2)
                except subprocess.TimeoutExpired:
                    raise error
            self.child.wait(timeout=5)
        for stream in (self.child.stdin, self.child.stdout, self.child.stderr):
            stream.close()
