import * as p from '@clack/prompts';
import {
  opApplyUpdates,
  opCheckUpdates,
  opDecline,
  opInstall,
  opList,
  type CliCtx,
} from './ops.js';
import { AGENTS, type AgentId, type Scope } from './paths.js';

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
  const agents = await p.multiselect({
    message: 'Install for which agents?',
    options: (Object.entries(AGENTS) as [AgentId, { label: string }][]).map(([id, a]) => ({
      value: id,
      label: a.label,
    })),
    initialValues: ['claude' as AgentId],
    required: true,
  });
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

  const spinner = p.spinner();
  spinner.start('Installing\u2026');
  const installed = await opInstall(skills as string[], agents as AgentId[], scope, ctx);
  spinner.stop(`Installed ${installed.map((s) => `${s.name}@${s.version}`).join(', ')}`);
  p.outro('Done — your agents will pick the skills up on next launch.');
}

function cancel(): void {
  p.cancel('Cancelled — nothing was changed.');
}
