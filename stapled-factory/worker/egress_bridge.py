"""Bridge localhost HTTP to a pre-mounted Unix socket inside a networkless worker."""
import argparse
import selectors
import socket
import socketserver


class Bridge(socketserver.BaseRequestHandler):
    def handle(self):
        upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            upstream.connect(self.server.socket_path)
            self.request.settimeout(120)
            upstream.settimeout(120)
            with selectors.DefaultSelector() as selector:
                selector.register(self.request, selectors.EVENT_READ, upstream)
                selector.register(upstream, selectors.EVENT_READ, self.request)
                while events := selector.select(120):
                    for key, _ in events:
                        data = key.fileobj.recv(65536)
                        if not data:
                            return
                        key.data.sendall(data)
        finally:
            upstream.close()


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', required=True)
    parser.add_argument('--port', type=int, default=8123)
    args = parser.parse_args()
    with Server(('127.0.0.1', args.port), Bridge) as server:
        server.socket_path = args.socket
        server.serve_forever()


if __name__ == '__main__':
    main()
