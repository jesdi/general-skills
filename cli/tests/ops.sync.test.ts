import { mkdtemp, mkdir, readFile, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import * as tar from 'tar';
import { describe, expect, it } from 'vitest';
import { opInstall, opList, opSync } from '../src/ops.js';

async function fixtureFetch(version = '1.0.0') {
  const root = await mkdtemp(join(tmpdir(), 'fix-'));
  const pkg = join(root, 'package');
  for (const name of ['hello-world', 'other']) {
    await mkdir(join(pkg, 'skills', name), { recursive: true });
    await writeFile(
      join(pkg, 'skills', name, 'SKILL.md'),
      `---\nname: ${name}\ndescription: ${name}\n---\nbody\n`,
    );
  }
  await writeFile(
    join(pkg, 'skills-manifest.json'),
    JSON.stringify({
      schemaVersion: 1,
      generatedAt: new Date().toISOString(),
      skills: [
        { name: 'hello-world', version: '0.1.0', hash: 'sha256-a', description: 'hello-world' },
        { name: 'other', version: '0.2.0', hash: 'sha256-b', description: 'other' },
      ],
    }),
  );
  const tarball = join(root, 'pkg.tgz');
  await tar.create({ gzip: true, file: tarball, cwd: root }, ['package']);
  const body = await readFile(tarball);
  return (async (url: RequestInfo | URL) => {
    const u = String(url);
    if (u.endsWith('/latest')) {
      return new Response(JSON.stringify({ version, dist: { tarball: 'https://r.test/p.tgz' } }));
    }
    return new Response(new Uint8Array(body));
  }) as typeof fetch;
}

async function makeCtx() {
  return {
    home: await mkdtemp(join(tmpdir(), 'home-')),
    project: await mkdtemp(join(tmpdir(), 'proj-')),
    fetchImpl: await fixtureFetch(),
  };
}

describe('opSync', () => {
  it('materializes a committed project manifest on a fresh machine', async () => {
    const ctx = await makeCtx();
    await writeFile(
      join(ctx.project, '.my-skills.json'),
      JSON.stringify({
        schemaVersion: 1,
        skills: { 'hello-world': { version: '0.1.0', agents: ['claude', 'opencode'] } },
      }),
    );
    const result = await opSync(ctx);
    expect(result).toEqual([{ name: 'hello-world', version: '0.1.0' }]);
    expect(existsSync(join(ctx.project, '.claude', 'skills', 'hello-world', 'SKILL.md'))).toBe(true);
    expect(existsSync(join(ctx.project, '.agents', 'skills', 'hello-world', 'SKILL.md'))).toBe(true);
  });

  it('errors when there is nothing to sync', async () => {
    const ctx = await makeCtx();
    await expect(opSync(ctx)).rejects.toThrow(/no .my-skills.json/i);
  });
});

/** Registry serving TWO package versions: 1.0.0 has hello-world@0.1.0,
 *  2.0.0 (latest) has hello-world@0.9.0. */
async function twoVersionFetch() {
  async function buildPkg(skillVersion: string) {
    const root = await mkdtemp(join(tmpdir(), 'fix-'));
    const pkg = join(root, 'package');
    await mkdir(join(pkg, 'skills', 'hello-world'), { recursive: true });
    await writeFile(
      join(pkg, 'skills', 'hello-world', 'SKILL.md'),
      `---\nname: hello-world\ndescription: v${skillVersion}\n---\nbody ${skillVersion}\n`,
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
    return readFile(tarball);
  }
  const v1 = await buildPkg('0.1.0');
  const v2 = await buildPkg('0.9.0');
  return (async (url: RequestInfo | URL) => {
    const u = String(url);
    if (u.endsWith('/latest')) {
      return new Response(
        JSON.stringify({ version: '2.0.0', dist: { tarball: 'https://r.test/pkg-2.0.0.tgz' } }),
      );
    }
    if (u.endsWith('/1.0.0')) {
      return new Response(
        JSON.stringify({ version: '1.0.0', dist: { tarball: 'https://r.test/pkg-1.0.0.tgz' } }),
      );
    }
    if (u.includes('pkg-1.0.0')) return new Response(new Uint8Array(v1));
    return new Response(new Uint8Array(v2));
  }) as typeof fetch;
}

describe('opSync pinning', () => {
  it('installs the pinned package version, not latest', async () => {
    const ctx = { ...(await makeCtx()), fetchImpl: await twoVersionFetch() };
    await writeFile(
      join(ctx.project, '.my-skills.json'),
      JSON.stringify({
        schemaVersion: 1,
        skills: { 'hello-world': { version: '0.1.0', package: '1.0.0', agents: ['claude'] } },
      }),
    );
    const result = await opSync(ctx);
    expect(result).toEqual([{ name: 'hello-world', version: '0.1.0' }]);
    const md = await readFile(
      join(ctx.project, '.claude', 'skills', 'hello-world', 'SKILL.md'),
      'utf8',
    );
    expect(md).toContain('v0.1.0');
    const state = JSON.parse(await readFile(join(ctx.project, '.my-skills.json'), 'utf8'));
    expect(state.skills['hello-world']).toEqual({
      version: '0.1.0',
      package: '1.0.0',
      agents: ['claude'],
    });
  });

  it('ratchets an unpinned entry to pinned via latest', async () => {
    const ctx = { ...(await makeCtx()), fetchImpl: await twoVersionFetch() };
    await writeFile(
      join(ctx.project, '.my-skills.json'),
      JSON.stringify({
        schemaVersion: 1,
        skills: { 'hello-world': { version: '0.1.0', agents: ['claude'] } },
      }),
    );
    const result = await opSync(ctx);
    expect(result).toEqual([{ name: 'hello-world', version: '0.9.0' }]);
    const state = JSON.parse(await readFile(join(ctx.project, '.my-skills.json'), 'utf8'));
    expect(state.skills['hello-world'].package).toBe('2.0.0');
  });

  it('fails loudly when the pin does not contain the recorded skill version', async () => {
    const ctx = { ...(await makeCtx()), fetchImpl: await twoVersionFetch() };
    await writeFile(
      join(ctx.project, '.my-skills.json'),
      JSON.stringify({
        schemaVersion: 1,
        skills: { 'hello-world': { version: '0.5.0', package: '1.0.0', agents: ['claude'] } },
      }),
    );
    await expect(opSync(ctx)).rejects.toThrow(/does not contain hello-world@0\.5\.0/);
  });
});

describe('opList', () => {
  it('merges manifest with installed state', async () => {
    const ctx = await makeCtx();
    await opInstall(['hello-world'], ['claude'], 'global', ctx);
    const list = await opList(ctx);
    expect(list).toEqual([
      {
        name: 'hello-world',
        description: 'hello-world',
        latest: '0.1.0',
        installedGlobal: '0.1.0',
        installedLocal: undefined,
      },
      {
        name: 'other',
        description: 'other',
        latest: '0.2.0',
        installedGlobal: undefined,
        installedLocal: undefined,
      },
    ]);
  });
});
