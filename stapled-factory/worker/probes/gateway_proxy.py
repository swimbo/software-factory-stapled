"""Disposable bounded Bifrost proxy for integration probes; not a production gateway."""
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

key = sys.stdin.readline().strip()
if not key:
    raise SystemExit('missing credential')
lock = threading.Lock()
remaining = 12

class Proxy(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        global remaining
        if self.path != '/v1/messages':
            self.send_error(404); return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if size <= 0 or size > 1024 * 1024: raise ValueError()
            raw = self.rfile.read(size)
            data = json.loads(raw)
            if data.get('model') != 'ollama-cloud/deepseek-v4.1-flash:1017' or not 1 <= int(data.get('max_tokens', 0)) <= 1024:
                raise ValueError()
        except (ValueError, TypeError):
            self.send_error(400); return
        with lock:
            if remaining <= 0:
                self.send_error(429); return
            remaining -= 1
        req = urllib.request.Request('https://hermes-control.tail4296e6.ts.net/anthropic/v1/messages', data=raw, headers={'content-type':'application/json','anthropic-version':'2023-06-01','x-bf-vk':key})
        try:
            response = urllib.request.urlopen(req, timeout=90)
        except urllib.error.HTTPError as error:
            response = error
        except Exception:
            self.send_error(502); return
        self.send_response(response.status)
        self.send_header('content-type', response.headers.get('content-type','application/json'))
        self.send_header('Connection', 'close')
        self.end_headers()
        try:
            while chunk := response.read(8192):
                self.wfile.write(chunk)
                self.wfile.flush()
        finally:
            response.close()

ThreadingHTTPServer(('0.0.0.0', 8123), Proxy).serve_forever()
