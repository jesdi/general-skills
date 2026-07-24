import {
  agentSkillsDir,
  cacheDir,
  storeDir,
  type AgentId,
  type Ctx,
  type Scope,
} from './paths.js';
import { fetchLatest, fetchVersion, type FetchLatestResult } from './registry.js';
import { loadState, saveState, loadGlobalState, saveGlobalState } from './state.js';
import { installSkill, uninstallSkill } from './store.js';
import { join } from 'node:path';

export interface CliCtx extends Ctx {
  registryUrl?: string;
  fetchImpl?: typeof fetch;
}

export async function fetchSkills(ctx: CliCtx): Promise<FetchLatestResult> {
  return fetchLatest({
    cacheDir: cacheDir(ctx),
    registryUrl: ctx.registryUrl,
    fetchImpl: ctx.fetchImpl,
  });
}

async function installFromFetched(
  fetched: FetchLatestResult,
  name: string,
  agents: AgentId[],
  scope: Scope,
  ctx: CliCtx,
): Promise<{ name: string; version: string }> {
  const skill = fetched.manifest.skills.find((s) => s.name === name);
  if (!skill) {
    throw new Error(`@jesdi/skills@${fetched.packageVersion} does not contain skill: ${name}`);
  }
  await installSkill({
    name,
    sourceDir: join(fetched.skillsDir, name),
    storeDir: storeDir(scope, ctx),
    agentDirs: agents.map((a) => agentSkillsDir(a, scope, ctx)),
  });
  const state = await loadState(scope, ctx);
  state.skills[name] = { version: skill.version, package: fetched.packageVersion, agents };
  await saveState(scope, ctx, state);
  return { name, version: skill.version };
}

export async function opInstall(
  names: string[],
  agents: AgentId[],
  scope: Scope,
  ctx: CliCtx,
): Promise<{ name: string; version: string }[]> {
  const fetched = await fetchSkills(ctx);
  const available = new Set(fetched.manifest.skills.map((s) => s.name));
  for (const name of names) {
    if (!available.has(name)) throw new Error(`unknown skill: ${name}`);
  }
  const installed: { name: string; version: string }[] = [];
  for (const name of names) {
    installed.push(await installFromFetched(fetched, name, agents, scope, ctx));
  }
  return installed;
}

export async function opUninstall(name: string, scope: Scope, ctx: CliCtx): Promise<void> {
  const state = await loadState(scope, ctx);
  const entry = state.skills[name];
  if (!entry) throw new Error(`skill not installed (${scope}): ${name}`);
  await uninstallSkill({
    name,
    storeDir: storeDir(scope, ctx),
    agentDirs: entry.agents.map((a) => agentSkillsDir(a, scope, ctx)),
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
    const entry = state.skills[name];
    if (!entry) throw new Error(`skill not installed (${scope}): ${name}`);
    await opInstall([name], entry.agents, scope, ctx);
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
