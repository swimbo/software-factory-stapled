import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, chmod, readFile, rm } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { orchestrate, reconcile, reconciliation } from '../dist/orchestrate.js';
import { workUnit } from '../dist/state.js';

test('atomic recovered result cannot overwrite another attempt or a later stage', async () => {
  let state = {stage: 'needs_reconciliation', build: {attempt_id: 'unit:0', conversation_id: 'c'}, claim: null, receipt: null, attempts: 0};
  const ctx = { get: async () => state, set: (_k, value) => { state = value; } };
  const result = {attempt_id: 'unit:0', conversation_id: 'c', status: 'completed', git_revision: 'a'.repeat(40)};
  assert.equal(await workUnit.object.completeReconciliation(ctx, {...result, conversation_id: 'wrong'}), false);
  assert.equal(state.stage, 'needs_reconciliation');
  assert.equal(await workUnit.object.completeReconciliation(ctx, result), true);
  assert.equal(state.stage, 'needs_validation');
  assert.equal(await workUnit.object.completeReconciliation(ctx, {...result, git_revision: 'b'.repeat(40)}), false);
  assert.equal(state.build.git_revision, 'a'.repeat(40));
});

test('repeatable observation rejects a different stored conversation before contacting worker', async () => {
  const ctx = context({stage: 'needs_reconciliation', build: {attempt_id: 'unit:0', conversation_id: 'wrong'},
    recovery: {request: {unitId: 'unit'}, attemptDb: '/db', attemptAnchor: '/anchor'}});
  ctx.run = async () => { throw Error('unexpected worker call'); };
  await assert.rejects(() => reconciliation.object.observe(ctx), /identity mismatch/);
});

function context(state) {
  const transitions = [];
  return { transitions, key: 'unit', objectClient: () => ({
    getState: async () => state,
    transition: async s => transitions.push(s),
    recordBuild: async () => { throw Error('unexpected build update'); },
  }), run: async fn => fn(), sleep: async () => { throw Error('poll suspended'); } };
}

test('reconciliation rejects missing original recovery context', async () => {
  const ctx = context({ stage: 'needs_reconciliation', build: { attempt_id: 'unit:0' } });
  await assert.rejects(() => reconcile.workflow.run(ctx), /No original attempt/);
});

test('reconciliation rejects a conversation belonging to another attempt', async () => {
  const ctx = context({ stage: 'needs_reconciliation', build: { attempt_id: 'unit:0', conversation_id: 'wrong' },
    recovery: { request: { unitId: 'unit' }, attemptDb: '/db', attemptAnchor: '/anchor' } });
  await assert.rejects(() => reconcile.workflow.run(ctx), /identity mismatch/);
});

test('unknown reconciliation only polls the same attempt and cannot dispatch', async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'reconcile-'));
  const oldPath = process.env.PATH, oldCapture = process.env.TEST_RECONCILE_CAPTURE;
  try {
    const db = path.join(dir, 'db'); await writeFile(db, '');
    await mkdir(path.join(dir, 'anchor')); const anchor = path.join(dir, 'anchor', 'watermark'); await writeFile(anchor, '{}');
    const capture = path.join(dir, 'capture');
    const python = path.join(dir, 'python3');
    await writeFile(python, '#!/bin/sh\nprintf "%s\\n" "$@" > "$TEST_RECONCILE_CAPTURE"\nprintf \'{"status":"unknown","git_revision":""}\\n\'\n');
    await chmod(python, 0o700); process.env.PATH = dir + path.delimiter + oldPath; process.env.TEST_RECONCILE_CAPTURE = capture;
    const ctx = context({ stage: 'needs_reconciliation', build: { attempt_id: 'unit:0', conversation_id: 'a030dd63-3a31-5c11-81a8-06fb9b84fec6' }, recovery: { attemptDb: db, attemptAnchor: anchor, request: { unitId: 'unit', targetRepo: '/repo', taskFile: '/task' } } });
    await assert.rejects(() => reconcile.workflow.run(ctx), /poll suspended/);
    const args = (await readFile(capture, 'utf8')).split('\n');
    assert.ok(args.includes('--reconcile-only'));
    assert.equal(args[args.indexOf('--attempt-id') + 1], 'unit:0');
    assert.deepEqual(ctx.transitions, []);
  } finally { process.env.PATH = oldPath; if (oldCapture === undefined) delete process.env.TEST_RECONCILE_CAPTURE; else process.env.TEST_RECONCILE_CAPTURE = oldCapture; await rm(dir, { recursive: true, force: true }); }
});

