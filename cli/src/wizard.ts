import * as p from '@clack/prompts';
import {
  opApplyUpdates,
  opCheckUpdates,
  opDecline,
  opInstall,
  opList,
  type CliCtx,
} from './ops.js';
import { type AgentId, type Scope } from './paths.js';
import { selectAgents } from './prompts.js';
import { loadState, saveState } from './state.js';

export async function runWizard(ctx: CliCtx): Promise<void> {
  p.intro('@jesdi/skills');

  // 1. Update prompts first (DESIGN.md: check on every run, remember declines per version).
  for (const scope of ['global', 'local'] as Scope[]) {
    const candidates = await opCheckUpdates(scope, ctx);
    for (const c of candidates) {
      const answer = await p.confirm({
        message: `Update ${c.name} (${scope}) ${c.from} -> ${c.to}?`,
      });
      if (p.isCancel(answer)) return cancel();
      if (answer) await opApplyUpdates([c.name], scope, ctx);
      else await opDecline(c.name, c.to, ctx);
    }
  }

  // 2. Pick skills.
  const available = await opList(ctx);
  const skills = await p.multiselect({
    message: 'Which skills do you want to install?',
    options: available.map((s) => ({
      value: s.name,
      label: s.name,
      hint: s.description.slice(0, 80),
    })),
    required: true,
  });
  if (p.isCancel(skills)) return cancel();

  // 3. Pick agents.
  const agents = await selectAgents();
  if (p.isCancel(agents)) return cancel();

  // 4. Pick scope.
  const scope = await p.select({
    message: 'Install where?',
    options: [
      { value: 'local' as Scope, label: `This project (${ctx.project})` },
      { value: 'global' as Scope, label: 'Globally (whole machine)' },
    ],
  });
  if (p.isCancel(scope)) return cancel();

  // Same rule as `install --agent`: the first choice becomes the scope's
  // top-level default; later choices are per-skill overrides - unless they
  // match the default, in which case the entries simply inherit it.
  const override = await settleAgents(agents as AgentId[], scope, ctx);

  const spinner = p.spinner();
  spinner.start('Installing\u2026');
  const installed = await opInstall(skills as string[], override, scope, ctx);
  spinner.stop(`Installed ${installed.map((s) => `${s.name}@${s.version}`).join(', ')}`);
  p.outro('Done — your agents will pick the skills up on next launch.');
}

async function settleAgents(
  chosen: AgentId[],
  scope: Scope,
  ctx: CliCtx,
): Promise<AgentId[] | undefined> {
  const state = await loadState(scope, ctx);
  const current = state.agents ?? [];
  if (current.length === 0) {
    state.agents = chosen;
    await saveState(scope, ctx, state);
    return undefined;
  }
  const sameSet = chosen.length === current.length && chosen.every((a) => current.includes(a));
  return sameSet ? undefined : chosen;
}

function cancel(): void {
  p.cancel('Cancelled — nothing was changed.');
}
