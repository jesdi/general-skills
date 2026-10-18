import { execFileSync, spawnSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';

const runner = resolve('scripts/test-python.sh');
const dirs: string[] = [];

afterEach(() => {
  for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true });
});

function fixture() {
  const root = mkdtempSync(join(tmpdir(), 'python-check-'));
  dirs.push(root);
  const bin = join(root, 'bin');
  const local = join(root, '.venv', 'bin');
  mkdirSync(bin);
  mkdirSync(local, { recursive: true });
  const log = join(root, 'calls');
  writeFileSync(join(bin, 'python3'), '#!/bin/sh\necho host >> "$CALLS"\nexit 99\n', { mode: 0o755 });
  writeFileSync(join(local, 'python'), '#!/bin/sh\necho "$*" >> "$CALLS"\ncase "$*" in *pytest*) exit "${PYTHON_TEST_EXIT:-0}";; esac\n', { mode: 0o755 });
  const env = { ...process.env, PATH: `${bin}:${process.env.PATH}`, VIRTUAL_ENV: '', CALLS: log };
  return { root, log, env };
}

describe('Python check runner', () => {
  it('uses the project venv instead of an incompatible host Python without activation', () => {
    const { root, log, env } = fixture();
    execFileSync('sh', [runner], { cwd: root, env });
    expect(readFileSync(log, 'utf8').trim().split('\n')).toEqual([
      '-m coverage erase',
      '-m coverage run -m pytest skills/backlog skills/crap-gate skills/pr-risk -q',
      '-m coverage run -a -m pytest skills/implement-spec -q',
      '-m coverage run -m pytest skills/test-prune -q',
      '-m coverage combine --append -q',
      '-m coverage json -o .crap/python.json',
    ]);
  });

  it('preserves the interpreter selected by an active virtual environment', () => {
    const { root, log, env } = fixture();
    const result = spawnSync('sh', [runner], { cwd: root, env: { ...env, VIRTUAL_ENV: '/active' } });
    expect(result.status).toBe(99);
    expect(readFileSync(log, 'utf8')).toBe('host\n');
  });

  it('stops on test failure before appending coverage or reporting success', () => {
    const { root, log, env } = fixture();
    const result = spawnSync('sh', [runner], { cwd: root, env: { ...env, PYTHON_TEST_EXIT: '1' } });
    expect(result.status).toBe(1);
    expect(readFileSync(log, 'utf8').trim().split('\n')).toEqual([
      '-m coverage erase',
      '-m coverage run -m pytest skills/backlog skills/crap-gate skills/pr-risk -q',
    ]);
  });
});
