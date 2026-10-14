import { existsSync } from 'node:fs';
import { mkdtemp, mkdir, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import { buildProgram } from '../src/program.js';
import { fixtureFetch } from './fixture-fetch.js';

async function makeCtx() {
  return {
    home: await mkdtemp(join(tmpdir(), 'home-')),
    project: await mkdtemp(join(tmpdir(), 'proj-')),
    fetchImpl: await fixtureFetch(),
  };
}

describe('cli wiring', () => {
  it.each([['update', 'hello-world', '--global'], ['update', '--all', '--global']])(
    'applies selected update arguments %j', async (...args) => {
      const ctx = await makeCtx();
      await buildProgram(ctx).parseAsync(['install', 'hello-world', '--agent', 'opencode', '--global'], { from: 'user' });
      ctx.fetchImpl = await fixtureFetch('1.0.1', '0.1.1');
      await buildProgram(ctx).parseAsync(args, { from: 'user' });
      expect(await readFile(join(ctx.home, '.agents', 'skills', 'hello-world', 'SKILL.md'), 'utf8')).toContain('body 0.1.1');
    },
  );

  it('lists update candidates until an explicit selection is supplied', async () => {
    const ctx = await makeCtx();
    await buildProgram(ctx).parseAsync(['install', 'hello-world', '--agent', 'opencode', '--global'], { from: 'user' });
    ctx.fetchImpl = await fixtureFetch('1.0.1', '0.1.1');
    const output = vi.spyOn(console, 'log').mockImplementation(() => {});
    try {
      await buildProgram(ctx).parseAsync(['update', '--global'], { from: 'user' });
      expect(output).toHaveBeenCalledWith('hello-world: 0.1.0 -> 0.1.1');
      expect(await readFile(join(ctx.home, '.agents', 'skills', 'hello-world', 'SKILL.md'), 'utf8')).toContain('body 0.1.0');
      await buildProgram(ctx).parseAsync(['update', '--all', '--global'], { from: 'user' });
      await buildProgram(ctx).parseAsync(['update', '--global'], { from: 'user' });
      expect(output).toHaveBeenCalledWith('everything is up to date');
    } finally { output.mockRestore(); }
  });

  it('lists local and global versions alongside uninstalled skills', async () => {
    const ctx = await makeCtx();
    for (const scope of [[], ['--global']]) {
      await buildProgram(ctx).parseAsync(['install', 'hello-world', '--agent', 'opencode', ...scope], { from: 'user' });
    }
    const output = vi.spyOn(console, 'log').mockImplementation(() => {});
    try {
      await buildProgram(ctx).parseAsync(['list'], { from: 'user' });
      expect(output).toHaveBeenCalledWith('hello-world@0.1.0 [global@0.1.0, local@0.1.0] — hello-world');
      expect(output).toHaveBeenCalledWith('other@0.2.0 — other');
    } finally { output.mockRestore(); }
  });

  it('registers hooks once and reports an already registered hook', async () => {
    const ctx = await makeCtx();
    await mkdir(join(ctx.home, '.claude'));
    const output = vi.spyOn(console, 'log').mockImplementation(() => {});
    try {
      await buildProgram(ctx).parseAsync(['hook'], { from: 'user' });
      expect(await readFile(join(ctx.home, '.claude', 'settings.json'), 'utf8')).toContain('session-update.sh');
      await buildProgram(ctx).parseAsync(['hook'], { from: 'user' });
      expect(output).toHaveBeenCalledWith('already registered');
    } finally { output.mockRestore(); }
  });

  it('hook turns auto-update on, hook --off turns it off and removes the hook', async () => {
    const ctx = await makeCtx();
    await mkdir(join(ctx.home, '.claude'));
    const settings = join(ctx.home, '.claude', 'settings.json');
    const autoUpdate = async () =>
      JSON.parse(await readFile(join(ctx.home, '.config', 'my-skills', 'state.json'), 'utf8')).autoUpdate;
    const output = vi.spyOn(console, 'log').mockImplementation(() => {});
    try {
      await buildProgram(ctx).parseAsync(['hook'], { from: 'user' });
      expect(await autoUpdate()).toBe(true);
      await buildProgram(ctx).parseAsync(['hook', '--off'], { from: 'user' });
      expect(await autoUpdate()).toBe(false);
      expect(output).toHaveBeenCalledWith(`removed from ${settings}`);
      expect(await readFile(settings, 'utf8')).not.toContain('session-update.sh');
      expect(existsSync(join(ctx.home, '.my-skills', '.session-update.sh'))).toBe(false);
      await buildProgram(ctx).parseAsync(['hook', '--off'], { from: 'user' });
      expect(output).toHaveBeenCalledWith('not registered');
    } finally { output.mockRestore(); }
  });
  it('install subcommand installs with flags', async () => {
    const ctx = await makeCtx();
    const program = buildProgram(ctx);
    await program.parseAsync(
      ['install', 'hello-world', '--agent', 'claude,opencode', '--global'],
      { from: 'user' },
    );
    expect(existsSync(join(ctx.home, '.claude', 'skills', 'hello-world', 'SKILL.md'))).toBe(true);
    expect(existsSync(join(ctx.home, '.agents', 'skills', 'hello-world', 'SKILL.md'))).toBe(true);
  });

  it('uninstall subcommand removes a global install', async () => {
    const ctx = await makeCtx();
    const program = buildProgram(ctx);
    await program.parseAsync(['install', 'hello-world', '--agent', 'claude', '--global'], {
      from: 'user',
    });
    await buildProgram(ctx).parseAsync(['uninstall', 'hello-world', '--global'], { from: 'user' });
    expect(existsSync(join(ctx.home, '.claude', 'skills', 'hello-world'))).toBe(false);
  });

  it('rejects an unknown agent id', async () => {
    const ctx = await makeCtx();
    const program = buildProgram(ctx);
    program.exitOverride();
    await expect(
      program.parseAsync(['install', 'hello-world', '--agent', 'cursor'], { from: 'user' }),
    ).rejects.toThrow(/unknown agent/i);
  });

  it('install without --agent fails in a non-TTY when no default is configured', async () => {
    const ctx = await makeCtx();
    const program = buildProgram(ctx);
    program.exitOverride();
    await expect(
      program.parseAsync(['install', 'hello-world'], { from: 'user' }),
    ).rejects.toThrow(/no agents configured.*--agent/);
    expect(existsSync(join(ctx.project, '.my-skills.json'))).toBe(false);
  });

  it('install without --agent inherits the committed top-level default', async () => {
    const ctx = await makeCtx();
    await writeFile(
      join(ctx.project, '.my-skills.json'),
      JSON.stringify({ schemaVersion: 1, agents: ['opencode'], skills: {} }),
    );
    await buildProgram(ctx).parseAsync(['install', 'hello-world'], { from: 'user' });
    expect(existsSync(join(ctx.project, '.agents', 'skills', 'hello-world', 'SKILL.md'))).toBe(true);
    expect(existsSync(join(ctx.project, '.claude', 'skills', 'hello-world'))).toBe(false);
    const state = JSON.parse(await readFile(join(ctx.project, '.my-skills.json'), 'utf8'));
    expect(state.skills['hello-world']).toEqual({ version: '0.1.0', package: '1.0.0' });
  });

  it('install --agent writes a per-skill override', async () => {
    const ctx = await makeCtx();
    await writeFile(
      join(ctx.project, '.my-skills.json'),
      JSON.stringify({ schemaVersion: 1, agents: ['opencode'], skills: {} }),
    );
    await buildProgram(ctx).parseAsync(['install', 'hello-world', '--agent', 'claude'], {
      from: 'user',
    });
    const state = JSON.parse(await readFile(join(ctx.project, '.my-skills.json'), 'utf8'));
    expect(state.agents).toEqual(['opencode']);
    expect(state.skills['hello-world'].agents).toEqual(['claude']);
  });
});
