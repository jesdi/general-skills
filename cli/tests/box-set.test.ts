import { cp, mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { existsSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import * as tar from 'tar';
import { describe, expect, it } from 'vitest';
import { generateManifest } from '../../lib/manifest.js';
import { opSync } from '../src/ops.js';

const root = new URL('../..', import.meta.url).pathname;
// Every own skill, so any of them can be in the box set, which agent-ops-infra lists (provision/my-skills.json).
const own: string[] = readdirSync(join(root, 'skills')).filter((name) =>
  existsSync(join(root, 'skills', name, 'SKILL.md')),
);

/** Package this repo's real skills/ as @jesdi/skills@9.9.9 and serve it from memory. */
async function packageRealSkills() {
  const stage = await mkdtemp(join(tmpdir(), 'box-pkg-'));
  const pkg = join(stage, 'package');
  await cp(join(root, 'skills'), join(pkg, 'skills'), { recursive: true });
  const manifest = await generateManifest(join(pkg, 'skills'), null);
  await writeFile(join(pkg, 'skills-manifest.json'), JSON.stringify(manifest));
  const tarball = join(stage, 'pkg.tgz');
  await tar.create({ gzip: true, file: tarball, cwd: stage }, ['package']);
  const body = await readFile(tarball);
  const fetchImpl = (async (url: RequestInfo | URL) => {
    const u = String(url);
    if (u.endsWith('/latest') || u.endsWith('/9.9.9')) {
      return new Response(JSON.stringify({ version: '9.9.9', dist: { tarball: 'https://r.test/p.tgz' } }));
    }
    return new Response(new Uint8Array(body));
  }) as typeof fetch;
  return { manifest, fetchImpl };
}

describe('own skills at the packaging seam', () => {
  it('every own skill appears in the generated manifest with a version', async () => {
    const manifest = await generateManifest(join(root, 'skills'), null);
    for (const name of own) {
      const skill = manifest.skills.find((s) => s.name === name);
      expect(skill?.version, name).toMatch(/^\d+\.\d+\.\d+$/);
    }
  });

  it('sync materializes every own skill at the agent paths', async () => {
    const { manifest, fetchImpl } = await packageRealSkills();
    const ctx = {
      home: await mkdtemp(join(tmpdir(), 'box-home-')),
      project: await mkdtemp(join(tmpdir(), 'box-proj-')),
      fetchImpl,
    };
    const skills = Object.fromEntries(
      own.map((name) => [
        name,
        { version: manifest.skills.find((s) => s.name === name)!.version, package: '9.9.9' },
      ]),
    );
    await writeFile(
      join(ctx.project, '.my-skills.json'),
      JSON.stringify({ schemaVersion: 1, agents: ['claude'], skills }),
    );
    const result = await opSync(ctx);
    expect(result.map((r) => r.name).sort()).toEqual([...own].sort());
    for (const name of own) {
      expect(existsSync(join(ctx.project, '.claude', 'skills', name, 'SKILL.md')), name).toBe(true);
    }
  }, 30_000);
});
