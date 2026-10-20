import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import * as tar from 'tar';
import { describe, expect, it } from 'vitest';
import {
  opApplyUpdates,
  opCheckUpdates,
  opDecline,
  opInstall,
  opSetPinned,
  opSync,
} from '../src/ops.js';

async function fixtureFetch(pkgVersion: string, skillVersion: string) {
  const root = await mkdtemp(join(tmpdir(), 'fix-'));
  const pkg = join(root, 'package');
  await mkdir(join(pkg, 'skills', 'hello-world'), { recursive: true });
  await writeFile(
    join(pkg, 'skills', 'hello-world', 'SKILL.md'),
    `---\nname: hello-world\ndescription: demo\n---\ncontent ${skillVersion}\n`,
  );
  await writeFile(
    join(pkg, 'skills-manifest.json'),
    JSON.stringify({
      schemaVersion: 1,
      generatedAt: new Date().toISOString(),
      skills: [
        { name: 'hello-world', version: skillVersion, hash: `sha256-${skillVersion}`, description: 'demo' },
      ],
    }),
  );
  const tarball = join(root, 'pkg.tgz');
  await tar.create({ gzip: true, file: tarball, cwd: root }, ['package']);
  const body = await readFile(tarball);
  return (async (url: RequestInfo | URL) => {
    const u = String(url);
    if (u.endsWith('/latest')) {
      return new Response(
        JSON.stringify({ version: pkgVersion, dist: { tarball: 'https://r.test/p.tgz' } }),
      );
    }
    return new Response(new Uint8Array(body));
  }) as typeof fetch;
}

async function makeCtx() {
  return {
    home: await mkdtemp(join(tmpdir(), 'home-')),
    project: await mkdtemp(join(tmpdir(), 'proj-')),
    fetchImpl: await fixtureFetch('1.0.0', '0.1.0'),
  };
}

describe('updates', () => {
  it('reports an update when the manifest moves ahead', async () => {
    const ctx = await makeCtx();
    await opInstall(['hello-world'], ['claude'], 'global', ctx);
    expect(await opCheckUpdates('global', ctx)).toEqual([]);
    ctx.fetchImpl = await fixtureFetch('1.0.1', '0.1.1');
    expect(await opCheckUpdates('global', ctx)).toEqual([
      { name: 'hello-world', from: '0.1.0', to: '0.1.1' },
    ]);
  });

  it('applies updates: new content, new state version', async () => {
    const ctx = await makeCtx();
    await opInstall(['hello-world'], ['claude'], 'global', ctx);
    ctx.fetchImpl = await fixtureFetch('1.0.1', '0.1.1');
    await opApplyUpdates(['hello-world'], 'global', ctx);
    const content = await readFile(
      join(ctx.home, '.claude', 'skills', 'hello-world', 'SKILL.md'),
      'utf8',
    );
    expect(content).toContain('content 0.1.1');
    expect(await opCheckUpdates('global', ctx)).toEqual([]);
  });

  it('updates an entry that inherits the top-level agents and keeps it inherited', async () => {
    const ctx = await makeCtx();
    await writeFile(
      join(ctx.project, '.my-skills.json'),
      JSON.stringify({ schemaVersion: 1, agents: ['claude', 'opencode'], skills: {} }),
    );
    await opInstall(['hello-world'], undefined, 'local', ctx);
    ctx.fetchImpl = await fixtureFetch('1.0.1', '0.1.1');
    await opApplyUpdates(['hello-world'], 'local', ctx);
    for (const dir of [join('.claude', 'skills'), join('.agents', 'skills')]) {
      const content = await readFile(join(ctx.project, dir, 'hello-world', 'SKILL.md'), 'utf8');
      expect(content).toContain('content 0.1.1');
    }
    const state = JSON.parse(await readFile(join(ctx.project, '.my-skills.json'), 'utf8'));
    expect(state.agents).toEqual(['claude', 'opencode']);
    expect(state.skills['hello-world']).toEqual({ version: '0.1.1', package: '1.0.1' });
  });

  it('keeps a per-skill override through an update', async () => {
    const ctx = await makeCtx();
    await writeFile(
      join(ctx.project, '.my-skills.json'),
      JSON.stringify({ schemaVersion: 1, agents: ['claude'], skills: {} }),
    );
    await opInstall(['hello-world'], ['opencode'], 'local', ctx);
    ctx.fetchImpl = await fixtureFetch('1.0.1', '0.1.1');
    await opApplyUpdates(['hello-world'], 'local', ctx);
    const state = JSON.parse(await readFile(join(ctx.project, '.my-skills.json'), 'utf8'));
    expect(state.skills['hello-world']).toEqual({
      version: '0.1.1',
      package: '1.0.1',
      agents: ['opencode'],
    });
    expect(existsSync(join(ctx.project, '.claude', 'skills', 'hello-world'))).toBe(false);
  });

  it('fails with the friendly message when an entry has no agents anywhere', async () => {
    const ctx = await makeCtx();
    await writeFile(
      join(ctx.project, '.my-skills.json'),
      JSON.stringify({ schemaVersion: 1, skills: { 'hello-world': { version: '0.1.0' } } }),
    );
    ctx.fetchImpl = await fixtureFetch('1.0.1', '0.1.1');
    await expect(opApplyUpdates(['hello-world'], 'local', ctx)).rejects.toThrow(
      /no agents configured for hello-world/,
    );
  });

  it('suppresses a declined version but re-prompts on the next one', async () => {
    const ctx = await makeCtx();
    await opInstall(['hello-world'], ['claude'], 'global', ctx);
    ctx.fetchImpl = await fixtureFetch('1.0.1', '0.1.1');
    await opDecline('hello-world', '0.1.1', ctx);
    expect(await opCheckUpdates('global', ctx)).toEqual([]);
    ctx.fetchImpl = await fixtureFetch('1.0.2', '0.1.2');
    expect(await opCheckUpdates('global', ctx)).toEqual([
      { name: 'hello-world', from: '0.1.0', to: '0.1.2' },
    ]);
  });
});

