import { cp, lstat, mkdir, mkdtemp, readFile, readlink, rm, symlink } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import type { CliCtx } from './ops.js';
import { loadGlobalState } from './state.js';
import { loadCatalogue, parseExternalSkill, writeJson, type ExternalSkill } from './external-catalogue.js';
import { fetchExternalSource, hashSkill, type ExternalGroup, type ResolvedExternalSkill } from './external-source.js';
import { renameNoReplace } from './atomic-rename.js';

interface ManagedExternal extends ExternalSkill {
  hash: string;
  /** The current directory and, during a swap, its staged replacement. */
  canonicalIds: string[];
  /** Identity and bytes of an attributed legacy Claude copy awaiting link conversion. */
  claudeDirectory?: { id: string; hash: string };
}
type ManagedState = Record<string, ManagedExternal>;
type Snapshot = { id: string } & ({ kind: 'directory'; hash: string } | { kind: 'link'; target: string });

async function readOptionalJson(file: string): Promise<unknown> {
  try { return JSON.parse(await readFile(file, 'utf8')); }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return {};
    throw new Error(`could not read ${file}: ${error}`);
  }
}

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('invalid external state object');
  return value as Record<string, unknown>;
}

function contentHash(value: unknown): string {
  if (typeof value !== 'string' || !/^[0-9a-f]{64}$/.test(value)) throw new Error('invalid external content hash');
  return value;
}

function managedState(value: unknown): ManagedState {
  const result: ManagedState = {};
  for (const [name, entry] of Object.entries(object(value))) {
    const item = object(entry);
    const skill = parseExternalSkill(item);
    if (skill.name !== name || !Array.isArray(item.canonicalIds) || item.canonicalIds.length < 1 ||
        item.canonicalIds.length > 2 || !item.canonicalIds.every((id) => typeof id === 'string' && /^\d+:\d+:\d+$/.test(id))) {
      throw new Error(`invalid managed external ownership: ${name}`);
    }
    result[name] = { ...skill, hash: contentHash(item.hash), canonicalIds: item.canonicalIds };
    if (item.claudeDirectory !== undefined) {
      const entry = object(item.claudeDirectory);
      if (typeof entry.id !== 'string' || !/^\d+:\d+:\d+$/.test(entry.id)) throw new Error('invalid Claude directory identity');
      result[name].claudeDirectory = { id: entry.id, hash: contentHash(entry.hash) };
    }
  }
  return result;
}

async function entryStat(file: string) {
  try { return await lstat(file, { bigint: true }); }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return undefined;
    throw error;
  }
}

function identity(stat: NonNullable<Awaited<ReturnType<typeof entryStat>>>): string {
  return `${stat.dev}:${stat.ino}:${stat.birthtimeNs}`;
}

/** Absence is different from an unreadable, unsupported or changing entry. */
async function snapshot(file: string): Promise<Snapshot | undefined> {
  const stat = await entryStat(file);
  if (!stat) return undefined;
  const id = identity(stat);
  let result: Snapshot;
  if (stat.isDirectory()) result = { id, kind: 'directory', hash: await hashSkill(file) };
  else if (stat.isSymbolicLink()) result = { id, kind: 'link', target: await readlink(file) };
  else throw new Error(`unsupported external entry: ${file}`);
  const after = await entryStat(file);
  if (!after || identity(after) !== id) throw new Error(`${file} changed while being checked`);
  return result;
}

/** Current external attribution wins over our previous record. Read at mutation boundaries. */
async function attributedToSource(skill: ExternalSkill, lockFile: string): Promise<boolean> {
  const lock = object(await readOptionalJson(lockFile));
  const entry = object(lock.skills ?? {})[skill.name];
  if (entry === undefined) return false;
  if (object(entry).source !== skill.source) {
    throw new Error(`${skill.name} has conflicting current skills.sh provenance; refusing to replace it`);
  }
  return true;
}

