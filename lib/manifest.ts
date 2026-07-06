import { createHash } from 'node:crypto';
import { readdir, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { listSkills } from './skills.js';

export interface ManifestSkill {
  name: string;
  version: string;
  hash: string;
  description: string;
}

export interface Manifest {
  schemaVersion: 1;
  generatedAt: string;
  skills: ManifestSkill[];
}

async function walk(dir: string, prefix = ''): Promise<string[]> {
  const entries = await readdir(dir, { withFileTypes: true });
  const files: string[] = [];
  for (const e of entries) {
    const rel = prefix ? `${prefix}/${e.name}` : e.name;
    if (e.isDirectory()) files.push(...(await walk(join(dir, e.name), rel)));
    else files.push(rel);
  }
  return files.sort();
}

export async function hashSkillDir(dir: string): Promise<string> {
  const hash = createHash('sha256');
  for (const rel of await walk(dir)) {
    hash.update(rel);
    hash.update('\0');
    hash.update(await readFile(join(dir, rel)));
    hash.update('\0');
  }
  return `sha256-${hash.digest('hex')}`;
}

function bumpPatch(version: string): string {
  const [major, minor, patch] = version.split('.').map(Number);
  return `${major}.${minor}.${patch + 1}`;
}

export async function generateManifest(
  skillsDir: string,
  previous: Manifest | null,
): Promise<Manifest> {
  const prev = new Map((previous?.skills ?? []).map((s) => [s.name, s]));
  const skills: ManifestSkill[] = [];
  for (const skill of await listSkills(skillsDir)) {
    const hash = await hashSkillDir(skill.dir);
    const before = prev.get(skill.name);
    const version =
      before === undefined ? '0.1.0' : before.hash === hash ? before.version : bumpPatch(before.version);
    skills.push({ name: skill.name, version, hash, description: skill.description });
  }
  return { schemaVersion: 1, generatedAt: new Date().toISOString(), skills };
}