test('blocked reconciliation stops without claiming completion or sleeping', async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'reconcile-blocked-'));
  const oldPath = process.env.PATH;
  try {
    const db = path.join(dir, 'db'); await writeFile(db, '');
    await mkdir(path.join(dir, 'anchor')); const anchor = path.join(dir, 'anchor', 'watermark'); await writeFile(anchor, '{}');
    const python = path.join(dir, 'python3');
    await writeFile(python, '#!/bin/sh\nprintf \'{"status":"blocked","git_revision":""}\\n\'\n');
    await chmod(python, 0o700); process.env.PATH = dir + path.delimiter + oldPath;
    const ctx = context({ stage: 'needs_reconciliation', build: { attempt_id: 'unit:0', conversation_id: 'a030dd63-3a31-5c11-81a8-06fb9b84fec6' }, recovery: { attemptDb: db, attemptAnchor: anchor, request: { unitId: 'unit', targetRepo: '/repo', taskFile: '/task' } } });
    assert.equal(await reconcile.workflow.run(ctx), null);
    assert.deepEqual(ctx.transitions, []);
  } finally { process.env.PATH = oldPath; await rm(dir, { recursive: true, force: true }); }
});

test('recovered revision is held until wall target is bound to it', async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'reconcile-verified-'));
  const oldPath = process.env.PATH;
  try {
    const db = path.join(dir, 'db'); await writeFile(db, '');
    await mkdir(path.join(dir, 'anchor')); const anchor = path.join(dir, 'anchor', 'watermark'); await writeFile(anchor, '{}');
    const python = path.join(dir, 'python3');
    await writeFile(python, '#!/bin/sh\nprintf \'{"status":"completed","git_revision":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","conversation_id":"a030dd63-3a31-5c11-81a8-06fb9b84fec6"}\\n\'\n');
    await chmod(python, 0o700); process.env.PATH = dir + path.delimiter + oldPath;
    const state = { stage: 'needs_reconciliation', build: { attempt_id: 'unit:0', conversation_id: 'a030dd63-3a31-5c11-81a8-06fb9b84fec6' }, recovery: { attemptDb: db, attemptAnchor: anchor, request: { unitId: 'unit', targetRepo: '/repo', taskFile: '/task' } } };
    const updates = []; const stages = [];
    const ctx = { key: 'unit', objectClient: () => ({getState: async () => state, completeReconciliation: async b => {updates.push(b); stages.push('needs_validation'); return true;}}), run: async fn => fn() };
    assert.equal(await reconcile.workflow.run(ctx), null);
    assert.equal(updates[0].conversation_id, 'a030dd63-3a31-5c11-81a8-06fb9b84fec6');
    assert.equal(updates[0].git_revision, 'a'.repeat(40));
    assert.deepEqual(stages, ['needs_validation']);
  } finally { process.env.PATH = oldPath; await rm(dir, {recursive: true, force: true}); }
});

test('newly completed build cannot validate an unbound target', async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'build-unbound-'));
  const oldPath = process.env.PATH, oldDb = process.env.FACTORY_ATTEMPT_DB, oldAnchor = process.env.FACTORY_ATTEMPT_ANCHOR;
  try {
    const db = path.join(dir, 'db'); await writeFile(db, '');
    await mkdir(path.join(dir, 'anchor')); const anchor = path.join(dir, 'anchor', 'watermark'); await writeFile(anchor, '{}');
    const python = path.join(dir, 'python3');
    await writeFile(python, '#!/bin/sh\nprintf \'{"status":"completed","git_revision":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","conversation_id":"a030dd63-3a31-5c11-81a8-06fb9b84fec6"}\\n\'\n');
    await chmod(python, 0o700); process.env.PATH = dir + path.delimiter + oldPath;
    process.env.FACTORY_ATTEMPT_DB = db; process.env.FACTORY_ATTEMPT_ANCHOR = anchor;
    const updates = [], stages = [];
    const ctx = {key: 'unit', objectClient: () => ({claim: async () => true, transition: async s => stages.push(s), recordRecoveryContext: async () => {}, recordBuild: async b => updates.push(b)}), run: async fn => fn(), serviceClient: () => {throw Error('unexpected gate');}};
    assert.equal(await orchestrate.workflow.run(ctx, {unitId:'unit', target:'https://example.test', scenarios:'/holdout', taskFile:'/task'}), null);
    assert.deepEqual(stages, ['building', 'needs_validation']);
    assert.equal(updates.at(-1).git_revision, 'a'.repeat(40));
  } finally {
    process.env.PATH = oldPath;
    if (oldDb === undefined) delete process.env.FACTORY_ATTEMPT_DB; else process.env.FACTORY_ATTEMPT_DB = oldDb;
    if (oldAnchor === undefined) delete process.env.FACTORY_ATTEMPT_ANCHOR; else process.env.FACTORY_ATTEMPT_ANCHOR = oldAnchor;
    await rm(dir, {recursive: true, force: true});
  }
});

