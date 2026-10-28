import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, writeFile, chmod, rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {runWall} from '../dist/wall.js';

test('wall invocation defaults to triple runs and preserves explicit run count', async () => {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'wall-args-'));
  const oldPath = process.env.PATH;
  try {
    const executable = path.join(dir, 'python3');
    await writeFile(executable, `#!/bin/sh
printf '{"args":['
for arg do printf '"%s",' "$arg"; done
printf '"end"]}'
`);
    await chmod(executable, 0o700);
    process.env.PATH = `${dir}:${oldPath}`;
    for (const [runs, expected] of [[undefined, '3'], [5, '5']]) {
      const result = await runWall({target: 'http://localhost', scenarios: '/holdout', runs});
      assert.equal(result.args[result.args.indexOf('--runs') + 1], expected);
    }
  } finally {
    process.env.PATH = oldPath;
    await rm(dir, {recursive: true, force: true});
  }
});