describe('pinned skills', () => {
  it('a pinned skill is not an update candidate; unpinned, it is again', async () => {
    const ctx = await makeCtx();
    await opInstall(['hello-world'], ['claude'], 'global', ctx);
    await opSetPinned('hello-world', true, 'global', ctx);
    ctx.fetchImpl = await fixtureFetch('1.0.1', '0.1.1');
    expect(await opCheckUpdates('global', ctx)).toEqual([]);
    await opSetPinned('hello-world', false, 'global', ctx);
    expect(await opCheckUpdates('global', ctx)).toEqual([
      { name: 'hello-world', from: '0.1.0', to: '0.1.1' },
    ]);
  });

  it('records the pin on the entry and removes the key on unpin', async () => {
    const ctx = await makeCtx();
    await opInstall(['hello-world'], ['claude'], 'local', ctx);
    const read = async () =>
      JSON.parse(await readFile(join(ctx.project, '.my-skills.json'), 'utf8')).skills['hello-world'];
    await opSetPinned('hello-world', true, 'local', ctx);
    expect(await read()).toEqual({ version: '0.1.0', package: '1.0.0', agents: ['claude'], pinned: true });
    await opSetPinned('hello-world', false, 'local', ctx);
    expect(await read()).toEqual({ version: '0.1.0', package: '1.0.0', agents: ['claude'] });
  });

  it('refuses to update or reinstall a pinned skill and leaves its content', async () => {
    const ctx = await makeCtx();
    await opInstall(['hello-world'], ['claude'], 'global', ctx);
    await opSetPinned('hello-world', true, 'global', ctx);
    ctx.fetchImpl = await fixtureFetch('1.0.1', '0.1.1');
    await expect(opApplyUpdates(['hello-world'], 'global', ctx)).rejects.toThrow(
      /hello-world is pinned at 0\.1\.0/,
    );
    await expect(opInstall(['hello-world'], ['claude'], 'global', ctx)).rejects.toThrow(
      /hello-world is pinned at 0\.1\.0/,
    );
    const content = await readFile(
      join(ctx.home, '.claude', 'skills', 'hello-world', 'SKILL.md'),
      'utf8',
    );
    expect(content).toContain('content 0.1.0');
  });

  it('errors when the skill to pin is not installed', async () => {
    const ctx = await makeCtx();
    await expect(opSetPinned('hello-world', true, 'global', ctx)).rejects.toThrow(
      /skill not installed \(global\): hello-world/,
    );
  });

  it('sync keeps the pin of a committed entry', async () => {
    const ctx = await makeCtx();
    await writeFile(
      join(ctx.project, '.my-skills.json'),
      JSON.stringify({
        schemaVersion: 1,
        skills: { 'hello-world': { version: '0.1.0', agents: ['claude'], pinned: true } },
      }),
    );
    await opSync(ctx);
    const state = JSON.parse(await readFile(join(ctx.project, '.my-skills.json'), 'utf8'));
    expect(state.skills['hello-world'].pinned).toBe(true);
  });
});

