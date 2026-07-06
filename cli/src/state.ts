import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname } from 'node:path';
import { globalStateFile, projectStateFile, type AgentId, type Ctx, type Scope } from './paths.js';

export interface InstalledSkill {
  version: string;
  agents: AgentId[];
}

export interface GlobalState {
  schemaVersion: 1;
  skills: Record<string, InstalledSkill>;
  declined: Record<string, string>;
}

export interface ProjectState {
  schemaVersion: 1;
  skills: Record<string, InstalledSkill>;
}

function stateFile(scope: Scope, ctx: Ctx): string {
  return scope === 'global' ? globalStateFile(ctx) : projectStateFile(ctx);
}

function emptyState(scope: Scope): GlobalState | ProjectState {
  return scope === 'global'
    ? { schemaVersion: 1, skills: {}, declined: {} }
    : { schemaVersion: 1, skills: {} };
}

export async function loadState(scope: Scope, ctx: Ctx): Promise<GlobalState | ProjectState> {
  try {
    return JSON.parse(await readFile(stateFile(scope, ctx), 'utf8'));
  } catch {
    return emptyState(scope);
  }
}

export async function saveState(
  scope: Scope,
  ctx: Ctx,
  state: GlobalState | ProjectState,
): Promise<void> {
  const file = stateFile(scope, ctx);
  await mkdir(dirname(file), { recursive: true });
  await writeFile(file, JSON.stringify(state, null, 2) + '\n');
}

export async function loadGlobalState(ctx: Ctx): Promise<GlobalState> {
  return (await loadState('global', ctx)) as GlobalState;
}

export async function saveGlobalState(ctx: Ctx, state: GlobalState): Promise<void> {
  await saveState('global', ctx, state);
}
