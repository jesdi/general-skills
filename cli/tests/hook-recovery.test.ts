import { existsSync } from 'node:fs';
import { chmod, symlink, readlink, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import * as tar from 'tar';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { buildProgram } from '../src/program.js';

interface Pack {
  /** Registry package version. */
  pkg: string;
  /** babysit-pr version. */
  skill: string;
  /** File name of the babysit-pr hook script under hooks/. */
  script: string;
  /** Marker echoed by the script, so a content change is a different file. */
  marker: string;
}

const SCRIPT = (marker: string) =>
  `#!/bin/sh\ncat >/dev/null\necho "${marker}" >> "$(dirname "$0")/ran.log"\n`;

/** Registry releases exercise installation through the CLI with an isolated home. */
async function registry(p: Pack): Promise<typeof fetch> {
  const root = await mkdtemp(join(tmpdir(), 'sh-fix-'));
  const pkg = join(root, 'package');
  const skills = [
    { name: 'babysit-pr', version: p.skill, event: 'PostToolUse', script: p.script, body: SCRIPT(p.marker) },
  ];
  for (const s of skills) {
    const dir = join(pkg, 'skills', s.name);
    await mkdir(join(dir, 'hooks'), { recursive: true });
    await writeFile(join(dir, 'SKILL.md'), `---\nname: ${s.name}\ndescription: ${s.name}\n---\nbody\n`);
    await writeFile(join(dir, 'hooks', s.script), s.body);
    await chmod(join(dir, 'hooks', s.script), 0o755);
    await writeFile(
      join(dir, 'hooks.json'),
      JSON.stringify({
        hooks: { [s.event]: [{ matcher: 'Bash', hooks: [{ type: 'command', command: `hooks/${s.script}` }] }] },
      }),
    );
  }
  await writeFile(
    join(pkg, 'skills-manifest.json'),
    JSON.stringify({
      schemaVersion: 1,
      generatedAt: new Date().toISOString(),
      skills: skills.map((s, i) => ({ name: s.name, version: s.version, hash: `sha256-${i}`, description: s.name })),
    }),
  );
  const tarball = join(root, 'pkg.tgz');
  await tar.create({ gzip: true, file: tarball, cwd: root }, ['package']);
  const body = await readFile(tarball);
  return (async (url: RequestInfo | URL) =>
    String(url).endsWith('/latest')
      ? new Response(JSON.stringify({ version: p.pkg, dist: { tarball: 'https://r.test/p.tgz' } }))
      : new Response(new Uint8Array(body))) as typeof fetch;
}

const V010: Pack = { pkg: '1.0.0', skill: '0.1.0', script: 'start.sh', marker: 'one' };

async function setup(opts: { claude?: boolean; codex?: boolean } = {}) {
  const { claude = true, codex = true } = opts;
  const home = await mkdtemp(join(tmpdir(), 'sh-home-'));
  const project = await mkdtemp(join(tmpdir(), 'sh-demo-'));
  if (claude) await mkdir(join(home, '.claude'));
  if (codex) await mkdir(join(home, '.codex'));
  return { home, project, fetchImpl: await registry(V010) };
}
type Ctx = Awaited<ReturnType<typeof setup>>;

const spies: Array<{ mockRestore(): void }> = [];
afterEach(() => spies.splice(0).forEach((s) => s.mockRestore()));

/** Run the CLI and return what it printed. */
async function cli(ctx: Ctx, ...args: string[]): Promise<string> {
  const log = vi.spyOn(console, 'log').mockImplementation(() => {});
  spies.push(log);
  await buildProgram(ctx).parseAsync(args, { from: 'user' });
  const out = log.mock.calls.map((c) => c.join(' ')).join('\n');
  log.mockRestore();
  return out;
}

async function update(ctx: Ctx, p: Pack, ...args: string[]): Promise<string> {
  ctx.fetchImpl = await registry(p);
  return cli(ctx, 'update', ...args);
}

const claudeFile = (root: string) => join(root, '.claude', 'settings.json');
const codexFile = (root: string) => join(root, '.codex', 'hooks.json');
const json = async (file: string) => JSON.parse(await readFile(file, 'utf8'));

/** Every command registered under an event, with its matcher. */
async function commands(file: string, event: string): Promise<Array<{ matcher?: string; command: string }>> {
  if (!existsSync(file)) return [];
  const cfg = await json(file);
  return (cfg.hooks?.[event] ?? []).flatMap((g: { matcher?: string; hooks?: Array<{ command: string }> }) =>
    (g.hooks ?? []).map((h) => ({ matcher: g.matcher, command: h.command })),
  );
}


describe('hook ownership recovery', () => {
  it.each(['global', 'local'])('keeps renamed ownership when clearing a declined %s update', async (scope) => {
    const ctx = await setup();
    const scopeArgs = scope === 'global' ? ['--global'] : [];
    await cli(ctx, 'install', 'babysit-pr', ...scopeArgs, '--agent', 'claude,codex');
    const stateFile = join(ctx.home, '.config/my-skills/state.json');
    await mkdir(join(ctx.home, '.config/my-skills'), { recursive: true });
    const state = existsSync(stateFile) ? await json(stateFile) : { schemaVersion: 1, skills: {}, declined: {} };
    state.declined['babysit-pr'] = '0.1.1';
    await writeFile(stateFile, JSON.stringify(state));
    await update(ctx, { ...V010, pkg: '1.0.2', skill: '0.1.2', script: 'renamed.sh' }, 'babysit-pr', ...scopeArgs);
    const installedState = scope === 'global' ? stateFile : join(ctx.project, '.my-skills.json');
    expect((await json(installedState)).skills['babysit-pr'].version).toBe('0.1.2');
    expect((await json(stateFile)).declined).toEqual({});
    await cli(ctx, 'uninstall', 'babysit-pr', ...scopeArgs);
    const root = scope === 'global' ? ctx.home : ctx.project;
    for (const f of [claudeFile(root), codexFile(root)]) expect(await commands(f, 'PostToolUse')).toEqual([]);
  });

  it('rolls back a failed state save before retry and uninstall', async () => {
    const ctx = await setup();
    const stateFile = join(ctx.home, '.config/my-skills/state.json');
    await mkdir(join(ctx.home, '.config/my-skills'), { recursive: true });
    await writeFile(stateFile, JSON.stringify({ schemaVersion: 1, skills: {}, declined: {} }));
    await chmod(stateFile, 0o444);
    try {
      await expect(cli(ctx, 'install', 'babysit-pr', '--global', '--agent', 'claude,codex')).rejects.toThrow();
      expect(existsSync(join(ctx.home, '.my-skills/babysit-pr'))).toBe(false);
      for (const f of [claudeFile(ctx.home), codexFile(ctx.home)]) expect(await commands(f, 'PostToolUse')).toEqual([]);
    } finally { await chmod(stateFile, 0o644); }
    await cli(ctx, 'install', 'babysit-pr', '--global', '--agent', 'claude,codex');
    await cli(ctx, 'uninstall', 'babysit-pr', '--global');
    for (const f of [claudeFile(ctx.home), codexFile(ctx.home)]) expect(await commands(f, 'PostToolUse')).toEqual([]);
  });

  it.each(['state', 'link'])('restores the installed release after an update %s failure', async (failure) => {
    const ctx = await setup();
    await cli(ctx, 'install', 'babysit-pr', '--global', '--agent', 'claude,codex');
    const stateFile = join(ctx.home, '.config/my-skills/state.json');
    const state = await readFile(stateFile, 'utf8');
    const configs = await Promise.all([claudeFile(ctx.home), codexFile(ctx.home)].map((f) => readFile(f, 'utf8')));
    const locked = failure === 'state' ? stateFile : join(ctx.home, '.claude/skills');
    await chmod(locked, failure === 'state' ? 0o444 : 0o555);
    try {
      await expect(update(ctx, { ...V010, pkg: '1.0.2', skill: '0.1.2', script: 'renamed.sh' }, 'babysit-pr', '--global')).rejects.toThrow();
      expect(await readFile(join(ctx.home, '.my-skills/babysit-pr/hooks.json'), 'utf8')).toContain('start.sh');
      expect(await readFile(stateFile, 'utf8')).toBe(state);
      expect(await Promise.all([claudeFile(ctx.home), codexFile(ctx.home)].map((f) => readFile(f, 'utf8')))).toEqual(configs);
    } finally { await chmod(locked, failure === 'state' ? 0o644 : 0o755); }
    await cli(ctx, 'update', 'babysit-pr', '--global');
    await cli(ctx, 'uninstall', 'babysit-pr', '--global');
    for (const f of [claudeFile(ctx.home), codexFile(ctx.home)]) expect(await commands(f, 'PostToolUse')).toEqual([]);
  });

  it('keeps the old manifest on foreign-link failure, then retries cleanly', async () => {
    const ctx = await setup();
    await cli(ctx, 'install', 'babysit-pr', '--global', '--agent', 'claude,codex');
    const link = join(ctx.home, '.claude/skills/babysit-pr');
    await rm(link);
    await symlink(ctx.project, link);
    await expect(update(ctx, { ...V010, pkg: '1.0.2', skill: '0.1.2', script: 'renamed.sh' }, 'babysit-pr', '--global')).rejects.toThrow(/outside/);
    expect(await readlink(link)).toBe(ctx.project);
    expect(await readFile(join(ctx.home, '.my-skills/babysit-pr/hooks.json'), 'utf8')).toContain('start.sh');
    await rm(link);
    await symlink(join(ctx.home, '.my-skills/babysit-pr'), link);
    await cli(ctx, 'update', 'babysit-pr', '--global');
    await cli(ctx, 'uninstall', 'babysit-pr', '--global');
    for (const f of [claudeFile(ctx.home), codexFile(ctx.home)]) expect(await commands(f, 'PostToolUse')).toEqual([]);
  });
});
