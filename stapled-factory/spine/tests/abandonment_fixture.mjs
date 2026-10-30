// Isolated probe only. Never registered by production main.ts.
import * as restate from '@restatedev/restate-sdk';
import { createServer } from 'node:http2';
import { readFileSync } from 'node:fs';
import { workUnit } from '../dist/state.js';
const seed = process.env.FACTORY_ABANDON_TEST_SEED;
if (!seed) throw Error('test seed required');
const unit = restate.object({name: 'WorkUnit', handlers: {...workUnit.object,
  seed: async ctx => {
    if (await ctx.get('state')) throw new restate.TerminalError('already seeded');
    ctx.set('state', JSON.parse(readFileSync(seed, 'utf8'))); return true;
  },
}});
const server = createServer(restate.createEndpointHandler({services: [unit]}));
server.listen(0, '127.0.0.1', () => console.log(JSON.stringify({port:server.address().port})));
