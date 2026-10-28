// Narrow local ingress for the only operations currently supported externally.
// Security depends on isolating the Restate ingress and admin listeners from callers.
import { createServer, request, type Server } from 'node:http';
import { timingSafeEqual } from 'node:crypto';
import { readFileSync, lstatSync } from 'node:fs';

const routes = /^\/(?:orchestrate\/[A-Za-z0-9_.:-]+\/run|Reconciliation\/[A-Za-z0-9_.:-]+\/(?:observe|restart)|WorkUnit\/[A-Za-z0-9_.:-]+\/getState)$/;
const maxBody = 64 * 1024;

export function readIngressToken(path: string): string {
  const st = lstatSync(path);
  if (!st.isFile() || st.isSymbolicLink() || (st.mode & 0o077) !== 0 || st.uid !== process.getuid?.()) {
    throw new Error('Ingress token must be an owner-only regular file');
  }
  const token = readFileSync(path, 'utf8').trim();
  if (!/^[A-Za-z0-9_-]{43,}$/.test(token)) throw new Error('Ingress token must be at least 43 base64url characters');
  return token;
}

export function createIngress(token: string, upstreamPort: number): Server {
  if (!/^[A-Za-z0-9_-]{43,}$/.test(token) || !Number.isInteger(upstreamPort) || upstreamPort < 1 || upstreamPort > 65535) throw new Error('Invalid ingress configuration');
  const expected = Buffer.from(`Bearer ${token}`);
  return createServer((req, res) => {
    const authHeaders = req.rawHeaders.filter((_, i) => i % 2 === 0 && req.rawHeaders[i].toLowerCase() === 'authorization');
    const supplied = Buffer.from(req.headers.authorization ?? '');
    if (authHeaders.length !== 1 || supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) {
      res.writeHead(401); res.end(); return;
    }
    if (req.method !== 'POST' || !routes.test(req.url ?? '') || req.headers['content-type'] !== 'application/json') {
      res.writeHead(404); res.end(); return;
    }
    const chunks: Buffer[] = []; let length = 0; let rejected = false;
    req.on('data', (chunk: Buffer) => {
      length += chunk.length;
      if (length > maxBody) { if (!rejected) { rejected = true; res.writeHead(413); res.end(); } return; }
      chunks.push(chunk);
    });
    req.on('end', () => {
      if (rejected) return;
      const body = Buffer.concat(chunks);
      const upstream = request({ hostname: '127.0.0.1', port: upstreamPort, method: 'POST', path: req.url,
        headers: {'content-type': 'application/json', 'content-length': body.length} }, response => {
        res.writeHead(response.statusCode ?? 502, {'content-type': response.headers['content-type'] ?? 'application/json'});
        response.pipe(res);
      });
      upstream.on('error', () => { if (!res.headersSent) res.writeHead(502); res.end(); });
      res.on('close', () => upstream.destroy());
      upstream.end(body);
    });
  });
}
