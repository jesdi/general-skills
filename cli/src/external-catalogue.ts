import { mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import type { CliCtx } from './ops.js';

const CATALOGUE_URL = 'https://raw.githubusercontent.com/jesdi/general-skills/main/external-skills.json';
export const DAY = 86_400_000;

export interface ExternalSkill {
  name: string;
  source: string;
  path: string;
  ref: string;
  /** Upstream version label, when the source publishes one. The ref selects the files. */
  version?: string;
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('external-skills.json: expected an object');
  }
  return value as Record<string, unknown>;
}

function text(value: unknown, pattern: RegExp): string {
  if (typeof value !== 'string' || !pattern.test(value)) {
    throw new Error(`external-skills.json: invalid field ${String(value)}`);
  }
  return value;
}

/** Desired entries and installed provenance use the same identity contract. */
export function parseExternalSkill(value: unknown): ExternalSkill {
  const skill = record(value);
  const name = text(skill.name, /^[a-z0-9]+(?:-[a-z0-9]+)*$/);
  const source = text(skill.source, /^[A-Za-z0-9_-]+\/[A-Za-z0-9_.-]+$/);
  const path = text(skill.path, /^[A-Za-z0-9_.-]+(?:\/[A-Za-z0-9_.-]+)*$/);
  if (path.split('/').some((p) => p === '.' || p === '..')) throw new Error(`unsafe external path: ${path}`);
  const ref = text(skill.ref, /^[0-9a-f]{40}$/);
  const version = skill.version === undefined ? undefined : text(skill.version, /^\S+$/);
  return { name, source, path, ref, ...(version === undefined ? {} : { version }) };
}

/** Validate the complete catalogue before installing any of it. */
export function parseCatalogue(value: unknown): ExternalSkill[] {
  const catalogue = record(value);
  if (catalogue.schemaVersion !== 2) throw new Error('external-skills.json: unsupported schemaVersion');
  const pins = record(catalogue.pins);
  const forks = record(catalogue.forks);
  if (!Array.isArray(forks.skills) || forks.doNotInstallUpstream !== true || !Array.isArray(catalogue.skills)) {
    throw new Error('external-skills.json: missing skills or fork exclusions');
  }
  const excluded = forks.skills;
  const names = new Set<string>();
  return catalogue.skills.map((value) => {
    const skill = record(value);
    const pin = record(pins[String(skill.source)]);
    const resolved = parseExternalSkill({ ...skill, ref: pin.ref, version: pin.version });
    const { name } = resolved;
    if (names.has(name) || excluded.includes(name)) throw new Error(`duplicate or fork external skill: ${name}`);
    names.add(name);
    return resolved;
  });
}

export function externalCacheDir(ctx: CliCtx): string {
  // Keep this outside the npm package cache, whose children are package versions.
  return join(ctx.home, '.cache', 'my-skills-external');
}

export async function writeJson(file: string, value: unknown): Promise<void> {
  await mkdir(dirname(file), { recursive: true });
  const temp = `${file}.${process.pid}.tmp`;
  await writeFile(temp, JSON.stringify(value, null, 2) + '\n');
  await rename(temp, file);
}

/** Refresh once a day; an offline session can still repair from the last valid catalogue. */
export async function loadCatalogue(ctx: CliCtx): Promise<ExternalSkill[]> {
  const file = join(externalCacheDir(ctx), 'catalogue.json');
  let cached: { refreshedAt: number; catalogue: unknown } | undefined;
  try {
    cached = JSON.parse(await readFile(file, 'utf8'));
    parseCatalogue(cached?.catalogue);
  } catch { cached = undefined; }
  if (cached && Date.now() - cached.refreshedAt < DAY) return parseCatalogue(cached.catalogue);
  let value: unknown;
  let skills: ExternalSkill[];
  try {
    const response = await (ctx.fetchImpl ?? fetch)(CATALOGUE_URL, { signal: AbortSignal.timeout(30_000) });
    if (!response.ok) throw new Error(`catalogue download failed: ${response.status}`);
    value = await response.json();
    skills = parseCatalogue(value);
  } catch (error) {
    if (!cached) throw error;
    console.warn(`external catalogue refresh failed; using cache: ${error}`);
    return parseCatalogue(cached.catalogue);
  }
  await writeJson(file, { refreshedAt: Date.now(), catalogue: value });
  return skills;
}
