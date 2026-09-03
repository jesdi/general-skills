import * as p from '@clack/prompts';
import { AGENTS, type AgentId } from './paths.js';

/** The one agent picker, shared by the wizard and `install` without `--agent`. */
export async function selectAgents(
  initialValues: readonly AgentId[] = ['claude'],
): Promise<AgentId[] | symbol> {
  return p.multiselect({
    message: 'Install for which agents?',
    options: (Object.entries(AGENTS) as [AgentId, { label: string }][]).map(([id, a]) => ({
      value: id,
      label: a.label,
    })),
    initialValues: [...initialValues],
    required: true,
  });
}

/**
 * `CliCtx.promptAgents` implementation for an interactive terminal: ask once
 * and treat a cancel as an abort so nothing gets written.
 */
export async function promptAgentsInteractive(): Promise<AgentId[]> {
  const agents = await selectAgents();
  if (p.isCancel(agents)) throw new Error('cancelled — nothing was changed');
  return agents as AgentId[];
}
