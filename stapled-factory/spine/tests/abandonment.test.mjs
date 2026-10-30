import test from 'node:test';
import assert from 'node:assert/strict';
import { generateKeyPairSync, sign } from 'node:crypto';
import { mkdtempSync, writeFileSync, chmodSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { verifyAbandonment } from '../dist/abandonment.js';
import { workUnit } from '../dist/state.js';

test('operator signature is required and cannot cross from image approval domain', async () => {
  const dir=mkdtempSync(path.join(os.tmpdir(),'abandon-authority-'));const prior=process.env.FACTORY_IMAGE_APPROVAL_KEY;
  const {privateKey,publicKey}=generateKeyPairSync('rsa',{modulusLength:2048});const key=path.join(dir,'public.pem');
  writeFileSync(key,publicKey.export({type:'spki',format:'pem'}));chmodSync(key,0o600);process.env.FACTORY_IMAGE_APPROVAL_KEY=key;
  const evidence={kind:'operator-abandon-v1',unit_id:'unit',attempt_id:'unit:0',conversation_id:'conversation',allocation_id:'a'.repeat(32),worker_id:'b'.repeat(64),proxy_id:'c'.repeat(64),store_id:'store',generation:3,reason:'operator closure'};
  function envelope(e){const payload=JSON.stringify(e);return {payload,signature:sign('sha256',Buffer.from(payload),privateKey).toString('base64')};}
  try {
    assert.deepEqual(verifyAbandonment(envelope(evidence)),evidence);
    assert.throws(()=>verifyAbandonment({...envelope(evidence),payload:JSON.stringify({...evidence,reason:'changed'})}));
    assert.throws(()=>verifyAbandonment(envelope({...evidence,kind:'image-approval'})));
    assert.throws(()=>verifyAbandonment(envelope({...evidence,generation:-1})));
    // A valid signature alone cannot abandon a successful, successor, or wrong unit.
    for(const patch of [{stage:'validated'}, {stage:'needs_validation'}, {successor:{status:'unknown'}}, {build:{attempt_id:'other:0',conversation_id:'conversation',status:'unknown'}}]){
      let state={stage:'needs_reconciliation',build:{attempt_id:'unit:0',conversation_id:'conversation',status:'unknown'},recovery:{attemptDb:'/db',attemptAnchor:'/anchor'},...patch};
      let effects=0;
      const ctx={key:'unit',get:async()=>state,set:()=>assert.fail('unexpected state update'),run:async fn=>{effects++;if(effects>1)assert.fail('unexpected Docker/store effect');return fn();}};
      assert.equal(await workUnit.object.abandon(ctx,envelope(evidence)),false);
    }
    const ctx={key:'other',get:async()=>({stage:'needs_reconciliation',build:{attempt_id:'unit:0',conversation_id:'conversation',status:'unknown'},recovery:{attemptDb:'/db'}}),run:async fn=>fn(),set:()=>assert.fail()};
    assert.equal(await workUnit.object.abandon(ctx,envelope(evidence)),false);
  } finally {if(prior===undefined)delete process.env.FACTORY_IMAGE_APPROVAL_KEY;else process.env.FACTORY_IMAGE_APPROVAL_KEY=prior;rmSync(dir,{recursive:true,force:true});}
});

test('abandoned state cannot be reclaimed, reconciled, restarted or transitioned', async()=>{
  const state={stage:'abandoned',claim:null,receipt:null,attempts:0,
    build:{attempt_id:'unit:0',conversation_id:'conversation',status:'unknown'},recovery:{request:{unitId:'unit'}}};
  const ctx={key:'unit',get:async()=>state,set:()=>assert.fail('terminal state changed')};
  assert.equal(await workUnit.object.claim(ctx,'new-worker'),false);
  assert.equal(await workUnit.object.beginSuccessor(ctx),false);
  assert.equal(await workUnit.object.completeReconciliation(ctx,{...state.build,status:'completed',git_revision:'a'.repeat(40)}),false);
  await assert.rejects(()=>workUnit.object.transition(ctx,'building'),/Invalid stage transition/);
});
