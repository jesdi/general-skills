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