test('blocked build retains identity; uncertain build schedules reconciliation', async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'build-reconcile-'));
  const oldPath = process.env.PATH, oldDb = process.env.FACTORY_ATTEMPT_DB, oldAnchor = process.env.FACTORY_ATTEMPT_ANCHOR;
  try {
    const db = path.join(dir, 'db'); await writeFile(db, '');
    await mkdir(path.join(dir, 'anchor')); const anchor = path.join(dir, 'anchor', 'watermark'); await writeFile(anchor, '{}');
    const python = path.join(dir, 'python3');
    await chmod(await writeFile(python, '#!/bin/sh\nprintf \'{"status":"blocked","git_revision":""}\\n\'\n').then(() => python), 0o700);
    process.env.PATH = dir + path.delimiter + oldPath;
    process.env.FACTORY_ATTEMPT_DB = db; process.env.FACTORY_ATTEMPT_ANCHOR = anchor;
    const updates = [], stages = [], sent = [];
    const ctx = {key: 'unit', objectClient: () => ({claim: async () => true, transition: async s => stages.push(s), recordRecoveryContext: async () => {}, recordBuild: async b => updates.push(b)}), run: async fn => fn(), workflowSendClient: (definition, key) => ({run: () => sent.push([definition.name, key])})};
    await orchestrate.workflow.run(ctx, {unitId:'unit', target:'https://example.test', scenarios:'/holdout', taskFile:'/task'});
    assert.deepEqual(stages, ['building', 'needs_reconciliation']);
    assert.equal(updates.at(-1).conversation_id, 'a030dd63-3a31-5c11-81a8-06fb9b84fec6');
    assert.deepEqual(sent, []);
    await writeFile(python, '#!/bin/sh\nprintf \'{"status":"unknown","git_revision":"","conversation_id":"a030dd63-3a31-5c11-81a8-06fb9b84fec6"}\\n\'\n');
    await orchestrate.workflow.run(ctx, {unitId:'unit', target:'https://example.test', scenarios:'/holdout', taskFile:'/task'});
    assert.deepEqual(sent, [['reconcileBuild', 'unit']]);
  } finally {
    process.env.PATH = oldPath;
    if (oldDb === undefined) delete process.env.FACTORY_ATTEMPT_DB; else process.env.FACTORY_ATTEMPT_DB = oldDb;
    if (oldAnchor === undefined) delete process.env.FACTORY_ATTEMPT_ANCHOR; else process.env.FACTORY_ATTEMPT_ANCHOR = oldAnchor;
    await rm(dir, {recursive: true, force: true});
  }
});

test('successor reservation excludes late source completion and rejects conflicting result', async () => {
  const {conversationId} = await import('../dist/build.js');
  let state = {stage: 'needs_reconciliation', build: {attempt_id: 'unit:0', conversation_id: conversationId('unit:0'), status: 'unknown'},
    recovery: {request: {unitId: 'unit'}, attemptDb: '/db', attemptAnchor: '/anchor'}, claim: null, receipt: null, attempts: 0};
  const ctx = {key: 'unit', get: async () => state, set: (_k, value) => {state = value;}};
  assert.equal(await workUnit.object.beginSuccessor(ctx), true);
  assert.equal(await workUnit.object.beginSuccessor(ctx), false);
  assert.equal(await workUnit.object.completeReconciliation(ctx, {...state.build, status: 'completed', git_revision:'a'.repeat(40)}), false);
  const result = {attempt_id:'unit:0:successor', conversation_id:conversationId('unit:0:successor'), status:'completed', git_revision:'b'.repeat(40)};
  assert.equal(await workUnit.object.recordSuccessor(ctx, {...result, conversation_id:'other'}), false);
  assert.equal(state.stage, 'needs_reconciliation');
  assert.equal(await workUnit.object.recordSuccessor(ctx, result), true);
  assert.equal(state.stage, 'needs_validation');
  assert.equal(await workUnit.object.recordSuccessor(ctx, result), false);
});

