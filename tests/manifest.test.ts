import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { generateManifest, hashSkillDir, type Manifest } from '../lib/manifest.js';

async function makeSkill(root: string, name: string, body: string) {
  const dir = join(root, name);
  await mkdir(dir, { recursive: true });
  await writeFile(
    join(dir, 'SKILL.md'),
    `---\nname: ${name}\ndescription: ${name} skill\n---\n\n${body}\n`,
  );
  return dir;
}

describe('hashSkillDir', () => {
  it('is stable for identical content and changes when content changes', async () => {
    const root = await mkdtemp(join(tmpdir(), 'mani-'));
    const dir = await makeSkill(root, 'alpha', 'v1');
    const h1 = await hashSkillDir(dir);
    expect(h1).toMatch(/^sha256-[0-9a-f]{64}$/);
    expect(await hashSkillDir(dir)).toBe(h1);
    await writeFile(join(dir, 'SKILL.md'), 'changed');
    expect(await hashSkillDir(dir)).not.toBe(h1);
  });
});

describe('generateManifest', () => {
  it('assigns 0.1.0 to new skills', async () => {
    const root = await mkdtemp(join(tmpdir(), 'mani-'));
    await makeSkill(root, 'alpha', 'v1');
    const m = await generateManifest(root, null);
    expect(m.schemaVersion).toBe(1);
    expect(m.skills).toHaveLength(1);
    expect(m.skills[0]).toMatchObject({ name: 'alpha', version: '0.1.0' });
  });

  it('keeps versions for unchanged skills and patch-bumps changed ones', async () => {
    const root = await mkdtemp(join(tmpdir(), 'mani-'));
    await makeSkill(root, 'alpha', 'v1');
    await makeSkill(root, 'beta', 'v1');
    const first = await generateManifest(root, null);
    await writeFile(
      join(root, 'beta', 'SKILL.md'),
      '---\nname: beta\ndescription: beta skill\n---\n\nv2\n',
    );
    const second = await generateManifest(root, first);
    const byName = Object.fromEntries(second.skills.map((s) => [s.name, s.version]));
    expect(byName.alpha).toBe('0.1.0');
    expect(byName.beta).toBe('0.1.1');
  });

  it('drops removed skills', async () => {
    const root = await mkdtemp(join(tmpdir(), 'mani-'));
    await makeSkill(root, 'alpha', 'v1');
    await makeSkill(root, 'beta', 'v1');
    const first: Manifest = await generateManifest(root, null);
    await rm(join(root, 'beta'), { recursive: true });
    const second = await generateManifest(root, first);
    expect(second.skills.map((s) => s.name)).toEqual(['alpha']);
  });
});
