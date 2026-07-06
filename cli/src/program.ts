import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { Command } from 'commander';
import {
  opApplyUpdates,
  opCheckUpdates,
  opInstall,
  opList,
  opSync,
  opUninstall,
  type CliCtx,
} from './ops.js';
import { AGENTS, type AgentId, type Scope } from './paths.js';

const pkg = JSON.parse(
  readFileSync(new URL('../package.json', import.meta.url), 'utf8'),
);

function parseAgents(value: string): AgentId[] {
  const agents = value.split(',').map((a) => a.trim());
  for (const a of agents) {
    if (!(a in AGENTS)) throw new Error(`unknown agent: ${a} (expected claude|opencode)`);
  }
  return agents as AgentId[];
}

function scopeOf(opts: { global?: boolean }): Scope {
  return opts.global ? 'global' : 'local';
}

export function buildProgram(ctx?: CliCtx): Command {
  const cliCtx: CliCtx = ctx ?? { home: homedir(), project: process.cwd() };
  const program = new Command('skills-cli');
  program
    .description("Install jesdi's agent skills into Claude Code and OpenCode")
    .version(pkg.version);

  program
    .command('install')
    .argument('<skills...>', 'skill names to install')
    .option('--agent <list>', 'comma-separated agents: claude,opencode', 'claude')
    .option('--global', 'install for the whole machine instead of this project')
    .action(async (skills: string[], opts: { agent: string; global?: boolean }) => {
      const installed = await opInstall(skills, parseAgents(opts.agent), scopeOf(opts), cliCtx);
      for (const s of installed) console.log(`installed ${s.name}@${s.version}`);
    });

  program
    .command('update')
    .argument('[skill]', 'update a single skill')
    .option('--all', 'apply every available update')
    .option('--global', 'operate on the global scope')
    .action(async (skill: string | undefined, opts: { all?: boolean; global?: boolean }) => {
      const scope = scopeOf(opts);
      const candidates = await opCheckUpdates(scope, cliCtx);
      if (candidates.length === 0) {
        console.log('everything is up to date');
        return;
      }
      const names = skill ? [skill] : opts.all ? candidates.map((c) => c.name) : null;
      if (!names) {
        for (const c of candidates) console.log(`${c.name}: ${c.from} -> ${c.to}`);
        console.log('run `skills-cli update --all` or `skills-cli update <skill>` to apply');
        return;
      }
      await opApplyUpdates(names, scope, cliCtx);
      for (const n of names) console.log(`updated ${n}`);
    });

  program
    .command('sync')
    .description('install everything listed in this project\'s .my-skills.json')
    .action(async () => {
      const results = await opSync(cliCtx);
      for (const s of results) console.log(`synced ${s.name}@${s.version}`);
    });

  program
    .command('list')
    .description('list available skills and where they are installed')
    .action(async () => {
      for (const s of await opList(cliCtx)) {
        const marks = [
          s.installedGlobal ? `global@${s.installedGlobal}` : null,
          s.installedLocal ? `local@${s.installedLocal}` : null,
        ]
          .filter(Boolean)
          .join(', ');
        console.log(`${s.name}@${s.latest}${marks ? ` [${marks}]` : ''} — ${s.description}`);
      }
    });

  program
    .command('uninstall')
    .argument('<skill>')
    .option('--global', 'uninstall from the global scope')
    .action(async (skill: string, opts: { global?: boolean }) => {
      await opUninstall(skill, scopeOf(opts), cliCtx);
      console.log(`uninstalled ${skill}`);
    });

  program.action(async () => {
    const { runWizard } = await import('./wizard.js');
    await runWizard(cliCtx);
  });

  return program;
}
