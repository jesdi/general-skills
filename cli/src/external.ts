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

function canonicalIds(value: unknown, name: string): string[] {
  if (!Array.isArray(value) || value.length < 1 || value.length > 2 ||
      !value.every((id) => typeof id === 'string' && /^\d+:\d+:\d+$/.test(id))) {
    throw new Error(`invalid managed external ownership: ${name}`);
  }
  return value;
}

function legacyClaudeDirectory(value: unknown): NonNullable<ManagedExternal['claudeDirectory']> {
  const entry = object(value);
  if (typeof entry.id !== 'string' || !/^\d+:\d+:\d+$/.test(entry.id)) throw new Error('invalid Claude directory identity');
  return { id: entry.id, hash: contentHash(entry.hash) };
}

function managedState(value: unknown): ManagedState {
  const result: ManagedState = {};
  for (const [name, entry] of Object.entries(object(value))) {
    const item = object(entry);
    const skill = parseExternalSkill(item);
    if (skill.name !== name) throw new Error(`invalid managed external ownership: ${name}`);
    const ids = canonicalIds(item.canonicalIds, name);
    result[name] = { ...skill, hash: contentHash(item.hash), canonicalIds: ids };
    if (item.claudeDirectory !== undefined) result[name].claudeDirectory = legacyClaudeDirectory(item.claudeDirectory);
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

async function cleanupSwap(temp: string, prepared: string, installed: boolean, moved: boolean): Promise<void> {
  // If rollback failed, preserve its captured copy for recovery.
  try {
    if (installed || !moved) await rm(temp, { recursive: true, force: true });
    else await rm(prepared, { recursive: true, force: true });
  } catch (error) { console.warn(`external swap cleanup failed at ${temp}: ${error}`); }
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
    await cleanupSwap(temp, prepared, installed, moved);
  }
}

interface ReconcileTarget {
  canonical: string;
  claude: string | undefined;
  managed: ManagedState;
  stateFile: string;
  lockFile: string;
}
type LegacyDirectory = ManagedExternal['claudeDirectory'];
type DesiredRecord = Omit<ResolvedExternalSkill, 'dir'>;
interface PreparedInstallation {
  readonly skill: ResolvedExternalSkill;
  readonly desired: DesiredRecord;
  readonly target: ReconcileTarget;
  readonly before: Snapshot | undefined;
  readonly claudeBefore: Snapshot | undefined;
  readonly legacyDirectory: LegacyDirectory;
  readonly previousRecord: ManagedExternal | undefined;
  readonly verifySource: () => Promise<void>;
}

function authorizeCanonical(before: Snapshot | undefined, previous: ManagedExternal | undefined, attributed: boolean, canonical: string): void {
  const owned = attributed || (before !== undefined && previous?.canonicalIds.includes(before.id) === true);
  if (before && (before.kind !== 'directory' || !owned)) {
    throw new Error(`${canonical} is an unrelated install; refusing to replace it`);
  }
}

/** Claude directory adoption needs current attribution or an exact recovery record. */
function trustedClaudeHashes(skill: ResolvedExternalSkill, before: Snapshot | undefined, claudeBefore: Snapshot | undefined,
  previous: ManagedExternal | undefined, attributed: boolean): string[] {
  const recovering = claudeBefore?.kind === 'directory' && previous?.claudeDirectory?.id === claudeBefore.id &&
    previous.claudeDirectory.hash === claudeBefore.hash;
  if (!attributed && !recovering) return [];
  const hashes = [skill.hash];
  if (before?.kind === 'directory') hashes.push(before.hash);
  if (recovering) hashes.push(claudeBefore.hash);
  return hashes;
}

/** Bind the checked entries and derived ownership record to one resolved installation. */
async function inspectInstallation(skill: ResolvedExternalSkill, target: ReconcileTarget): Promise<PreparedInstallation> {
  const previousRecord = target.managed[skill.name];
  const before = await snapshot(target.canonical);
  const attributed = await attributedToSource(skill, target.lockFile);
  authorizeCanonical(before, previousRecord, attributed, target.canonical);
  const claudeBefore = target.claude ? await snapshot(target.claude) : undefined;
  if (target.claude) checkClaude(claudeBefore, target.claude, target.canonical,
    trustedClaudeHashes(skill, before, claudeBefore, previousRecord, attributed));
  const legacyDirectory = claudeBefore?.kind === 'directory' ? { id: claudeBefore.id, hash: claudeBefore.hash } : undefined;
  const { dir: _dir, ...desired } = skill;
  const verifySource = async () => { await attributedToSource(skill, target.lockFile); };
  return { skill, desired, target, before, claudeBefore, legacyDirectory, previousRecord, verifySource };
}

function ownershipRecord(desired: DesiredRecord, ids: string[], legacyDirectory: LegacyDirectory): ManagedExternal {
  return { ...desired, canonicalIds: ids, ...(legacyDirectory ? { claudeDirectory: legacyDirectory } : {}) };
}

/** Persist the exact staged identity before publication so interrupted swaps are recoverable. */
async function stageCanonical(installation: PreparedInstallation, file: string): Promise<void> {
  const { skill, target, before, desired, legacyDirectory } = installation;
  await cp(skill.dir, file, { recursive: true });
  const staged = await snapshot(file);
  if (!staged || staged.kind !== 'directory' || staged.hash !== skill.hash) throw new Error('staged external skill mismatch');
  target.managed[skill.name] = ownershipRecord(desired, before ? [before.id, staged.id] : [staged.id], legacyDirectory);
  await writeJson(target.stateFile, target.managed);
}

async function publishCanonical(installation: PreparedInstallation): Promise<boolean> {
  const { skill, target, before, verifySource } = installation;
  if (before?.kind === 'directory' && before.hash === skill.hash) return false;
  await replaceEntry({ destination: target.canonical, previous: before, verifySource,
    prepare: (file) => stageCanonical(installation, file) });
  return true;
}

async function verifiedCanonical(installation: PreparedInstallation, committed: boolean): Promise<Snapshot & { kind: 'directory' }> {
  const { skill, target, before } = installation;
  const current = await snapshot(target.canonical);
  if (!current || current.kind !== 'directory' || current.hash !== skill.hash) throw new Error(`${target.canonical} changed after installation`);
  const verifiedIds = committed ? target.managed[skill.name].canonicalIds : before ? [before.id] : [];
  if (!verifiedIds.includes(current.id)) throw new Error(`${target.canonical} identity changed after installation`);
  return current;
}

async function commitOwnership(installation: PreparedInstallation, currentId: string): Promise<boolean> {
  const { skill, desired, target, legacyDirectory, verifySource } = installation;
  const pending = ownershipRecord(desired, [currentId], legacyDirectory);
  if (JSON.stringify(target.managed[skill.name]) === JSON.stringify(pending)) return false;
  await verifySource();
  target.managed[skill.name] = pending;
  await writeJson(target.stateFile, target.managed);
  return true;
}

async function publishClaude(installation: PreparedInstallation): Promise<boolean> {
  const { target, claudeBefore: before, verifySource } = installation;
  const { claude, canonical } = target;
  if (!claude || (before?.kind === 'link' && resolve(dirname(claude), before.target) === resolve(canonical))) return false;
  await replaceEntry({ destination: claude, previous: before, verifySource,
    prepare: async (file) => { await symlink(canonical, file, 'dir'); } });
  return true;
}

async function reconcile(skill: ResolvedExternalSkill, target: ReconcileTarget): Promise<boolean> {
  const { managed, stateFile } = target;
  const installation = await inspectInstallation(skill, target);
  const { desired, legacyDirectory, previousRecord } = installation;
  let canonicalCommitted = false;
  try {
    canonicalCommitted = await publishCanonical(installation);
    const current = await verifiedCanonical(installation, canonicalCommitted);
    let changed = canonicalCommitted;
    changed = await commitOwnership(installation, current.id) || changed;
    changed = await publishClaude(installation) || changed;
    if (legacyDirectory) {
      managed[skill.name] = ownershipRecord(desired, [current.id], undefined);
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

function groupSources(skills: ExternalSkill[]): ExternalGroup[] {
  const groups = new Map<string, ExternalGroup>();
  for (const skill of skills) {
    const key = `${skill.source}@${skill.ref}`;
    const group = groups.get(key);
    if (group) group.push(skill);
    else groups.set(key, [skill]);
  }
  return [...groups.values()];
}

async function claudeConfiguration(ctx: CliCtx): Promise<{ installed: boolean; frontendPlugin: boolean }> {
  const installed = await entryStat(join(ctx.home, '.claude')).then((stat) => stat !== undefined);
  const settings = object(installed ? await readOptionalJson(join(ctx.home, '.claude', 'settings.json')) : {});
  const frontendPlugin = Object.entries(object(settings.enabledPlugins ?? {})).some(([name, enabled]) =>
    name.startsWith('frontend-design@') && enabled === true);
  return { installed, frontendPlugin };
}

interface ReconcileContext {
  ctx: CliCtx;
  managed: ManagedState;
  stateFile: string;
  lockFile: string;
  global: Awaited<ReturnType<typeof loadGlobalState>>;
  claude: Awaited<ReturnType<typeof claudeConfiguration>>;
}

async function reconcileSkill(skill: ResolvedExternalSkill, context: ReconcileContext): Promise<boolean> {
  const { ctx, managed, stateFile, lockFile, global, claude: configuration } = context;
  if (global.skills[skill.name]) throw new Error(`${skill.name} is an own skill; refusing an external replacement`);
  const canonical = join(ctx.home, '.agents', 'skills', skill.name);
  const claude = configuration.installed && !(skill.name === 'frontend-design' && configuration.frontendPlugin)
    ? join(ctx.home, '.claude', 'skills', skill.name) : undefined;
  const changed = await reconcile(skill, { canonical, claude, managed, stateFile, lockFile });
  if (changed) console.log(`reconciled external ${skill.name}: ${skill.source}@${skill.ref}${skill.version ? ` (${skill.version})` : ''}`);
  return changed;
}

/** A failed source or install does not prevent checks for other catalogue entries. */
async function reconcileGroup(group: ExternalGroup, context: ReconcileContext, repaired: string[], errors: string[]): Promise<void> {
  let fetched: ResolvedExternalSkill[];
  try { fetched = await fetchExternalSource(group, context.ctx); }
  catch (error) { errors.push(`${group[0].source}: ${error}`); return; }
  for (const skill of fetched) {
    try { if (await reconcileSkill(skill, context)) repaired.push(skill.name); }
    catch (error) { errors.push(`${skill.name}: ${error}`); }
  }
}

/** Reconcile every catalogue entry. External files stay outside the repository. */
export async function opSyncExternal(ctx: CliCtx): Promise<string[]> {
  const skills = await loadCatalogue(ctx);
  const stateFile = join(ctx.home, '.config', 'my-skills', 'external.json');
  const managed = managedState(await readOptionalJson(stateFile));
  const lockFile = join(ctx.home, '.agents', '.skill-lock.json');
  const global = await loadGlobalState(ctx);
  const claude = await claudeConfiguration(ctx);
  const context: ReconcileContext = { ctx, managed, stateFile, lockFile, global, claude };
  const repaired: string[] = [];
  const errors: string[] = [];
  for (const group of groupSources(skills)) await reconcileGroup(group, context, repaired, errors);
  if (errors.length) throw new Error(`external skill check failed:\n${errors.join('\n')}`);
  console.log(`verified ${skills.length} external skills`);
  return repaired;
}
