import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname } from 'node:path';
import {
  AGENTS,
  globalStateFile,
  projectStateFile,
  type AgentId,
  type Ctx,
  type Scope,
} from './paths.js';

export interface InstalledSkill {
  version: string;
  package?: string;
  /** Per-skill override; when absent the entry inherits the state's top-level `agents`. */
  agents?: AgentId[];
}

export interface GlobalState {
  schemaVersion: 1;
  /** Default agents for every skill without its own `agents`. */
  agents?: AgentId[];
  skills: Record<string, InstalledSkill>;
  declined: Record<string, string>;
}

export interface ProjectState {
  schemaVersion: 1;
  /** Default agents for every skill without its own `agents`. */
  agents?: AgentId[];
  skills: Record<string, InstalledSkill>;
}

const AGENT_IDS = Object.keys(AGENTS).join('|');

/** Validate a list of agent ids (from a flag, a prompt, or a state file). */
export function validateAgents(agents: readonly string[]): AgentId[] {
  for (const a of agents) {
    if (!(a in AGENTS)) throw new Error(`unknown agent: ${a} (expected ${AGENT_IDS})`);
  }
  return [...agents] as AgentId[];
}

/**
 * Resolve which agents a skill is linked into: the entry's own `agents`
 * overrides the state's top-level `agents`.
 */
export function agentsFor(state: GlobalState | ProjectState, name: string): AgentId[] {
  const agents = state.skills[name]?.agents ?? state.agents;
  if (!agents || agents.length === 0) {
    throw new Error(
      `no agents configured for ${name}: add a top-level "agents" to .my-skills.json or pass --agent`,
    );
  }
  return validateAgents(agents);
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
  const file = stateFile(scope, ctx);
  let raw: string;
  try {
    raw = await readFile(file, 'utf8');
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === 'ENOENT') return emptyState(scope);
    throw err;
  }
  try {
    return JSON.parse(raw);
  } catch (err) {
    throw new Error(`malformed state file ${file}: ${(err as Error).message}`);
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
