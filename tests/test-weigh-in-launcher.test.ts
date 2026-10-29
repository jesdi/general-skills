// Ticket tw/s3-skeleton/04: test-weigh-in wrapper and the launcher drift check.
// `pnpm check` runs on a throwaway copy of the repo. A pnpm shim stands in for the heavy steps
// (tests, build, tsc, python tests, crap gate) so only the launcher check can fail the run.
import { spawnSync } from 'node:child_process';
import { appendFileSync, copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';

const root = join(import.meta.dirname, '..');
const sandboxes: string[] = [];
afterEach(() => sandboxes.splice(0).forEach((d) => rmSync(d, { recursive: true, force: true })));

const run = (cmd: string, args: string[], cwd: string, env: NodeJS.ProcessEnv = process.env) => {
  const r = spawnSync(cmd, args, { cwd, env, encoding: 'utf8' });
  return { code: r.status, out: `${r.stdout}${r.stderr}` };
};

function copyOfRepo() {
  const dir = mkdtempSync(join(tmpdir(), 'launcher-drift-'));
  sandboxes.push(dir);
  const files = spawnSync('git', ['ls-files', '-z'], { cwd: root, encoding: 'utf8' }).stdout.split('\0').filter(Boolean);
  for (const f of files) {
    if (!existsSync(join(root, f))) continue;
    mkdirSync(dirname(join(dir, f)), { recursive: true });
    copyFileSync(join(root, f), join(dir, f));
  }
  for (const nm of ['node_modules', 'cli/node_modules']) {
    if (existsSync(join(root, nm))) symlinkSync(join(root, nm), join(dir, nm));
  }
  const bin = join(dir, '.shim');
  mkdirSync(bin);
  const real = spawnSync('which', ['pnpm'], { encoding: 'utf8' }).stdout.trim();
  writeFileSync(
    join(bin, 'pnpm'),
    `#!/bin/sh\ncase "$1" in test:coverage|--filter|exec|test:python|crap-gate) exit 0 ;; esac\nexec "${real}" "$@"\n`,
    { mode: 0o755 },
  );
  const env = { ...process.env, PATH: `${bin}:${process.env.PATH}` };
  return { dir, pnpm: (...args: string[]) => run('pnpm', args, dir, env) };
}

// A wrapper that sources its launcher copy, for repos where test-weigh-in has no run.sh yet.
const ensureWrapper = (dir: string) => {
  const wrapper = join(dir, 'skills/test-weigh-in/run.sh');
  if (!existsSync(wrapper)) {
    writeFileSync(wrapper, '#!/bin/sh\nset -eu\nskill_dir=$(dirname -- "$0")\nname=test-weigh-in env_prefix=TEST_WEIGH_IN prefix=test-weigh-in fail_code=2 engine=weigh.py\n. "$skill_dir/launcher.sh"\n');
  }
};

describe('launcher drift check', () => {
  it('fails and names skills/crap-gate/launcher.sh when its copy has one byte more', () => {
    const { dir, pnpm } = copyOfRepo();
    appendFileSync(join(dir, 'skills/crap-gate/launcher.sh'), '\n');
    const r = pnpm('check');
    expect(r.code).not.toBe(0);
    expect(r.out).toContain('skills/crap-gate/launcher.sh');
  });

  it('sync-launchers then check leave stale copies equal to shared/launcher.sh', () => {
    const { dir, pnpm } = copyOfRepo();
    ensureWrapper(dir);
    appendFileSync(join(dir, 'shared/launcher.sh'), '# changed\n');
    for (const skill of ['crap-gate', 'test-weigh-in']) writeFileSync(join(dir, `skills/${skill}/launcher.sh`), '# stale\n');
    expect(pnpm('sync-launchers').code).toBe(0);
    const r = pnpm('check');
    expect(r.out).not.toContain('launcher.sh');
    expect(r.code).toBe(0);
    const shared = readFileSync(join(dir, 'shared/launcher.sh'), 'utf8');
    for (const skill of ['crap-gate', 'test-weigh-in']) {
      expect(readFileSync(join(dir, `skills/${skill}/launcher.sh`), 'utf8')).toBe(shared);
    }
  });

  it('fails and names skills/test-weigh-in when run.sh runs launcher.sh and the copy is missing', () => {
    const { dir, pnpm } = copyOfRepo();
    ensureWrapper(dir);
    rmSync(join(dir, 'skills/test-weigh-in/launcher.sh'), { force: true });
    const r = pnpm('check');
    expect(r.code).not.toBe(0);
    expect(r.out).toContain('skills/test-weigh-in');
  });
});

describe('test-weigh-in wrapper', () => {
  it('exits 2 when TEST_WEIGH_IN_CACHE_DIR is relative', () => {
    const r = run('sh', ['skills/test-weigh-in/run.sh'], root, { ...process.env, TEST_WEIGH_IN_CACHE_DIR: 'cache' });
    expect(r.code).toBe(2);
    expect(r.out).toContain('must be an absolute path');
  });
});
