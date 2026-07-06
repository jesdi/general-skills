import { existsSync } from 'node:fs';
import { mkdtemp, mkdir, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import * as tar from 'tar';
import { describe, expect, it } from 'vitest';
import { buildProgram } from '../src/program.js';

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

describe('cli wiring', () => {
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
    await program.parseAsync(['install', 'hello-world', '--global'], { from: 'user' });
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
});
