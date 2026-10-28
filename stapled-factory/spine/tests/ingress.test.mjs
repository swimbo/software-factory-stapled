import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer, request } from 'node:http';
import { mkdtemp, writeFile, chmod, symlink, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createIngress, readIngressToken } from '../dist/ingress.js';
import { workUnit } from '../dist/state.js';

function listen(server) { return new Promise(resolve => server.listen(0, '127.0.0.1', () => resolve(server.address().port))); }
function call(port, route, auth, body = '{}') { return new Promise((resolve, reject) => {
  const req = request({host:'127.0.0.1', port, path:route, method:'POST', headers:{'content-type':'application/json', ...(auth ? {authorization:auth} : {})}}, res => {
    let data=''; res.on('data', x => data += x); res.on('end', () => resolve([res.statusCode, data]));
  }); req.on('error', reject); req.end(body);
}); }
test('ingress credential must be an owner-only regular file', async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'factory-ingress-token-'));
  const token = path.join(dir, 'token');
  try {
    await writeFile(token, 'a'.repeat(43), {mode: 0o600});
    assert.equal(readIngressToken(token), 'a'.repeat(43));
    await chmod(token, 0o644);
    assert.throws(() => readIngressToken(token), /owner-only/);
    await chmod(token, 0o600);
    const link = path.join(dir, 'link'); await symlink(token, link);
    assert.throws(() => readIngressToken(link), /owner-only/);
  } finally { await rm(dir, {recursive: true, force: true}); }
});
test('local ingress authenticates and forwards only approved methods without forwarding credentials', async () => {
  const seen = [];
  const upstream = createServer((req, res) => {seen.push([req.url, req.headers.authorization]); res.end('ok');});
  const upstreamPort = await listen(upstream), token = 'a'.repeat(43);
  const ingress = createIngress(token, upstreamPort), port = await listen(ingress);
  try {
    assert.equal((await call(port, '/orchestrate/unit/run', null))[0], 401);
    assert.equal((await call(port, '/orchestrate/unit/run', 'Bearer '+ 'b'.repeat(43)))[0], 401);
    assert.equal((await call(port, '/Reconciliation/unit/restart', null))[0], 401);
    assert.equal((await call(port, '/WorkUnit/unit/transition', 'Bearer '+token))[0], 404);
    assert.equal((await call(port, '/orchestrate/unit/run/send', 'Bearer '+token))[0], 404);
    assert.equal((await call(port, '/orchestrate/unit/run?x=1', 'Bearer '+token))[0], 404);
    assert.equal((await call(port, '/orchestrate/unit/run', 'Bearer '+token, 'a'.repeat(65537)))[0], 413);
    assert.deepEqual(await call(port, '/orchestrate/unit/run', 'Bearer '+token), [200,'ok']);
    assert.deepEqual(await call(port, '/Reconciliation/unit/restart', 'Bearer '+token), [200,'ok']);
    assert.deepEqual(seen, [['/orchestrate/unit/run', undefined], ['/Reconciliation/unit/restart', undefined]]);
  } finally { ingress.close(); upstream.close(); }
});

test('state cannot skip validation or accept caller receipts', async () => {
 let state = {stage:'unclaimed', claim:null, receipt:null, attempts:0, build:null};
 const ctx = {key:'unit', get:async()=>state, set:(_k,v)=>{state=v;}};
 await assert.rejects(()=>workUnit.object.transition(ctx,'merged'), /Invalid stage/);
 assert.equal(await workUnit.object.claim(ctx,'worker'),true);
 await workUnit.object.transition(ctx,'building');
 await assert.rejects(()=>workUnit.object.transition(ctx,'needs_validation'), /Invalid stage/);
 await assert.rejects(()=>workUnit.object.recordReceipt(ctx,{}), /not enabled/);
 await workUnit.object.recordRecoveryContext(ctx,{request:{unitId:'unit'},attemptDb:'/db'});
 await assert.rejects(()=>workUnit.object.recordBuild(ctx,{attempt_id:'other:0',status:'completed'}), /Invalid build/);
 await workUnit.object.recordBuild(ctx,{attempt_id:'unit:0',status:'completed',git_revision:'a'.repeat(40)});
 await workUnit.object.transition(ctx,'needs_validation');
 await assert.rejects(()=>workUnit.object.transition(ctx,'gated'), /Invalid stage/);
 assert.equal(state.stage,'needs_validation');
});