test('unavailable successor preflight leaves original state retryable and performs no effect', async () => {
  const {conversationId} = await import('../dist/build.js');
  const dir = await mkdtemp(path.join(os.tmpdir(), 'restart-blocked-'));
  try {
    const db = path.join(dir, 'db'); await writeFile(db, '');
    await mkdir(path.join(dir, 'anchor')); const anchor = path.join(dir, 'anchor', 'watermark'); await writeFile(anchor, '{}');
    let state = {stage:'needs_reconciliation', build:{attempt_id:'unit:0', conversation_id:conversationId('unit:0'), status:'unknown'},
      recovery:{request:{unitId:'unit', targetRepo:'/repo', taskFile:'/task'}, attemptDb:db, attemptAnchor:anchor}, claim:null, receipt:null, attempts:0};
    const stateCtx = {key:'unit', get:async () => state, set:(_k, v) => {state = v;}};
    let runs = 0;
    const client = {getState:async () => state, beginSuccessor:() => workUnit.object.beginSuccessor(stateCtx),
      recordSuccessor:b => workUnit.object.recordSuccessor(stateCtx, b)};
    const ctx = {key:'unit', objectClient:() => client, run:async fn => {runs++; return fn();}};
    assert.equal(await reconciliation.object.restart(ctx), 'blocked');
    assert.equal(state.build.attempt_id, 'unit:0');
    assert.equal(state.successor, undefined);
    assert.equal(await reconciliation.object.restart(ctx), 'blocked');
    assert.equal(runs, 2); // one side-effect-free worker preflight per invocation
  } finally {await rm(dir, {recursive:true, force:true});}
});

test('preflight and dispatch use the worker CLI with the stored recovery identity', async () => {
  const {conversationId} = await import('../dist/build.js');
  const dir = await mkdtemp(path.join(os.tmpdir(), 'restart-cli-'));
  const oldPath = process.env.PATH, oldCapture = process.env.TEST_RECONCILE_CAPTURE;
  try {
    const db = path.join(dir, 'db'); await writeFile(db, '');
    await mkdir(path.join(dir, 'anchor')); const anchor = path.join(dir, 'anchor', 'watermark'); await writeFile(anchor, '{}');
    const capture = path.join(dir, 'capture'), python = path.join(dir, 'python3');
    const cid = conversationId('unit:0:successor');
    await writeFile(python, `#!/bin/sh\nprintf '%s\\n' "$*" >> "$TEST_RECONCILE_CAPTURE"\ncase "$*" in *--successor-preflight-only*) printf '{"status":"ready","git_revision":""}\\n';; *--successor-only*) printf '{"status":"completed","git_revision":"${'c'.repeat(40)}","conversation_id":"${cid}"}\\n';; *) exit 1;; esac\n`);
    await chmod(python, 0o700); process.env.PATH = dir + path.delimiter + oldPath; process.env.TEST_RECONCILE_CAPTURE = capture;
    let state = {stage:'needs_reconciliation', build:{attempt_id:'unit:0', conversation_id:conversationId('unit:0'), status:'unknown'},
      recovery:{request:{unitId:'unit', targetRepo:'/repo', taskFile:'/task'}, attemptDb:db, attemptAnchor:anchor}, claim:null, receipt:null, attempts:0};
    const stateCtx = {key:'unit', get:async () => state, set:(_k,v) => {state = v;}};
    const client = {getState:async () => state, beginSuccessor:() => workUnit.object.beginSuccessor(stateCtx), recordSuccessor:b => workUnit.object.recordSuccessor(stateCtx,b)};
    const ctx = {key:'unit', objectClient:() => client, run:async fn => fn()};
    assert.equal(await reconciliation.object.restart(ctx), 'needs_validation');
    assert.equal(state.successor.git_revision, 'c'.repeat(40));
    const calls = (await readFile(capture, 'utf8')).trim().split('\n');
    assert.equal(calls.length, 2);
    assert.match(calls[0], /--attempt-id unit:0 .*--successor-preflight-only/);
    assert.match(calls[1], /--attempt-id unit:0 .*--successor-only/);
  } finally {
    process.env.PATH = oldPath;
    if (oldCapture === undefined) delete process.env.TEST_RECONCILE_CAPTURE; else process.env.TEST_RECONCILE_CAPTURE = oldCapture;
    await rm(dir, {recursive:true, force:true});
  }
});

