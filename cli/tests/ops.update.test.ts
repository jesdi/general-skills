import { mkdtemp, mkdir, readFile, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import * as tar from 'tar';
import { describe, expect, it } from 'vitest';
import { opApplyUpdates, opCheckUpdates, opDecline, opInstall } from '../src/ops.js';

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
