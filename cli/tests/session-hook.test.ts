import { execFileSync } from 'node:child_process';
import { chmod, mkdtemp, mkdir, readFile, realpath, utimes, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { ensureSessionHook } from '../src/hook.js';

async function makeCtx(...configDirs: string[]) {
  const ctx = {
    home: await mkdtemp(join(tmpdir(), 'home-')),
    project: await mkdtemp(join(tmpdir(), 'proj-')),
  };
  for (const dir of configDirs) await mkdir(join(ctx.home, dir));
  return ctx;
}

const readJson = async (file: string) => JSON.parse(await readFile(file, 'utf8'));

function commands(config: { hooks: { SessionStart: { hooks: { command: string }[] }[] } }) {
  return config.hooks.SessionStart.flatMap((g) => g.hooks.map((h) => h.command));
}

describe('ensureSessionHook', () => {
  it('writes the script and registers it for Claude Code and Codex', async () => {
    const ctx = await makeCtx('.claude', '.codex');
    const changed = await ensureSessionHook(ctx);
    const script = join(ctx.home, '.my-skills', '.session-update.sh');
    expect(existsSync(script)).toBe(true);
    const claude = join(ctx.home, '.claude', 'settings.json');
    const codex = join(ctx.home, '.codex', 'hooks.json');
    expect(changed).toEqual([claude, codex]);
    for (const file of [claude, codex]) {
      expect(commands(await readJson(file))).toEqual([`sh '${script}'`]);
    }
  });

  it('skips an agent whose config directory does not exist', async () => {
    const ctx = await makeCtx('.claude');
    expect(await ensureSessionHook(ctx)).toEqual([join(ctx.home, '.claude', 'settings.json')]);
    expect(existsSync(join(ctx.home, '.codex'))).toBe(false);
  });

  it('keeps the other settings and hooks, and registers only once', async () => {
    const ctx = await makeCtx('.claude');
    const file = join(ctx.home, '.claude', 'settings.json');
    await writeFile(
      file,
      JSON.stringify({
        model: 'opus',
        hooks: {
          Stop: [{ hooks: [{ type: 'command', command: 'stop.sh' }] }],
          SessionStart: [{ matcher: 'startup', hooks: [{ type: 'command', command: 'mine.sh' }] }],
        },
      }),
    );
    await ensureSessionHook(ctx);
    expect(await ensureSessionHook(ctx)).toEqual([]);
    const config = await readJson(file);
    expect(config.model).toBe('opus');
    expect(config.hooks.Stop).toHaveLength(1);
    expect(config.hooks.SessionStart[0].matcher).toBe('startup');
    expect(commands(config)).toEqual([
      'mine.sh',
      `sh '${join(ctx.home, '.my-skills', '.session-update.sh')}'`,
    ]);
  });

  it('names the file when the config is malformed and leaves it as it is', async () => {
    const ctx = await makeCtx('.claude');
    const file = join(ctx.home, '.claude', 'settings.json');
    await writeFile(file, '{ nope');
    await expect(ensureSessionHook(ctx)).rejects.toThrow(file);
    expect(await readFile(file, 'utf8')).toBe('{ nope');
  });
});

describe('session-update.sh', () => {
  /** Run the installed script with a fake `npx` that records its arguments. */
  async function setup() {
    const ctx = await makeCtx('.claude');
    await ensureSessionHook(ctx);
    const bin = join(ctx.home, 'bin');
    await mkdir(bin);
    const calls = join(ctx.home, 'npx-calls');
    await writeFile(join(bin, 'npx'), `#!/bin/sh\necho "$PWD: $@" >> '${calls}'\n`);
    await chmod(join(bin, 'npx'), 0o755);
    const run = () => {
      execFileSync('sh', [join(ctx.home, '.my-skills', '.session-update.sh')], {
        cwd: ctx.project,
        env: { HOME: ctx.home, PATH: `${bin}:/usr/bin:/bin` },
        input: '{}',
      });
    };
    const callCount = async () => {
      // The update runs in the background: give it a moment to record the call.
      await new Promise((r) => setTimeout(r, 300));
      return existsSync(calls) ? (await readFile(calls, 'utf8')).trim().split('\n').length : 0;
    };
    return { ctx, calls, run, callCount };
  }

  it('updates the global skills on the first session, outside the project, with this CLI version', async () => {
    const { ctx, calls, run, callCount } = await setup();
    run();
    expect(await callCount()).toBe(1);
    const { version } = JSON.parse(
      await readFile(new URL('../package.json', import.meta.url), 'utf8'),
    );
    const [cwd, args] = (await readFile(calls, 'utf8')).trim().split(': ');
    expect(await realpath(cwd)).toBe(await realpath(join(ctx.home, '.my-skills')));
    expect(args).toBe(`-y @jesdi/skills-cli@${version} update --all --global`);
  });

  it('does nothing on later sessions of the same day, and runs again the next day', async () => {
    const { ctx, run, callCount } = await setup();
    run();
    run();
    expect(await callCount()).toBe(1);
    const twoDaysAgo = new Date(Date.now() - 2 * 86_400_000);
    await utimes(join(ctx.home, '.my-skills', '.session-update.stamp'), twoDaysAgo, twoDaysAgo);
    run();
    expect(await callCount()).toBe(2);
  });
});
