import { execFileSync, spawnSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';

const runner = resolve('scripts/check-fast.sh');
const dirs: string[] = [];

afterEach(() => {
  for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true });
});

function fixture() {
  const root = mkdtempSync(join(tmpdir(), 'check-fast-'));
  dirs.push(root);
  const bin = join(root, 'bin');
  const local = join(root, '.venv', 'bin');
  mkdirSync(bin);
  mkdirSync(local, { recursive: true });
  const log = join(root, 'calls');
  writeFileSync(join(bin, 'pnpm'), '#!/bin/sh\necho "pnpm $*" >> "$CALLS"\ncase "$*" in *tsc*) exit "${TSC_EXIT:-0}";; esac\n', { mode: 0o755 });
  writeFileSync(join(local, 'python'), '#!/bin/sh\necho "venv $*" >> "$CALLS"\n', { mode: 0o755 });
  const env = { ...process.env, PATH: `${bin}:${process.env.PATH}`, VIRTUAL_ENV: '', CALLS: log, IMPLEMENT_SPEC_LOCKED: '' };
  return { root, log, env };
}

describe('fast check runner', () => {
  it('type checks, then runs each locked Python file alone and the TypeScript files together', () => {
    const { root, log, env } = fixture();
    const locked = 'a/test_x.py b/test_x.py tests/one.test.ts tests/fixture.json tests/two.test.ts';
    execFileSync('sh', [runner], { cwd: root, env: { ...env, IMPLEMENT_SPEC_LOCKED: locked } });
    expect(readFileSync(log, 'utf8').trim().split('\n')).toEqual([
      'pnpm exec tsc --noEmit',
      'venv -m pytest -q a/test_x.py',
      'venv -m pytest -q b/test_x.py',
      'pnpm exec vitest run tests/one.test.ts tests/two.test.ts',
    ]);
  });

  it('takes its arguments over the locked list', () => {
    const { root, log, env } = fixture();
    execFileSync('sh', [runner, 'c/test_y.py'], { cwd: root, env: { ...env, IMPLEMENT_SPEC_LOCKED: 'a/test_x.py' } });
    expect(readFileSync(log, 'utf8').trim().split('\n')).toEqual(['pnpm exec tsc --noEmit', 'venv -m pytest -q c/test_y.py']);
  });

  it('stops before any test when the type check fails', () => {
    const { root, log, env } = fixture();
    const result = spawnSync('sh', [runner, 'a/test_x.py'], { cwd: root, env: { ...env, TSC_EXIT: '2' } });
    expect(result.status).toBe(2);
    expect(readFileSync(log, 'utf8')).toBe('pnpm exec tsc --noEmit\n');
  });
});
