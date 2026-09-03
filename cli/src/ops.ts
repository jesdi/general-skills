import {
  agentSkillsDir,
  cacheDir,
  globalStateFile,
  projectStateFile,
  storeDir,
  type AgentId,
  type Ctx,
  type Scope,
} from './paths.js';
import { fetchLatest, fetchVersion, type FetchLatestResult } from './registry.js';
import {
  agentsFor,
  loadState,
  saveState,
  loadGlobalState,
  saveGlobalState,
  validateAgents,
  type InstalledSkill,
} from './state.js';
import { installSkill, uninstallSkill } from './store.js';
import { join } from 'node:path';

export interface CliCtx extends Ctx {
  registryUrl?: string;
  fetchImpl?: typeof fetch;
  /**
   * Interactive hook used when an install has no agents to work with (no
   * `--agent`, no top-level `agents` in the target state). Absent means
   * non-interactive: such an install fails instead of asking.
   */
  promptAgents?: () => Promise<readonly AgentId[]>;
}

export async function fetchSkills(ctx: CliCtx): Promise<FetchLatestResult> {
  return fetchLatest({
    cacheDir: cacheDir(ctx),
    registryUrl: ctx.registryUrl,
    fetchImpl: ctx.fetchImpl,
  });
}

/**
 * Copy one skill into the store, link it into its agents and record it.
 * `override` is the entry's own `agents`; `undefined` means the entry
 * inherits the state's top-level `agents`.
 */
async function installFromFetched(
  fetched: FetchLatestResult,
  name: string,
  override: AgentId[] | undefined,
  scope: Scope,
  ctx: CliCtx,
): Promise<{ name: string; version: string }> {
  const skill = fetched.manifest.skills.find((s) => s.name === name);
  if (!skill) {
    throw new Error(`@jesdi/skills@${fetched.packageVersion} does not contain skill: ${name}`);
  }
  const state = await loadState(scope, ctx);
  const entry: InstalledSkill = { version: skill.version, package: fetched.packageVersion };
  if (override) entry.agents = validateAgents(override);
  state.skills[name] = entry;
  const agents = agentsFor(state, name);
  await installSkill({
    name,
    sourceDir: join(fetched.skillsDir, name),
    storeDir: storeDir(scope, ctx),
    agentDirs: agents.map((a) => agentSkillsDir(a, scope, ctx)),
  });
  await saveState(scope, ctx, state);
  return { name, version: skill.version };
}

/**
 * Make sure the scope has a top-level `agents` default, asking the user
 * (through `ctx.promptAgents`) and persisting the answer when it is missing.
 */
async function ensureDefaultAgents(scope: Scope, ctx: CliCtx): Promise<void> {
  const state = await loadState(scope, ctx);
  if (state.agents && state.agents.length > 0) return;
  const file = scope === 'global' ? globalStateFile(ctx) : projectStateFile(ctx);
  if (!ctx.promptAgents) {
    throw new Error(
      `no agents configured: add a top-level "agents" to ${file} or pass --agent`,
    );
  }
  state.agents = validateAgents(await ctx.promptAgents());
  await saveState(scope, ctx, state);
}

/**
 * Install skills. `agents` given → written as a per-skill override on each
 * entry; `undefined` → the entries inherit the scope's top-level `agents`
 * (asking for it once if it does not exist yet).
 */
export async function opInstall(
  names: string[],
  agents: AgentId[] | undefined,
  scope: Scope,
  ctx: CliCtx,
): Promise<{ name: string; version: string }[]> {
  const fetched = await fetchSkills(ctx);
  const available = new Set(fetched.manifest.skills.map((s) => s.name));
  for (const name of names) {
    if (!available.has(name)) throw new Error(`unknown skill: ${name}`);
  }
  if (agents) validateAgents(agents);
  else await ensureDefaultAgents(scope, ctx);
  const installed: { name: string; version: string }[] = [];
  for (const name of names) {
    installed.push(await installFromFetched(fetched, name, agents, scope, ctx));
  }
  return installed;
}