function checkClaude(entry: Snapshot | undefined, file: string, canonical: string, ownedHashes: readonly string[]): void {
  if (!entry) return;
  if (entry.kind === 'link' && resolve(dirname(file), entry.target) === resolve(canonical)) return;
  if (entry.kind === 'directory' && ownedHashes.includes(entry.hash)) return;
  throw new Error(`${file} is an unrelated install; refusing to replace it`);
}

/** Both directory and link replacement capture and validate the entry they remove. */
async function replaceEntry(opts: {
  destination: string;
  previous: Snapshot | undefined;
  prepare: (file: string) => Promise<void>;
  verifySource: () => Promise<void>;
}): Promise<void> {
  await mkdir(dirname(opts.destination), { recursive: true });
  const temp = await mkdtemp(join(dirname(opts.destination), '.external-'));
  const prepared = join(temp, 'entry');
  const backup = join(temp, 'old');
  let moved = false;
  let installed = false;
  try {
    await opts.prepare(prepared);
    await opts.verifySource();
    try { await renameNoReplace(opts.destination, backup); moved = true; }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
    try {
      const captured = moved ? await snapshot(backup) : undefined;
      if (JSON.stringify(captured) !== JSON.stringify(opts.previous)) {
        throw new Error(`${opts.destination} changed during installation; refusing to replace it`);
      }
      await opts.verifySource();
      await renameNoReplace(prepared, opts.destination);
      installed = true;
    } catch (error) {
      if (moved) {
        try { await renameNoReplace(backup, opts.destination); }
        catch (restoreError) { throw new Error(`${error}; ${restoreError}; original entry preserved at ${backup}`); }
        moved = false;
      }
      throw error;
    }
  } finally {
    // If rollback failed, preserve its captured copy for recovery.
    try {
      if (installed || !moved) await rm(temp, { recursive: true, force: true });
      else await rm(prepared, { recursive: true, force: true });
    } catch (error) { console.warn(`external swap cleanup failed at ${temp}: ${error}`); }
  }
}

async function reconcile(
  skill: ResolvedExternalSkill,
  target: { canonical: string; claude: string | undefined; managed: ManagedState; stateFile: string; lockFile: string },
): Promise<boolean> {
  const { canonical, claude, managed, stateFile, lockFile } = target;
  const previousRecord = managed[skill.name];
  const before = await snapshot(canonical);
  const attributed = await attributedToSource(skill, lockFile);
  const owned = attributed || (before !== undefined && previousRecord?.canonicalIds.includes(before.id) === true);
  if (before && (before.kind !== 'directory' || !owned)) {
    throw new Error(`${canonical} is an unrelated install; refusing to replace it`);
  }
  const claudeBefore = claude ? await snapshot(claude) : undefined;
  const recoveringClaude = claudeBefore?.kind === 'directory' && previousRecord?.claudeDirectory?.id === claudeBefore.id &&
    previousRecord.claudeDirectory.hash === claudeBefore.hash;
  const hashes = attributed || recoveringClaude ? [skill.hash, before?.kind === 'directory' ? before.hash : undefined,
    recoveringClaude ? claudeBefore.hash : undefined].filter((h): h is string => h !== undefined) : [];
  if (claude) checkClaude(claudeBefore, claude, canonical, hashes);
  const { dir, ...desired } = skill;
  const legacyHash = claudeBefore?.kind === 'directory' ? claudeBefore.hash : undefined;
  const legacyDirectory = claudeBefore?.kind === 'directory' ? { id: claudeBefore.id, hash: claudeBefore.hash } : undefined;
  const verifySource = async () => { await attributedToSource(skill, lockFile); };
  let canonicalCommitted = false;
  let changed = false;
  try {
    if (!before || before.kind !== 'directory' || before.hash !== skill.hash) {
      await replaceEntry({ destination: canonical, previous: before, verifySource, prepare: async (file) => {
        await cp(dir, file, { recursive: true });
        const staged = await snapshot(file);
        if (!staged || staged.kind !== 'directory' || staged.hash !== skill.hash) throw new Error('staged external skill mismatch');
        // A journal authorizes only the old attributed directory and this exact
        // staged inode. A refused install cannot authorize a foreign replacement.
        managed[skill.name] = { ...desired, canonicalIds: before ? [before.id, staged.id] : [staged.id],
          ...(legacyDirectory ? { claudeDirectory: legacyDirectory } : {}) };
        await writeJson(stateFile, managed);
      } });
      canonicalCommitted = true;
      changed = true;
    }
    const current = await snapshot(canonical);
    if (!current || current.kind !== 'directory' || current.hash !== skill.hash) throw new Error(`${canonical} changed after installation`);
    const verifiedIds = canonicalCommitted ? managed[skill.name].canonicalIds : before ? [before.id] : [];
    if (!verifiedIds.includes(current.id)) throw new Error(`${canonical} identity changed after installation`);
    const pending: ManagedExternal = { ...desired, canonicalIds: [current.id],
      ...(legacyDirectory ? { claudeDirectory: legacyDirectory } : {}) };
    if (JSON.stringify(managed[skill.name]) !== JSON.stringify(pending)) {
      await verifySource();
      managed[skill.name] = pending;
      await writeJson(stateFile, managed);
      changed = true;
    }
    if (claude && !(claudeBefore?.kind === 'link' && resolve(dirname(claude), claudeBefore.target) === resolve(canonical))) {
      await replaceEntry({ destination: claude, previous: claudeBefore, verifySource,
        prepare: async (file) => { await symlink(canonical, file, 'dir'); } });
      changed = true;
    }
    if (legacyHash) {
      managed[skill.name] = { ...desired, canonicalIds: [current.id] };
      await writeJson(stateFile, managed);
    }
    return changed;
  } catch (error) {
    if (!canonicalCommitted) {
      if (previousRecord) managed[skill.name] = previousRecord;
      else delete managed[skill.name];
      await writeJson(stateFile, managed);
    }
    throw error;
  }
}

