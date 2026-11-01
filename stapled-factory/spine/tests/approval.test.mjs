import test from 'node:test';
import assert from 'node:assert/strict';
import { approvalStore } from '../dist/approval_store.js';
import { gate } from '../dist/gate.js';

const registration = () => ({ requestId: 'unit:deploy', gateType: 'initial_prod_deploy',
  awakeableId: 'original-awakeable', context: 'Deploy revision abc to production' });

function storeContext() {
  let value = null, clockReads = 0, writes = 0;
  return {
    key: 'unit:deploy',
    date: { now: async () => ++clockReads * 1000 },
    get: async () => structuredClone(value),
    set: (_key, next) => { value = structuredClone(next); writes++; },
    read: () => structuredClone(value),
    counts: () => ({ clockReads, writes }),
  };
}

test('identical registration preserves the original pending identity and timestamp', async () => {
  const ctx = storeContext();
  const original = await approvalStore.object.register(ctx, registration());
  assert.deepEqual(await approvalStore.object.register(ctx, registration()), original);
  assert.equal(ctx.read().requestedAtMs, 1000);
  assert.deepEqual(ctx.counts(), { clockReads: 1, writes: 1 });
});

test('registration cannot replace the request, context, gate or original awakeable', async () => {
  const ctx = storeContext();
  await approvalStore.object.register(ctx, registration());
  const original = ctx.read();
  for (const changed of [{ requestId: 'other' }, { context: 'Different revision' },
    { gateType: 'budget_raise' }, { awakeableId: 'replacement' }]) {
    await assert.rejects(() => approvalStore.object.register(ctx, { ...registration(), ...changed }), /approval/i);
    assert.deepEqual(ctx.read(), original);
  }
});

test('registration rejects malformed or unknown authority without writing', async () => {
  const ctx = storeContext();
  for (const changed of [{ gateType: 'root' }, { context: ' ' }, { context: 'x'.repeat(16385) },
    { awakeableId: '' }, { awakeableId: 'x'.repeat(2049) }, { requestId: '' },
    { requestId: 'x'.repeat(161) }]) {
    await assert.rejects(() => approvalStore.object.register(ctx, { ...registration(), ...changed }), /approval/i);
  }
  assert.deepEqual(ctx.counts(), { clockReads: 0, writes: 0 });
});

for (const decision of ['approved', 'rejected']) {
  test(`${decision} decision binds its approver and survives identical retries`, async () => {
    const ctx = storeContext();
    await approvalStore.object.register(ctx, registration());
    const request = { decision, approver: 'operator@example.test' };
    const original = await approvalStore.object.resolve(ctx, request);
    assert.equal(original.status, decision);
    assert.equal(original.awakeableId, registration().awakeableId);
    assert.equal(original.approver, request.approver);
    assert.equal(original.resolvedAtMs, 2000);
    assert.deepEqual(await approvalStore.object.resolve(ctx, request), original);
    assert.deepEqual(await approvalStore.object.register(ctx, registration()), original);
    assert.equal(await approvalStore.object.getAwakeableId(ctx), null);
    assert.deepEqual(ctx.counts(), { clockReads: 2, writes: 2 });
    for (const conflict of [{ decision: decision === 'approved' ? 'rejected' : 'approved', approver: request.approver },
      { decision, approver: 'different-operator' }]) {
      await assert.rejects(() => approvalStore.object.resolve(ctx, conflict), /approval/i);
      assert.deepEqual(ctx.read(), original);
    }
  });
}

test('unknown decisions and blank or unbounded identities cannot approve or reject', async () => {
  const ctx = storeContext();
  await approvalStore.object.register(ctx, registration());
  const original = ctx.read();
  for (const request of [{ decision: 'approve', approver: 'operator' },
    { decision: 'approved', approver: ' ' }, { decision: 'rejected', approver: 'x'.repeat(201) },
    { decision: 'approved', approver: null }]) {
    await assert.rejects(() => approvalStore.object.resolve(ctx, request), /approval/i);
    assert.deepEqual(ctx.read(), original);
  }
});

test('a decision without an original request is rejected', async () => {
  const ctx = storeContext();
  await assert.rejects(() => approvalStore.object.resolve(ctx, { decision: 'approved', approver: 'operator' }), /approval/i);
  assert.equal(ctx.read(), null);
});

test('gate recovery delivers only the original atomically resolved awakeable', async () => {
  const store = storeContext();
  await approvalStore.object.register(store, registration());
  const deliveries = [];
  let crash = true;
  const ctx = {
    objectClient: () => ({
      getAwakeableId: () => assert.fail('split read can lose the original after store commit'),
      resolve: request => approvalStore.object.resolve(store, request),
    }),
    resolveAwakeable: (id, decision) => {
      if (crash) { crash = false; throw Error('crash after durable decision'); }
      deliveries.push({ id, decision });
    },
  };
  const request = { requestId: registration().requestId, decision: 'approved', approver: 'operator' };
  await assert.rejects(() => gate.service.resolve(ctx, request), /crash after durable decision/);
  assert.equal(store.read().status, 'approved');
  await gate.service.resolve(ctx, request);
  assert.deepEqual(deliveries, [{ id: registration().awakeableId, decision: 'approved' }]);
  await assert.rejects(() => gate.service.resolve(ctx, { ...request, decision: 'rejected' }), /approval/i);
  assert.equal(deliveries.length, 1);
});

test('gate rejects malformed requests before allocating an awakeable', async () => {
  const ctx = {
    objectClient: () => assert.fail('invalid request must not address a store'),
    awakeable: () => assert.fail('invalid request must not allocate an awakeable'),
  };
  await assert.rejects(() => gate.service.request(ctx, { requestId: 'unit:deploy', gateType: 'root', context: 'text' }), /approval/i);
  await assert.rejects(() => gate.service.resolve(ctx, { requestId: '', decision: 'approved', approver: 'operator' }), /approval/i);
});
