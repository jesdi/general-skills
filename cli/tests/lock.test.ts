import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test } from 'vitest';
import { withLock } from '../src/lock.js';

test('withLock runs critical sections one at a time and releases after a failure', async () => {
  const lock = join(mkdtempSync(join(tmpdir(), 'lock-')), 'x.lock');
  let inside = 0;
  let overlap = false;
  const run = (fail = false) =>
    withLock(lock, async () => {
      overlap ||= ++inside > 1;
      await new Promise((r) => setTimeout(r, 20));
      inside--;
      if (fail) throw new Error('boom');
    });
  await Promise.all([run(), run(), run(true).catch(() => {})]);
  await run();
  expect(overlap).toBe(false);
});
