import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, writeFile, readFile, chmod, rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {runWall} from '../dist/wall.js';

test('wall invocation defaults to triple runs and preserves explicit run count', async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'wall-args-'));
  const oldPath = process.env.PATH;
  const oldArgs = process.env.WALL_ARGS_PATH;
  try {
    const executable = path.join(dir, 'python3');
    await writeFile(executable, `#!/bin/sh
printf '%s\\n' "$@" > "$WALL_ARGS_PATH"
printf '%s' '{"verdict":{"passed":false,"aggregate_pass_rate":0.5,"failure_message":"private-marker /private/path","per_scenario":{"private-marker":false}},"receipt":{"tokens_in":0,"tokens_out":0,"requests":3,"retries":0,"wall_clock_ms":1,"tier":"unknown","pricing_mode":"subscription","cost_estimate_usd":null,"ceiling_violation":"","private":"private-marker"}}'
`);
    await chmod(executable, 0o700);
    process.env.PATH = `${dir}:${oldPath}`;
    process.env.WALL_ARGS_PATH = path.join(dir, 'args');
    for (const [runs, expected] of [[undefined, '3'], [5, '5']]) {
      const result = await runWall({target: 'http://localhost', scenarios: '/holdout', runs});
      const args = (await readFile(process.env.WALL_ARGS_PATH, 'utf8')).trim().split('\n');
      assert.equal(args[args.indexOf('--runs') + 1], expected);
      assert.deepEqual(result.verdict.per_scenario, {});
      assert.equal(result.verdict.failure_message, 'A holdout scenario did not meet its quorum');
      assert.doesNotMatch(JSON.stringify(result), /private-marker|\/private\/path/);
    }
    await writeFile(executable, '#!/bin/sh\nprintf "private-marker invalid-json"\n');
    await assert.rejects(() => runWall({target:'http://localhost',scenarios:'/holdout'}),
      error => error.message === 'Wall evaluation unavailable');
    await writeFile(executable, '#!/bin/sh\nprintf "private-marker" >&2\nexit 1\n');
    await assert.rejects(() => runWall({target:'http://localhost',scenarios:'/holdout'}),
      error => error.message === 'Wall evaluation unavailable');
  } finally {
    if (oldArgs === undefined) delete process.env.WALL_ARGS_PATH; else process.env.WALL_ARGS_PATH = oldArgs;
    process.env.PATH = oldPath;
    await rm(dir, {recursive: true, force: true});
  }
});