/** Reconcile every catalogue entry. External files stay outside the repository. */
export async function opSyncExternal(ctx: CliCtx): Promise<string[]> {
  const skills = await loadCatalogue(ctx);
  const stateFile = join(ctx.home, '.config', 'my-skills', 'external.json');
  const managed = managedState(await readOptionalJson(stateFile));
  const lockFile = join(ctx.home, '.agents', '.skill-lock.json');
  const global = await loadGlobalState(ctx);
  const claudeInstalled = await entryStat(join(ctx.home, '.claude')).then((stat) => stat !== undefined);
  const settings = object(claudeInstalled ? await readOptionalJson(join(ctx.home, '.claude', 'settings.json')) : {});
  const frontendPlugin = Object.entries(object(settings.enabledPlugins ?? {})).some(([name, enabled]) =>
    name.startsWith('frontend-design@') && enabled === true);
  const groups = new Map<string, ExternalGroup>();
  for (const skill of skills) {
    const key = `${skill.source}@${skill.ref}`;
    const group = groups.get(key);
    if (group) group.push(skill);
    else groups.set(key, [skill]);
  }
  const repaired: string[] = [];
  const errors: string[] = [];
  for (const group of groups.values()) {
    let fetched: ResolvedExternalSkill[];
    try { fetched = await fetchExternalSource(group, ctx); }
    catch (error) { errors.push(`${group[0].source}: ${error}`); continue; }
    for (const skill of fetched) {
      try {
        if (global.skills[skill.name]) throw new Error(`${skill.name} is an own skill; refusing an external replacement`);
        const canonical = join(ctx.home, '.agents', 'skills', skill.name);
        const claude = claudeInstalled && !(skill.name === 'frontend-design' && frontendPlugin)
          ? join(ctx.home, '.claude', 'skills', skill.name) : undefined;
        if (await reconcile(skill, { canonical, claude, managed, stateFile, lockFile })) {
          repaired.push(skill.name);
          console.log(`reconciled external ${skill.name}: ${skill.source}@${skill.ref}${skill.version ? ` (${skill.version})` : ''}`);
        }
      } catch (error) { errors.push(`${skill.name}: ${error}`); }
    }
  }
  if (errors.length) throw new Error(`external skill check failed:\n${errors.join('\n')}`);
  console.log(`verified ${skills.length} external skills`);
  return repaired;
}
