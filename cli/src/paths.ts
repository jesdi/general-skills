import { join } from 'node:path';

export type AgentId = 'claude' | 'opencode';
export type Scope = 'global' | 'local';

export const AGENTS: Record<AgentId, { label: string }> = {
  claude: { label: 'Claude Code' },
  opencode: { label: 'OpenCode (.agents standard)' },
};

export interface Ctx {
  home: string;
  project: string;
}

const AGENT_SUBDIR: Record<AgentId, string> = {
  claude: join('.claude', 'skills'),
  opencode: join('.agents', 'skills'),
};

function scopeRoot(scope: Scope, ctx: Ctx): string {
  return scope === 'global' ? ctx.home : ctx.project;
}

export function agentSkillsDir(agent: AgentId, scope: Scope, ctx: Ctx): string {
  return join(scopeRoot(scope, ctx), AGENT_SUBDIR[agent]);
}

export function storeDir(scope: Scope, ctx: Ctx): string {
  return join(scopeRoot(scope, ctx), '.my-skills');
}

export function globalStateFile(ctx: Ctx): string {
  return join(ctx.home, '.config', 'my-skills', 'state.json');
}

export function projectStateFile(ctx: Ctx): string {
  return join(ctx.project, '.my-skills.json');
}

export function cacheDir(ctx: Ctx): string {
  return join(ctx.home, '.cache', 'my-skills');
}
