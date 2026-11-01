import test from 'node:test';
import assert from 'node:assert/strict';
import {createHmac,randomBytes} from 'node:crypto';
import {mkdtemp,writeFile,chmod,rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {workUnit} from '../dist/state.js';
test('signed validation rejects substitution, tamper, failed verdict and replay',async()=>{
const dir=await mkdtemp(path.join(os.tmpdir(),'validation-'));const old=process.env.FACTORY_VALIDATION_KEY_FILE;
try{const key=randomBytes(32),file=path.join(dir,'key');await writeFile(file,key,{mode:0o600});process.env.FACTORY_VALIDATION_KEY_FILE=file;
const good={unit_id:'unit',attempt_id:'unit:0:successor',git_revision:'a'.repeat(40),deployment_id:'container',artifact_digest:'sha256:'+'b'.repeat(64),scenario_digest:'c'.repeat(64),passed:true,scope:'disposable-candidate',security_engines:{sast:'regex'}};
const sign=v=>{const payload=JSON.stringify(v);return{payload,signature:createHmac('sha256',key).update(payload).digest('hex')}};
let state={stage:'needs_validation',successor:{attempt_id:good.attempt_id,status:'completed',git_revision:good.git_revision}};const ctx={key:'unit',run:async f=>f(),get:async()=>state,set:(_k,s)=>state=s};
for(const bad of [{...good,unit_id:'other'},{...good,attempt_id:'unit:0'},{...good,git_revision:'d'.repeat(40)}])assert.equal(await workUnit.object.acceptValidation(ctx,sign(bad)),false);
const bad=sign(good);bad.payload=JSON.stringify({...good,deployment_id:'attacker'});await assert.rejects(()=>workUnit.object.acceptValidation(ctx,bad),/signed evidence/);
await assert.rejects(()=>workUnit.object.acceptValidation(ctx,sign({...good,passed:false})),/signed evidence/);assert.equal(state.stage,'needs_validation');
assert.equal(await workUnit.object.acceptValidation(ctx,sign(good)),true);assert.equal(state.stage,'validated');assert.equal(await workUnit.object.acceptValidation(ctx,sign(good)),false);
await chmod(file,0o644);await assert.rejects(()=>workUnit.object.acceptValidation(ctx,sign(good)),/signed evidence/);
}finally{if(old===undefined)delete process.env.FACTORY_VALIDATION_KEY_FILE;else process.env.FACTORY_VALIDATION_KEY_FILE=old;await rm(dir,{recursive:true,force:true})}});

test('validated wall receipt is signed, revision bound and stored atomically', async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'validation-receipt-'));
  const previous = process.env.FACTORY_VALIDATION_KEY_FILE;
  try {
    const key = randomBytes(32);
    const file = path.join(dir, 'key');
    await writeFile(file, key, {mode: 0o600});
    process.env.FACTORY_VALIDATION_KEY_FILE = file;
    const revision = 'a'.repeat(40);
    const receipt = {git_revision: revision,
      verdict: {passed: true, aggregate_pass_rate: 1, failure_message: '', per_scenario: {}},
      usage: {tokens_in: 0, tokens_out: 0, requests: 15, retries: 0, wall_clock_ms: 100,
        tier: 'unknown', pricing_mode: 'subscription', cost_estimate_usd: null, ceiling_violation: ''}};
    const proof = {unit_id: 'unit', attempt_id: 'unit:0', git_revision: revision,
      deployment_id: 'd'.repeat(64), artifact_digest: 'sha256:' + 'b'.repeat(64),
      scenario_digest: 'c'.repeat(64), source_archive_digest: 'e'.repeat(64),
      checks_digest: 'f'.repeat(64), passed: true, scope: 'disposable-candidate',
      security_engines: {sast: 'fixture'}, receipt};
    const sign = value => {
      const payload = JSON.stringify(value);
      return {payload, signature: createHmac('sha256', key).update(payload).digest('hex')};
    };
    let state = {stage: 'needs_validation', receipt: null,
      build: {attempt_id: 'unit:0', status: 'completed', git_revision: revision}};
    const ctx = {key: 'unit', run: async f => f(), get: async () => state, set: (_key, value) => state = value};
    for (const bad of [
      {...receipt, git_revision: '0'.repeat(40)},
      {...receipt, verdict: {...receipt.verdict, passed: false}},
      {...receipt, usage: {...receipt.usage, requests: -1}},
    ]) {
      await assert.rejects(() => workUnit.object.acceptValidation(ctx, sign({...proof, receipt: bad})), /signed evidence/);
      assert.equal(state.stage, 'needs_validation');
      assert.equal(state.receipt, null);
    }
    assert.equal(await workUnit.object.acceptValidation(ctx, sign(proof)), true);
    assert.deepEqual(state.receipt, receipt);
    assert.equal(state.stage, 'validated');
    assert.equal(await workUnit.object.acceptValidation(ctx, sign(proof)), false);
  } finally {
    if (previous === undefined) delete process.env.FACTORY_VALIDATION_KEY_FILE;
    else process.env.FACTORY_VALIDATION_KEY_FILE = previous;
    await rm(dir, {recursive: true, force: true});
  }
});
