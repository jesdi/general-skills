import { readdir, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import matter from 'gray-matter';

export interface SkillInfo {
  name: string;
  description: string;
  dir: string;
}

export async function listSkills(skillsDir: string): Promise<SkillInfo[]> {
  const entries = await readdir(skillsDir, { withFileTypes: true });
  const dirs = entries.filter((e) => e.isDirectory()).sort((a, b) => a.name.localeCompare(b.name));
  const skills: SkillInfo[] = [];
  for (const entry of dirs) {
    const dir = join(skillsDir, entry.name);
    let raw: string;
    try {
      raw = await readFile(join(dir, 'SKILL.md'), 'utf8');
    } catch {
      throw new Error(`skills/${entry.name}: missing SKILL.md`);
    }
    const { data } = matter(raw);
    if (data.name !== entry.name) {
      throw new Error(
        `skills/${entry.name}: frontmatter name "${data.name}" must match directory name`,
      );
    }
    if (!data.description || typeof data.description !== 'string') {
      throw new Error(`skills/${entry.name}: frontmatter must include a description`);
    }
    skills.push({ name: entry.name, description: data.description, dir });
  }
  return skills;
}
