import { mkdtemp, readFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { opInstall, opUninstall } from '../src/ops.js';

// Reuse the fixture builder from the registry test.
import { mkdir, writeFile } from 'node:fs/promises';
import * as tar from 'tar';

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

describe('opInstall', () => {
  it('installs globally: store copy, agent links, state entry', async () => {
    const ctx = await makeCtx();
    const installed = await opInstall(['hello-world'], ['claude', 'opencode'], 'global', ctx);
    expect(installed).toEqual([{ name: 'hello-world', version: '0.1.0' }]);
    expect(existsSync(join(ctx.home, '.my-skills', 'hello-world', 'SKILL.md'))).toBe(true);
    expect(existsSync(join(ctx.home, '.claude', 'skills', 'hello-world', 'SKILL.md'))).toBe(true);
    expect(existsSync(join(ctx.home, '.agents', 'skills', 'hello-world', 'SKILL.md'))).toBe(true);
    const state = JSON.parse(
      await readFile(join(ctx.home, '.config', 'my-skills', 'state.json'), 'utf8'),
    );
    expect(state.skills['hello-world']).toEqual({
      version: '0.1.0',
      package: '1.0.0',
      agents: ['claude', 'opencode'],
    });
  });

  it('installs locally into the project and writes .my-skills.json', async () => {
    const ctx = await makeCtx();
    await opInstall(['other'], ['opencode'], 'local', ctx);
    expect(existsSync(join(ctx.project, '.agents', 'skills', 'other', 'SKILL.md'))).toBe(true);
    const state = JSON.parse(await readFile(join(ctx.project, '.my-skills.json'), 'utf8'));
    expect(state.skills.other).toEqual({ version: '0.2.0', package: '1.0.0', agents: ['opencode'] });
  });

  it('rejects unknown skill names', async () => {
    const ctx = await makeCtx();
    await expect(opInstall(['nope'], ['claude'], 'global', ctx)).rejects.toThrow(/unknown skill/i);
  });

  it('records the @jesdi/skills package version as a pin', async () => {
    const ctx = await makeCtx();
    await opInstall(['hello-world'], ['claude'], 'local', ctx);
    const state = JSON.parse(await readFile(join(ctx.project, '.my-skills.json'), 'utf8'));
    expect(state.skills['hello-world'].package).toBe('1.0.0');
  });
});

describe('opInstall agent resolution', () => {
  it('inherits the top-level agents when none are given: entry has no agents key', async () => {
    const ctx = await makeCtx();
    await writeFile(
      join(ctx.project, '.my-skills.json'),
      JSON.stringify({ schemaVersion: 1, agents: ['claude', 'opencode'], skills: {} }),
    );
    await opInstall(['hello-world'], undefined, 'local', ctx);
    expect(existsSync(join(ctx.project, '.claude', 'skills', 'hello-world', 'SKILL.md'))).toBe(true);
    expect(existsSync(join(ctx.project, '.agents', 'skills', 'hello-world', 'SKILL.md'))).toBe(true);
    const state = JSON.parse(await readFile(join(ctx.project, '.my-skills.json'), 'utf8'));
    expect(state.agents).toEqual(['claude', 'opencode']);
    expect(state.skills['hello-world']).toEqual({ version: '0.1.0', package: '1.0.0' });
    expect('agents' in state.skills['hello-world']).toBe(false);
  });

  it('writes a per-skill override when agents are given and leaves the top-level untouched', async () => {
    const ctx = await makeCtx();
    await writeFile(
      join(ctx.project, '.my-skills.json'),
      JSON.stringify({ schemaVersion: 1, agents: ['claude'], skills: {} }),
    );
    await opInstall(['hello-world'], ['opencode'], 'local', ctx);
    expect(existsSync(join(ctx.project, '.agents', 'skills', 'hello-world', 'SKILL.md'))).toBe(true);
    expect(existsSync(join(ctx.project, '.claude', 'skills', 'hello-world'))).toBe(false);
    const state = JSON.parse(await readFile(join(ctx.project, '.my-skills.json'), 'utf8'));
    expect(state.agents).toEqual(['claude']);
    expect(state.skills['hello-world'].agents).toEqual(['opencode']);
  });

  it('does not create a top-level default when agents are given explicitly', async () => {
    const ctx = await makeCtx();
    await opInstall(['hello-world'], ['claude'], 'local', ctx);
    const state = JSON.parse(await readFile(join(ctx.project, '.my-skills.json'), 'utf8'));
    expect('agents' in state).toBe(false);
    expect(state.skills['hello-world'].agents).toEqual(['claude']);
  });

  it('throws when nothing is configured and no prompt is available (non-TTY)', async () => {
    const ctx = await makeCtx();
    await expect(opInstall(['hello-world'], undefined, 'local', ctx)).rejects.toThrow(
      /no agents configured.*--agent/,
    );
    expect(existsSync(join(ctx.project, '.my-skills.json'))).toBe(false);
    expect(existsSync(join(ctx.project, '.my-skills'))).toBe(false);
  });

  it('asks once via the injected prompt, persists the answer as the top-level default and uses it', async () => {
    let calls = 0;
    const ctx = {
      ...(await makeCtx()),
      promptAgents: async () => {
        calls++;
        return ['claude', 'opencode'] as const;
      },
    };
    await opInstall(['hello-world', 'other'], undefined, 'local', ctx);
    expect(calls).toBe(1);
    expect(existsSync(join(ctx.project, '.claude', 'skills', 'hello-world', 'SKILL.md'))).toBe(true);
    expect(existsSync(join(ctx.project, '.agents', 'skills', 'other', 'SKILL.md'))).toBe(true);
    const state = JSON.parse(await readFile(join(ctx.project, '.my-skills.json'), 'utf8'));
    expect(state.agents).toEqual(['claude', 'opencode']);
    expect(state.skills['hello-world']).toEqual({ version: '0.1.0', package: '1.0.0' });
    expect(state.skills.other).toEqual({ version: '0.2.0', package: '1.0.0' });

    // A second install does not ask again.
    await opInstall(['hello-world'], undefined, 'local', ctx);
    expect(calls).toBe(1);
  });

  it('does not prompt when the top-level default already exists', async () => {
    const ctx = {
      ...(await makeCtx()),
      promptAgents: async (): Promise<['claude']> => {
        throw new Error('should not prompt');
      },
    };
    await mkdir(join(ctx.home, '.config', 'my-skills'), { recursive: true });
    await writeFile(
      join(ctx.home, '.config', 'my-skills', 'state.json'),
      JSON.stringify({ schemaVersion: 1, agents: ['opencode'], skills: {}, declined: {} }),
    );
    await opInstall(['hello-world'], undefined, 'global', ctx);
    expect(existsSync(join(ctx.home, '.agents', 'skills', 'hello-world', 'SKILL.md'))).toBe(true);
  });

  it('rejects an unknown agent id coming from the prompt', async () => {
    const ctx = {
      ...(await makeCtx()),
      promptAgents: async () => ['cursor'] as never,
    };
    await expect(opInstall(['hello-world'], undefined, 'local', ctx)).rejects.toThrow(
      /unknown agent: cursor/,
    );
  });
});

describe('opUninstall', () => {
  it('removes links, store copy and state entry', async () => {
    const ctx = await makeCtx();
    await opInstall(['hello-world'], ['claude'], 'global', ctx);
    await opUninstall('hello-world', 'global', ctx);
    expect(existsSync(join(ctx.home, '.claude', 'skills', 'hello-world'))).toBe(false);
    expect(existsSync(join(ctx.home, '.my-skills', 'hello-world'))).toBe(false);
    const state = JSON.parse(
      await readFile(join(ctx.home, '.config', 'my-skills', 'state.json'), 'utf8'),
    );
    expect(state.skills['hello-world']).toBeUndefined();
  });

  it('errors when the skill is not installed', async () => {
    const ctx = await makeCtx();
    await expect(opUninstall('hello-world', 'global', ctx)).rejects.toThrow(/not installed/i);
  });

  it('removes links from the inherited top-level agents', async () => {
    const ctx = await makeCtx();
    await writeFile(
      join(ctx.project, '.my-skills.json'),
      JSON.stringify({ schemaVersion: 1, agents: ['claude', 'opencode'], skills: {} }),
    );
    await opInstall(['hello-world'], undefined, 'local', ctx);
    await opUninstall('hello-world', 'local', ctx);
    expect(existsSync(join(ctx.project, '.claude', 'skills', 'hello-world'))).toBe(false);
    expect(existsSync(join(ctx.project, '.agents', 'skills', 'hello-world'))).toBe(false);
    expect(existsSync(join(ctx.project, '.my-skills', 'hello-world'))).toBe(false);
    const state = JSON.parse(await readFile(join(ctx.project, '.my-skills.json'), 'utf8'));
    expect(state.agents).toEqual(['claude', 'opencode']);
    expect(state.skills['hello-world']).toBeUndefined();
  });
});