test('unknown successor is observed without redispatch and only its revision is accepted', async () => {
  const {conversationId} = await import('../dist/build.js');
  const dir = await mkdtemp(path.join(os.tmpdir(), 'observe-successor-'));
  const oldPath = process.env.PATH, oldCapture = process.env.TEST_RECONCILE_CAPTURE;
  try {
    const db = path.join(dir, 'db'); await writeFile(db, '');
    await mkdir(path.join(dir, 'anchor')); const anchor = path.join(dir, 'anchor', 'watermark'); await writeFile(anchor, '{}');
    const capture = path.join(dir, 'capture');
    const python = path.join(dir, 'python3');
    const cid = conversationId('unit:0:successor');
    await writeFile(python, `#!/bin/sh\nprintf '%s\\n' "$@" > "$TEST_RECONCILE_CAPTURE"\nprintf '{"status":"completed","git_revision":"${'b'.repeat(40)}","conversation_id":"${cid}"}\\n'\n`);
    await chmod(python, 0o700); process.env.PATH = dir + path.delimiter + oldPath; process.env.TEST_RECONCILE_CAPTURE = capture;
    let state = {stage:'needs_reconciliation', build:{attempt_id:'unit:0', conversation_id:conversationId('unit:0'), status:'unknown'},
      successor:{attempt_id:'unit:0:successor', conversation_id:cid, status:'unknown'},
      recovery:{request:{unitId:'unit', targetRepo:'/repo', taskFile:'/task'}, attemptDb:db, attemptAnchor:anchor}, claim:null, receipt:null, attempts:0};
    const stateCtx = {key:'unit', get:async () => state, set:(_k,v) => {state = v;}};
    const client = {getState:async () => state, completeSuccessorReconciliation:b => workUnit.object.completeSuccessorReconciliation(stateCtx,b)};
    const ctx = {key:'unit', objectClient:() => client, run:async fn => fn()};
    assert.equal(await reconciliation.object.observe(ctx), 'needs_validation');
    assert.equal(state.successor.git_revision, 'b'.repeat(40));
    assert.equal(state.build.status, 'unknown');
    const args = (await readFile(capture, 'utf8')).split('\n');
    assert.ok(args.includes('--successor-reconcile-only'));
    assert.ok(!args.includes('--successor-only'));
    assert.equal(args[args.indexOf('--attempt-id') + 1], 'unit:0');
    assert.equal(await workUnit.object.completeSuccessorReconciliation(stateCtx, state.successor), false);
  } finally {
    process.env.PATH = oldPath;
    if (oldCapture === undefined) delete process.env.TEST_RECONCILE_CAPTURE; else process.env.TEST_RECONCILE_CAPTURE = oldCapture;
    await rm(dir, {recursive:true, force:true});
  }
});

test('successor reconciliation rejects a wrong identity and a stale state', async () => {
  const {conversationId} = await import('../dist/build.js');
  let state = {stage:'needs_reconciliation', build:{attempt_id:'unit:0', conversation_id:conversationId('unit:0'), status:'unknown'},
    successor:{attempt_id:'unit:0:successor', conversation_id:conversationId('unit:0:successor'), status:'unknown'},
    recovery:{request:{unitId:'unit'}, attemptDb:'/db', attemptAnchor:'/anchor'}};
  const ctx = {key:'unit', get:async () => state, set:(_k,v) => {state=v;}};
  const evidence = {...state.successor, status:'completed', git_revision:'a'.repeat(40)};
  assert.equal(await workUnit.object.completeSuccessorReconciliation(ctx, {...evidence, conversation_id:'wrong'}), false);
  assert.equal(await workUnit.object.completeSuccessorReconciliation(ctx, {...evidence, git_revision:'not-a-sha'}), false);
  state.stage = 'failed';
  assert.equal(await workUnit.object.completeSuccessorReconciliation(ctx, evidence), false);
});
