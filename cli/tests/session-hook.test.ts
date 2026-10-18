import { spawn } from 'node:child_process';
import { chmod, mkdtemp, mkdir, readFile, realpath, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { ensureSessionHook, removeSessionHook } from '../src/hook.js';

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

describe('removeSessionHook', () => {
  it('removes only its own hook from every agent config and deletes the script', async () => {
    const ctx = await makeCtx('.claude', '.codex');
    const claude = join(ctx.home, '.claude', 'settings.json');
    const codex = join(ctx.home, '.codex', 'hooks.json');
    await writeFile(
      claude,
      JSON.stringify({
        model: 'opus',
        hooks: { SessionStart: [{ matcher: 'startup', hooks: [{ type: 'command', command: 'mine.sh' }] }] },
      }),
    );
    await ensureSessionHook(ctx);
    expect(await removeSessionHook(ctx)).toEqual([claude, codex]);
    const config = await readJson(claude);
    expect(config.model).toBe('opus');
    expect(config.hooks.SessionStart).toEqual([
      { matcher: 'startup', hooks: [{ type: 'command', command: 'mine.sh' }] },
    ]);
    expect((await readJson(codex)).hooks.SessionStart).toEqual([]);
    expect(existsSync(join(ctx.home, '.my-skills', '.session-update.sh'))).toBe(false);
    expect(await removeSessionHook(ctx)).toEqual([]);
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
    // Async, with a timeout: `execFileSync` with a large `input` can leave the child's stdin
    // open on macOS, and then the script and the blocked worker wait for each other forever.
    const run = (input = '{}') =>
      new Promise<void>((resolve, reject) => {
        const child = spawn('sh', [join(ctx.home, '.my-skills', '.session-update.sh')], {
          cwd: ctx.project,
          env: { HOME: ctx.home, PATH: `${bin}:/usr/bin:/bin` },
        });
        const timer = setTimeout(() => {
          child.kill('SIGKILL');
          reject(new Error('session-update.sh did not exit within 3 s'));
        }, 3000);
        const fail = (err: Error) => {
          clearTimeout(timer);
          reject(err);
        };
        child.on('error', fail);
        // EPIPE lands here when the script exits before it reads its input.
        child.stdin.on('error', fail);
        child.on('close', (code) => {
          clearTimeout(timer);
          if (code === 0) resolve();
          else reject(new Error(`session-update.sh exited with ${code}`));
        });
        child.stdin.end(input);
      });
    const callCount = async () =>
      existsSync(calls) ? (await readFile(calls, 'utf8')).trim().split('\n').length : 0;
    // The update runs in the background: wait until it records the call, however slow the machine.
    const expectCalls = (n: number) => expect.poll(callCount, { timeout: 3000 }).toBe(n);
    return { ctx, calls, run, expectCalls };
  }

  it('updates the global skills on the first session, outside the project, with the latest CLI', async () => {
    const { ctx, calls, run, expectCalls } = await setup();
    await run();
    await expectCalls(1);
    const [cwd, args] = (await readFile(calls, 'utf8')).trim().split(': ');
    expect(await realpath(cwd)).toBe(await realpath(join(ctx.home, '.my-skills')));
    expect(args).toBe('-y @jesdi/skills-cli@latest session-update');
  });

  it('reads its hook input to the end, so the runner never writes to a closed pipe', async () => {
    const { run, expectCalls } = await setup();
    // More than a pipe buffer: a script that exits before it reads makes this write fail with EPIPE.
    await run(JSON.stringify({ transcript: 'x'.repeat(1 << 20) }));
    await expectCalls(1);
  });

  it('checks on every session, even when the daily update stamp is recent', async () => {
    const { ctx, run, expectCalls } = await setup();
    await run();
    await expectCalls(1);
    await run();
    await expectCalls(2);
    await run();
    await expectCalls(3);
  });
});
