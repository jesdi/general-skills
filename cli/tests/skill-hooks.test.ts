import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { chmod, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
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

/** A fake registry with `babysit-pr` and `pr-risk`, both shipping a `hooks.json`. */
async function registry(p: Pack): Promise<typeof fetch> {
  const root = await mkdtemp(join(tmpdir(), 'sh-fix-'));
  const pkg = join(root, 'package');
  const skills = [
    { name: 'babysit-pr', version: p.skill, event: 'PostToolUse', script: p.script, body: SCRIPT(p.marker) },
    { name: 'pr-risk', version: '0.3.0', event: 'PreToolUse', script: 'gate.sh', body: SCRIPT('gate') },
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

const TRUST = /trust/i;
/** The steps to trust hooks in Codex: open /hooks, review the entries, trust them. */
const TRUST_STEPS = /\/hooks[\s\S]*review[\s\S]*trust/i;

describe('skill hooks: global install', () => {
  it('registers one PostToolUse Bash entry in both agents and prints the Codex trust steps', async () => {
    const ctx = await setup();
    const out = await cli(ctx, 'install', 'babysit-pr', '--global', '--agent', 'claude,codex');
    for (const file of [claudeFile(ctx.home), codexFile(ctx.home)]) {
      const found = await commands(file, 'PostToolUse');
      expect(found).toHaveLength(1);
      expect(found[0].matcher).toBe('Bash');
      expect(found[0].command).toContain('.my-skills/babysit-pr/hooks/start.sh');
      execFileSync('sh', ['-c', found[0].command], { input: '{}' });
    }
    const ran = join(ctx.home, '.my-skills', 'babysit-pr', 'hooks', 'ran.log');
    expect(await readFile(ran, 'utf8')).toBe('one\none\n');
    expect(out).toMatch(/codex/i);
    expect(out).toMatch(TRUST_STEPS);
  });

  it('leaves exactly one entry per command after a second identical install', async () => {
    const ctx = await setup();
    const args = ['install', 'babysit-pr', '--global', '--agent', 'claude,codex'];
    await cli(ctx, ...args);
    const first = [await readFile(claudeFile(ctx.home), 'utf8'), await readFile(codexFile(ctx.home), 'utf8')];
    await cli(ctx, ...args);
    for (const file of [claudeFile(ctx.home), codexFile(ctx.home)]) {
      expect(await commands(file, 'PostToolUse')).toHaveLength(1);
    }
    expect([await readFile(claudeFile(ctx.home), 'utf8'), await readFile(codexFile(ctx.home), 'utf8')]).toEqual(first);
  });

  it('writes only Claude Code config for a Claude-only install and prints no Codex trust steps', async () => {
    const ctx = await setup();
    const out = await cli(ctx, 'install', 'babysit-pr', '--global', '--agent', 'claude');
    expect(await commands(claudeFile(ctx.home), 'PostToolUse')).toHaveLength(1);
    expect(existsSync(codexFile(ctx.home))).toBe(false);
    expect(out).not.toMatch(TRUST);
  });

  it('writes nothing for the .agents/skills target and says to start /babysit-pr by hand', async () => {
    const ctx = await setup();
    const out = await cli(ctx, 'install', 'babysit-pr', '--global', '--agent', 'opencode');
    expect(existsSync(claudeFile(ctx.home))).toBe(false);
    expect(existsSync(codexFile(ctx.home))).toBe(false);
    expect(out).toContain('/babysit-pr <url>');
  });
});

describe('skill hooks: project install', () => {
  async function projectInstall() {
    const ctx = await setup();
    await mkdir(join(ctx.project, '.claude'));
    await mkdir(join(ctx.project, '.codex'));
    execFileSync('git', ['init', '-q'], { cwd: ctx.project });
    await mkdir(join(ctx.project, 'src', 'lib'), { recursive: true });
    await writeFile(claudeFile(ctx.home), '{"model":"kept"}\n');
    await writeFile(codexFile(ctx.home), '{"hooks":{}}\n');
    const before = [await readFile(claudeFile(ctx.home), 'utf8'), await readFile(codexFile(ctx.home), 'utf8')];
    await cli(ctx, 'install', 'babysit-pr', '--agent', 'claude,codex');
    return { ctx, before };
  }

  const runHook = (command: string, cwd: string) =>
    execFileSync('sh', ['-c', command], { cwd, input: '{}', encoding: 'utf8' });

  it('writes the project configs and leaves the global configs unchanged', async () => {
    const { ctx, before } = await projectInstall();
    for (const file of [claudeFile(ctx.project), codexFile(ctx.project)]) {
      const found = await commands(file, 'PostToolUse');
      expect(found).toHaveLength(1);
      expect(found[0].matcher).toBe('Bash');
    }
    expect([await readFile(claudeFile(ctx.home), 'utf8'), await readFile(codexFile(ctx.home), 'utf8')]).toEqual(before);
  });

  it('exits 0 with no output on a clone without the store', async () => {
    const { ctx } = await projectInstall();
    await rm(join(ctx.project, '.my-skills'), { recursive: true, force: true });
    for (const file of [claudeFile(ctx.project), codexFile(ctx.project)]) {
      const [{ command }] = await commands(file, 'PostToolUse');
      expect(command).toBeTruthy();
      expect(runHook(command, join(ctx.project, 'src'))).toBe('');
    }
  });

  it('finds and runs the store script from a subfolder', async () => {
    const { ctx } = await projectInstall();
    const [{ command }] = await commands(claudeFile(ctx.project), 'PostToolUse');
    runHook(command, join(ctx.project, 'src', 'lib'));
    expect(await readFile(join(ctx.project, '.my-skills', 'babysit-pr', 'hooks', 'ran.log'), 'utf8')).toBe('one\n');
  });
});

describe('skill hooks: uninstall', () => {
  it('removes only the babysit-pr entries; foreign and pr-risk entries and the rest stay byte-equal', async () => {
    const ctx = await setup();
    const own = { hooks: { PreToolUse: [{ matcher: 'Bash', hooks: [{ type: 'command', command: 'rtk hook claude' }] }] } };
    await writeFile(claudeFile(ctx.home), JSON.stringify(own, null, 2) + '\n');
    await cli(ctx, 'install', 'pr-risk', '--global', '--agent', 'claude,codex');
    const withRisk = [await readFile(claudeFile(ctx.home), 'utf8'), await readFile(codexFile(ctx.home), 'utf8')];

    await cli(ctx, 'install', 'babysit-pr', '--global', '--agent', 'claude,codex');
    for (const file of [claudeFile(ctx.home), codexFile(ctx.home)]) {
      expect((await commands(file, 'PostToolUse')).map((c) => c.command).join()).toContain('babysit-pr');
      expect((await commands(file, 'PreToolUse')).map((c) => c.command).join()).toContain('pr-risk');
    }
    expect((await commands(claudeFile(ctx.home), 'PreToolUse')).map((c) => c.command)).toContain('rtk hook claude');

    await cli(ctx, 'uninstall', 'babysit-pr', '--global');
    expect([await readFile(claudeFile(ctx.home), 'utf8'), await readFile(codexFile(ctx.home), 'utf8')]).toEqual(withRisk);
  });

  it('keeps foreign commands on the same event and matcher, in the same group or another', async () => {
    const ctx = await setup();
    await cli(ctx, 'install', 'babysit-pr', '--global', '--agent', 'claude,codex');
    for (const file of [claudeFile(ctx.home), codexFile(ctx.home)]) {
      const cfg = await json(file);
      cfg.hooks.PostToolUse[0].hooks.push({ type: 'command', command: 'same-group.sh' });
      cfg.hooks.PostToolUse.push({ matcher: 'Bash', hooks: [{ type: 'command', command: 'other-group.sh' }] });
      await writeFile(file, JSON.stringify(cfg, null, 2) + '\n');
    }

    await cli(ctx, 'uninstall', 'babysit-pr', '--global');
    for (const file of [claudeFile(ctx.home), codexFile(ctx.home)]) {
      const left = (await commands(file, 'PostToolUse')).map((c) => c.command);
      expect(left).toEqual(['same-group.sh', 'other-group.sh']);
    }
  });

  it('prints the Codex trust note when it moves later hook groups', async () => {
    const ctx = await setup();
    await cli(ctx, 'install', 'babysit-pr', '--global', '--agent', 'claude,codex');
    const cfg = await json(codexFile(ctx.home));
    cfg.hooks.PostToolUse.push({ matcher: 'Edit', hooks: [{ type: 'command', command: 'later.sh' }] });
    await writeFile(codexFile(ctx.home), JSON.stringify(cfg, null, 2) + '\n');
    expect(cfg.hooks.PostToolUse).toHaveLength(2);

    const out = await cli(ctx, 'uninstall', 'babysit-pr', '--global');
    expect(await commands(codexFile(ctx.home), 'PostToolUse')).toEqual([{ matcher: 'Edit', command: 'later.sh' }]);
    expect((await json(codexFile(ctx.home))).hooks.PostToolUse).toMatchObject([{ matcher: 'Edit' }]);
    expect(out).toMatch(/codex/i);
    expect(out).toMatch(TRUST_STEPS);
  });
});

describe('skill hooks: update', () => {
  const install = async () => {
    const ctx = await setup();
    await cli(ctx, 'install', 'babysit-pr', '--global', '--agent', 'claude,codex');
    return ctx;
  };

  it('keeps the command string and prints no trust steps when only the script content changes', async () => {
    const ctx = await install();
    const before = await commands(codexFile(ctx.home), 'PostToolUse');
    expect(before).toHaveLength(1);
    const raw = await readFile(codexFile(ctx.home), 'utf8');

    const out = await update(ctx, { pkg: '1.0.1', skill: '0.1.1', script: 'start.sh', marker: 'two' }, 'babysit-pr', '--global');
    expect(out).toContain('updated babysit-pr');
    expect(await commands(codexFile(ctx.home), 'PostToolUse')).toEqual(before);
    expect(await readFile(codexFile(ctx.home), 'utf8')).toBe(raw);
    expect(out).not.toMatch(TRUST);
  });

  it('replaces the old command with the new one in each config and prints the trust steps on a rename', async () => {
    const ctx = await install();
    const out = await update(ctx, { pkg: '1.0.1', skill: '0.1.1', script: 'on-pr-create.sh', marker: 'one' }, 'babysit-pr', '--global');
    for (const file of [claudeFile(ctx.home), codexFile(ctx.home)]) {
      const found = await commands(file, 'PostToolUse');
      expect(found).toHaveLength(1);
      expect(found[0].command).toContain('hooks/on-pr-create.sh');
      expect(found[0].command).not.toContain('start.sh');
    }
    expect(out).toMatch(/codex/i);
    expect(out).toMatch(TRUST_STEPS);
  });
});

describe('skill hooks: Codex trust', () => {
  it('never changes ~/.codex/config.toml on install, update and uninstall', async () => {
    const ctx = await setup();
    const toml = '[hooks.state]\n"hooks.json:post_tool_use:0:0" = { trusted_hash = "sha256:abc" }\n';
    const file = join(ctx.home, '.codex', 'config.toml');
    await writeFile(file, toml);

    await cli(ctx, 'install', 'babysit-pr', '--global', '--agent', 'claude,codex');
    expect((await commands(codexFile(ctx.home), 'PostToolUse')).length).toBe(1);
    expect(await readFile(file, 'utf8')).toBe(toml);

    await update(ctx, { pkg: '1.0.1', skill: '0.1.1', script: 'on-pr-create.sh', marker: 'one' }, 'babysit-pr', '--global');
    expect(await commands(codexFile(ctx.home), 'PostToolUse')).toHaveLength(1);
    expect(await readFile(file, 'utf8')).toBe(toml);

    await cli(ctx, 'uninstall', 'babysit-pr', '--global');
    expect(await commands(codexFile(ctx.home), 'PostToolUse')).toHaveLength(0);
    expect(await readFile(file, 'utf8')).toBe(toml);
  });
});
