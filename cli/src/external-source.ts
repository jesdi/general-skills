import { createHash } from 'node:crypto';
import { cp, lstat, mkdir, mkdtemp, readFile, readdir, rename, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import * as tar from 'tar';
import type { CliCtx } from './ops.js';
import { externalCacheDir, writeJson, type ExternalSkill } from './external-catalogue.js';

/** Hash paths, executable permissions and bytes; refuse links out of a skill directory. */
export async function hashSkill(dir: string): Promise<string> {
  const hash = createHash('sha256');
  async function walk(relative: string): Promise<void> {
    const file = join(dir, relative);
    const stat = await lstat(file);
    if (stat.isDirectory()) {
      hash.update(JSON.stringify(['directory', relative]) + '\n');
      for (const name of (await readdir(file)).sort()) await walk(join(relative, name));
    } else if (stat.isFile()) {
      hash.update(JSON.stringify(['file', relative, stat.mode & 0o111, stat.size]) + '\n');
      hash.update(await readFile(file));
    } else {
      throw new Error(`external skill contains a link or special file: ${file}`);
    }
  }
  await walk('');
  return hash.digest('hex');
}

export interface ResolvedExternalSkill extends ExternalSkill { dir: string; hash: string }
export type ExternalGroup = [ExternalSkill, ...ExternalSkill[]];

/** One archive per source/ref. Only configured skill directories enter the cache. */
export async function fetchExternalSource(skills: ExternalGroup, ctx: CliCtx): Promise<ResolvedExternalSkill[]> {
  const { source, ref } = skills[0];
  const key = createHash('sha256').update(`${source}@${ref}`).digest('hex');
  const cache = join(externalCacheDir(ctx), key);
  const result: ResolvedExternalSkill[] = [];
  try {
    const saved: Record<string, { path: string; hash: string }> = JSON.parse(await readFile(join(cache, 'snapshot.json'), 'utf8'));
    for (const skill of skills) {
      const dir = join(cache, skill.name);
      await readFile(join(dir, 'SKILL.md'));
      const hash = await hashSkill(dir);
      if (saved[skill.name]?.path !== skill.path || saved[skill.name].hash !== hash) throw new Error('cache mismatch');
      result.push({ ...skill, dir, hash });
    }
    return result;
  } catch { result.length = 0; }

  const response = await (ctx.fetchImpl ?? fetch)(`https://codeload.github.com/${source}/tar.gz/${ref}`, {
    signal: AbortSignal.timeout(30_000),
  });
  if (!response.ok) throw new Error(`external archive ${source}@${ref} failed: ${response.status}`);
  await mkdir(externalCacheDir(ctx), { recursive: true });
  const temp = await mkdtemp(join(externalCacheDir(ctx), '.download-'));
  try {
    const archive = join(temp, 'source.tgz');
    await writeFile(archive, Buffer.from(await response.arrayBuffer()));
    const extracted = join(temp, 'extracted');
    await mkdir(extracted);
    const unsafe: string[] = [];
    await tar.extract({ file: archive, cwd: extracted, strip: 1, strict: true, filter: (path, entry) => {
      const relative = path.split('/').slice(1).join('/').replace(/\/$/, '');
      if (!skills.some((s) => relative === s.path || relative.startsWith(`${s.path}/`))) return false;
      if (relative.split('/').some((p) => p === '..') || !('type' in entry) || !['File', 'Directory'].includes(entry.type)) {
        unsafe.push(path);
        return false;
      }
      return true;
    } });
    if (unsafe.length) throw new Error(`unsafe external archive entries: ${unsafe.join(', ')}`);
    const prepared = join(temp, 'prepared');
    await mkdir(prepared);
    const saved: Record<string, { path: string; hash: string }> = {};
    for (const skill of skills) {
      const dir = join(extracted, skill.path);
      await readFile(join(dir, 'SKILL.md'));
      const hash = await hashSkill(dir);
      await cp(dir, join(prepared, skill.name), { recursive: true });
      saved[skill.name] = { path: skill.path, hash };
      result.push({ ...skill, dir: join(cache, skill.name), hash });
    }
    await writeJson(join(prepared, 'snapshot.json'), saved);
    await rm(cache, { recursive: true, force: true });
    await rename(prepared, cache);
    return result;
  } finally {
    await rm(temp, { recursive: true, force: true });
  }
}
