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
