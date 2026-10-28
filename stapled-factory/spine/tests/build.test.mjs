import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, writeFile, chmod, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { buildStep, reconcileBuildStep, conversationId } from '../dist/build.js';

test('worker crash returns a deterministic unknown identity instead of retrying dispatch', async () => {
  assert.equal(conversationId('unit:0'), 'a030dd63-3a31-5c11-81a8-06fb9b84fec6');
  const tmp = await mkdtemp(path.join(os.tmpdir(), 'factory-crash-test-'));
  const oldPath = process.env.PATH;
  try {
    await writeFile(path.join(tmp, 'python3'), '#!/bin/sh\nexit 137\n');
    await chmod(path.join(tmp, 'python3'), 0o700);
    process.env.PATH = tmp + path.delimiter + oldPath;
    const db = path.join(tmp, 'attempts'); await writeFile(db, '');
    await mkdir(path.join(tmp, 'anchor')); const anchor = path.join(tmp, 'anchor', 'watermark'); await writeFile(anchor, '{}');
    const result = await buildStep({ attemptId: 'unit:0', attemptDb: db, attemptAnchor: anchor, targetRepo: '/repo', taskFile: '/task' });
    assert.equal(result.status, 'unknown');
    assert.equal(result.conversation_id, conversationId('unit:0'));
  } finally { process.env.PATH = oldPath; await rm(tmp, {recursive: true, force: true}); }
});

test('requires a durable absolute attempt store before launching a worker', async () => {
  const before = process.env.FACTORY_ATTEMPT_DB;
  try {
    delete process.env.FACTORY_ATTEMPT_DB;
    const result = await buildStep({ attemptId: 'unit:0', attemptAnchor: '', attemptDb: process.env.FACTORY_ATTEMPT_DB ?? '', targetRepo: '/repo', taskFile: '/task' });
    assert.equal(result.status, 'blocked');
    process.env.FACTORY_ATTEMPT_DB = 'relative.db';
    assert.equal((await buildStep({ attemptId: 'unit:0', attemptAnchor: '', attemptDb: process.env.FACTORY_ATTEMPT_DB ?? '', targetRepo: '/repo', taskFile: '/task' })).status, 'blocked');
    assert.equal((await buildStep({ attemptId: 'unit:0', attemptAnchor: '', attemptDb: path.join(os.tmpdir(), 'factory-nonexistent-attempt-store'), targetRepo: '/repo', taskFile: '/task' })).status, 'blocked');
  } finally {
    if (before === undefined) delete process.env.FACTORY_ATTEMPT_DB;
    else process.env.FACTORY_ATTEMPT_DB = before;
  }
});

test('passes the stable attempt identity and durable store to the worker', async () => {
  const tmp = await mkdtemp(path.join(os.tmpdir(), 'factory-build-test-'));
  const oldPath = process.env.PATH;
  const oldDb = process.env.FACTORY_ATTEMPT_DB;
  const oldCapture = process.env.FACTORY_TEST_CAPTURE;
  try {
    const stub = path.join(tmp, 'python3');
    const capture = path.join(tmp, 'args');
    await writeFile(stub, '#!/bin/sh\nprintf "%s\\n" "$@" > "$FACTORY_TEST_CAPTURE"\nprintf \'{"status":"unknown","git_revision":""}\\n\'\n');
    await chmod(stub, 0o700);
    process.env.PATH = tmp + path.delimiter + oldPath;
    process.env.FACTORY_TEST_CAPTURE = capture;
    process.env.FACTORY_ATTEMPT_DB = path.join(tmp, 'attempts.db');
    await writeFile(process.env.FACTORY_ATTEMPT_DB, '');
    await mkdir(path.join(tmp, 'anchor'));
    const anchor = path.join(tmp, 'anchor', 'watermark');
    await writeFile(anchor, '{}');
    const result = await buildStep({ attemptId: 'unit:2', attemptAnchor: anchor, attemptDb: process.env.FACTORY_ATTEMPT_DB, targetRepo: '/repo', taskFile: '/task' });
    assert.equal(result.status, 'unknown');
    const args = (await readFile(capture, 'utf8')).trim().split('\n');
    assert.equal(args[args.indexOf('--attempt-id') + 1], 'unit:2');
    assert.equal(args[args.indexOf('--attempt-db') + 1], process.env.FACTORY_ATTEMPT_DB);
    assert.equal(args[args.indexOf('--attempt-anchor') + 1], anchor);
    assert.equal(args[args.indexOf('--repo') + 1], '/repo');
    assert.equal(args[args.indexOf('--task-file') + 1], '/task');
    await reconcileBuildStep({ attemptId: 'unit:2', attemptAnchor: anchor, attemptDb: process.env.FACTORY_ATTEMPT_DB, targetRepo: '/repo', taskFile: '/task' });
    const reconcileArgs = (await readFile(capture, 'utf8')).trim().split('\n');
    assert.ok(reconcileArgs.includes('--reconcile-only'));
  } finally {
    process.env.PATH = oldPath;
    if (oldDb === undefined) delete process.env.FACTORY_ATTEMPT_DB; else process.env.FACTORY_ATTEMPT_DB = oldDb;
    if (oldCapture === undefined) delete process.env.FACTORY_TEST_CAPTURE; else process.env.FACTORY_TEST_CAPTURE = oldCapture;
    await rm(tmp, { recursive: true, force: true });
  }
});