describe('global install', () => {
  const settingsOf = (ctx: { home: string }) => join(ctx.home, '.claude', 'settings.json');
  const autoUpdateOf = async (ctx: { home: string }) =>
    JSON.parse(await readFile(join(ctx.home, '.config', 'my-skills', 'state.json'), 'utf8')).autoUpdate;

  it('asks once and registers the session update hook on yes', async () => {
    let asked = 0;
    const ctx = { ...(await makeCtx()), promptAutoUpdate: async () => { asked++; return true; } };
    await opInstall(['hello-world'], ['claude'], 'global', ctx);
    await opInstall(['hello-world'], ['claude'], 'global', ctx);
    expect(asked).toBe(1);
    expect(await autoUpdateOf(ctx)).toBe(true);
    expect(await readFile(settingsOf(ctx), 'utf8')).toContain('.session-update.sh');
  });

  it('asks once and registers no hook on no', async () => {
    let asked = 0;
    const ctx = { ...(await makeCtx()), promptAutoUpdate: async () => { asked++; return false; } };
    await opInstall(['hello-world'], ['claude'], 'global', ctx);
    await opInstall(['hello-world'], ['claude'], 'global', ctx);
    expect(asked).toBe(1);
    expect(await autoUpdateOf(ctx)).toBe(false);
    expect(existsSync(settingsOf(ctx))).toBe(false);
    expect(existsSync(join(ctx.home, '.my-skills', '.session-update.sh'))).toBe(false);
  });

  it('registers no hook and records nothing when it cannot ask', async () => {
    const ctx = await makeCtx();
    await opInstall(['hello-world'], ['claude'], 'global', ctx);
    expect(await autoUpdateOf(ctx)).toBeUndefined();
    expect(existsSync(settingsOf(ctx))).toBe(false);
  });

  it('a project install registers no hook', async () => {
    const ctx = await makeCtx();
    await opInstall(['hello-world'], ['claude'], 'local', ctx);
    expect(existsSync(join(ctx.home, '.my-skills'))).toBe(false);
  });
});

describe('state writers', () => {
  it('a decline and a pin wait for the store lock and keep the hook ownership written under it', async () => {
    const ctx = await makeCtx();
    await opInstall(['hello-world'], ['claude'], 'global', ctx);
    const lock = join(ctx.home, '.my-skills.lock');
    const file = join(ctx.home, '.config', 'my-skills', 'state.json');
    await mkdir(lock); // an install of a renamed hook holds the lock
    const pending = Promise.all([
      opDecline('hello-world', '0.1.1', ctx),
      opSetPinned('hello-world', true, 'global', ctx),
    ]);
    await new Promise((r) => setTimeout(r, 120));
    const state = JSON.parse(await readFile(file, 'utf8'));
    state.skills['hello-world'].ownedHooks = ['claude\0PostToolUse\0Bash\0renamed.sh'];
    await writeFile(file, JSON.stringify(state));
    await rm(lock, { recursive: true });
    await pending;
    const after = JSON.parse(await readFile(file, 'utf8'));
    expect(after.skills['hello-world'].ownedHooks).toEqual(['claude\0PostToolUse\0Bash\0renamed.sh']);
    expect(after.skills['hello-world'].pinned).toBe(true);
    expect(after.declined['hello-world']).toBe('0.1.1');
  });
});
