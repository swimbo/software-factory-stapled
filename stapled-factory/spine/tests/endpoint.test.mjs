import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { createServer } from 'node:net';
import { connect } from 'node:http2';

test('SDK endpoint requires a key and rejects unsigned requests', async () => {
  const env = { ...process.env };
  delete env.FACTORY_RESTATE_IDENTITY_KEYS;
  const missing = spawnSync(process.execPath, ['dist/main.js'], { env, encoding: 'utf8' });
  assert.notEqual(missing.status, 0);
  assert.match(missing.stderr, /FACTORY_RESTATE_IDENTITY_KEYS/);

  const holder = createServer();
  await new Promise(resolve => holder.listen(0, '127.0.0.1', resolve));
  const port = holder.address().port;
  await new Promise(resolve => holder.close(resolve));
  const child = spawn(process.execPath, ['dist/main.js'], { env: { ...env,
    FACTORY_ENDPOINT_PORT: String(port),
    FACTORY_RESTATE_IDENTITY_KEYS: 'publickeyv1_2G8dCQhArfvGpzPw5Vx2ALciR4xCLHfS5YaT93XjNxX9',
  }, stdio: 'ignore' });
  try {
    let status;
    for (let n = 0; n < 50; n++) {
      try {
        status = await new Promise((resolve, reject) => {
          const client = connect(`http://127.0.0.1:${port}`);
          client.on('error', reject);
          const request = client.request({ ':path': '/discover', ':method': 'GET' });
          request.on('response', headers => { resolve(headers[':status']); client.close(); });
          request.on('error', reject);
          request.resume(); request.end();
        });
        break;
      } catch {
        if (child.exitCode !== null) throw new Error('SDK endpoint exited');
        await new Promise(resolve => setTimeout(resolve, 100));
      }
    }
    assert.equal(status, 401);
  } finally {
    if (child.exitCode === null && child.signalCode === null) {
      child.kill('SIGTERM');
      await new Promise(resolve => child.once('exit', resolve));
    }
  }
});
