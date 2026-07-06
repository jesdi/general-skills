import { mkdtemp, mkdir, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { listSkills } from '../lib/skills.js';

async function makeSkill(root: string, name: string, frontmatter: string) {
  const dir = join(root, name);
  await mkdir(dir, { recursive: true });
  await writeFile(join(dir, 'SKILL.md'), `---\n${frontmatter}\n---\n\nBody.\n`);
  return dir;
}

describe('listSkills', () => {
  it('lists skills sorted by name with descriptions', async () => {
    const root = await mkdtemp(join(tmpdir(), 'skills-'));
    await makeSkill(root, 'zeta', 'name: zeta\ndescription: Z skill');
    await makeSkill(root, 'alpha', 'name: alpha\ndescription: A skill');
    const skills = await listSkills(root);
    expect(skills.map((s) => s.name)).toEqual(['alpha', 'zeta']);
    expect(skills[0].description).toBe('A skill');
    expect(skills[0].dir).toBe(join(root, 'alpha'));
  });

  it('rejects a skill whose frontmatter name mismatches its directory', async () => {
    const root = await mkdtemp(join(tmpdir(), 'skills-'));
    await makeSkill(root, 'alpha', 'name: beta\ndescription: broken');
    await expect(listSkills(root)).rejects.toThrow(/must match directory/);
  });

  it('rejects a skill without a description', async () => {
    const root = await mkdtemp(join(tmpdir(), 'skills-'));
    await makeSkill(root, 'alpha', 'name: alpha');
    await expect(listSkills(root)).rejects.toThrow(/description/);
  });

  it('rejects a skill directory without SKILL.md', async () => {
    const root = await mkdtemp(join(tmpdir(), 'skills-'));
    await mkdir(join(root, 'empty'));
    await expect(listSkills(root)).rejects.toThrow(/SKILL\.md/);
  });
});