export async function opUninstall(name: string, scope: Scope, ctx: CliCtx): Promise<void> {
  const state = await loadState(scope, ctx);
  if (!state.skills[name]) throw new Error(`skill not installed (${scope}): ${name}`);
  await uninstallSkill({
    name,
    storeDir: storeDir(scope, ctx),
    agentDirs: agentsFor(state, name).map((a) => agentSkillsDir(a, scope, ctx)),
  });
  delete state.skills[name];
  await saveState(scope, ctx, state);
}

export interface UpdateCandidate {
  name: string;
  from: string;
  to: string;
}

export async function opCheckUpdates(scope: Scope, ctx: CliCtx): Promise<UpdateCandidate[]> {
  const { manifest } = await fetchSkills(ctx);
  const available = new Map(manifest.skills.map((s) => [s.name, s]));
  const state = await loadState(scope, ctx);
  const globalState = await loadGlobalState(ctx);
  const candidates: UpdateCandidate[] = [];
  for (const [name, installed] of Object.entries(state.skills)) {
    const latest = available.get(name);
    if (!latest || latest.version === installed.version) continue;
    if (globalState.declined[name] === latest.version) continue;
    candidates.push({ name, from: installed.version, to: latest.version });
  }
  return candidates;
}

export async function opApplyUpdates(names: string[], scope: Scope, ctx: CliCtx): Promise<void> {
  const state = await loadState(scope, ctx);
  const globalState = await loadGlobalState(ctx);
  for (const name of names) {
    if (!state.skills[name]) throw new Error(`skill not installed (${scope}): ${name}`);
    agentsFor(state, name); // fail before touching anything if the config is unresolvable
  }
  const fetched = await fetchSkills(ctx);
  for (const name of names) {
    // Reinstall with the entry's own shape: an inherited entry stays inherited.
    await installFromFetched(fetched, name, state.skills[name].agents, scope, ctx);
    if (globalState.declined[name]) {
      delete globalState.declined[name];
      await saveGlobalState(ctx, globalState);
    }
  }
}

export async function opDecline(name: string, version: string, ctx: CliCtx): Promise<void> {
  const globalState = await loadGlobalState(ctx);
  globalState.declined[name] = version;
  await saveGlobalState(ctx, globalState);
}

export async function opSync(ctx: CliCtx): Promise<{ name: string; version: string }[]> {
  const state = await loadState('local', ctx);
  const entries = Object.entries(state.skills);
  if (entries.length === 0) {
    throw new Error('no .my-skills.json with skills found in this project — nothing to sync');
  }
  // Validate the whole file before materializing anything.
  for (const [name] of entries) agentsFor(state, name);
  const results: { name: string; version: string }[] = [];
  for (const [name, entry] of entries) {
    const fetched = entry.package
      ? await fetchVersion(entry.package, {
          cacheDir: cacheDir(ctx),
          registryUrl: ctx.registryUrl,
          fetchImpl: ctx.fetchImpl,
        })
      : await fetchSkills(ctx);
    if (entry.package) {
      const skill = fetched.manifest.skills.find((s) => s.name === name);
      if (!skill || skill.version !== entry.version) {
        throw new Error(
          `pinned @jesdi/skills@${entry.package} does not contain ${name}@${entry.version} — ` +
            `.my-skills.json is inconsistent`,
        );
      }
    }
    // Pass the entry's own shape through: inherited stays inherited.
    results.push(await installFromFetched(fetched, name, entry.agents, 'local', ctx));
  }
  return results;
}

export interface ListedSkill {
  name: string;
  description: string;
  latest: string;
  installedGlobal?: string;
  installedLocal?: string;
}

export async function opList(ctx: CliCtx): Promise<ListedSkill[]> {
  const { manifest } = await fetchSkills(ctx);
  const globalState = await loadState('global', ctx);
  const localState = await loadState('local', ctx);
  return manifest.skills.map((s) => ({
    name: s.name,
    description: s.description,
    latest: s.version,
    installedGlobal: globalState.skills[s.name]?.version,
    installedLocal: localState.skills[s.name]?.version,
  }));
}
