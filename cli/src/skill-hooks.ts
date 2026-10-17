import { existsSync } from 'node:fs';
import { readFile, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { groupsOf, register, unregister } from './hook.js';
import { storeDir, type AgentId, type Ctx, type Scope } from './paths.js';

/** One hook a skill ships: `command` is relative to the skill folder. */
interface SkillHook {
  event: string;
  matcher?: string;
  command: string;
}

/** Agent config file by agent; agents without an entry (OpenCode) take no hooks. */
const CONFIG: Partial<Record<AgentId, readonly [string, string]>> = {
  claude: ['.claude', 'settings.json'],
  codex: ['.codex', 'hooks.json'],
};

export const CODEX_TRUST_NOTE =
  'Codex asks you to trust hook changes: start codex, run /hooks, review the new entries and trust them. ' +
  'The CLI never writes Codex trust.';

/** The hooks in `<skillDir>/hooks.json`; none when the skill ships no such file. */
export async function readSkillHooks(skillDir: string): Promise<SkillHook[]> {
  const file = join(skillDir, 'hooks.json');
  if (!existsSync(file)) return [];
  const config = JSON.parse(await readFile(file, 'utf8')) as {
    hooks?: Record<string, { matcher?: string; hooks?: { command: string }[] }[]>;
  };
  return Object.entries(config.hooks ?? {}).flatMap(([event, groups]) =>
    groups.flatMap((g) => (g.hooks ?? []).map((h) => ({ event, matcher: g.matcher, command: h.command }))),
  );
}

/**
 * The command string the agent runs. It depends only on scope, skill and the
 * script path, never on the script's content, so an update leaves Codex's trust alone.
 * A project command resolves the store from the git root and exits 0 when it is
 * missing, so a committed config works on a clone without the store.
 */
function commandFor(skill: string, rel: string, scope: Scope, ctx: Ctx): string {
  if (scope === 'global') return `sh '${join(storeDir('global', ctx), skill, rel).replaceAll("'", `'\\''`)}'`;
  return `f="$(git rev-parse --show-toplevel 2>/dev/null)/.my-skills/${skill}/${rel}"; [ -f "$f" ] && exec sh "$f"; exit 0`;
}

const key = (h: SkillHook) => `${h.event}\0${h.matcher ?? ''}\0${h.command}`;

function configFile(agent: AgentId, scope: Scope, ctx: Ctx): string | undefined {
  const parts = CONFIG[agent];
  return parts && join(scope === 'global' ? ctx.home : ctx.project, ...parts);
}

/** Drop the entries of `gone` from one config file. True when Codex trust positions moved. */
async function removeHooks(file: string, gone: SkillHook[]): Promise<boolean> {
  let moved = false;
  for (const h of gone) {
    const groups = await groupsOf(file, h.event);
    if (!(await unregister(file, h.event, h.matcher, h.command, true))) continue;
    // Codex keys trust by position: a group that is no longer where it was needs a new look.
    const now = await groupsOf(file, h.event);
    moved ||= now.some((g, i) => JSON.stringify(g) !== JSON.stringify(groups[i]));
  }
  return moved;
}

/** Run `fn`; when it throws, put the hook configs of the scope back as they were. */
export async function withHookRollback<T>(scope: Scope, ctx: Ctx, fn: () => Promise<T>): Promise<T> {
  const files = (Object.keys(CONFIG) as AgentId[]).map((a) => configFile(a, scope, ctx)!);
  const saved = await Promise.all(files.map((f) => (existsSync(f) ? readFile(f, 'utf8') : null)));
  try {
    return await fn();
  } catch (err) {
    await Promise.all(files.map((f, i) => (saved[i] === null ? rm(f, { force: true }) : writeFile(f, saved[i]!))));
    throw err;
  }
}

/**
 * Make each agent's hook config match the skill's hooks: entries in `before`
 * but not in `after` go, entries in `after` come. Only entries this install
 * created (`owned`) are removed; entries of other owners are never touched.
 * Returns the owned set afterwards, whether Codex needs the owner to trust
 * something, and whether an agent without hook support (`.agents/skills`) was
 * left out.
 */
export async function syncSkillHooks(opts: {
  skill: string;
  agents: readonly AgentId[];
  scope: Scope;
  ctx: Ctx;
  before: SkillHook[];
  after: SkillHook[];
  owned: readonly string[];
}): Promise<{ codexTrust: boolean; byHand: boolean; owned: string[] }> {
  const { skill, agents, scope, ctx } = opts;
  const resolve = (hooks: SkillHook[]) => hooks.map((h) => ({ ...h, command: commandFor(skill, h.command, scope, ctx) }));
  const before = resolve(opts.before);
  const after = resolve(opts.after);
  const gone = before.filter((b) => !after.some((h) => key(h) === key(b)));
  const owned = new Set(opts.owned);
  let codexTrust = false;
  // Codex last: its trust note depends on what the other agents' sync left unchanged.
  for (const agent of [...agents].sort((a) => (a === 'codex' ? 1 : -1))) {
    const f = configFile(agent, scope, ctx);
    if (!f) continue;
    const trust = await syncAgent(f, agent, gone, after, owned);
    if (agent === 'codex') codexTrust = trust;
  }
  return { codexTrust, byHand: after.length > 0 && agents.some((a) => !CONFIG[a]), owned: [...owned] };
}

/** Sync one agent's config file, updating `owned`. True when something changed that Codex must trust. */
async function syncAgent(file: string, agent: AgentId, gone: SkillHook[], after: SkillHook[], owned: Set<string>): Promise<boolean> {
  const own = (h: SkillHook) => `${agent}\0${key(h)}`;
  const mine = gone.filter((h) => owned.has(own(h)));
  let changed = await removeHooks(file, mine);
  for (const h of mine) owned.delete(own(h));
  for (const h of after) {
    const added = await register(file, h.event, h.matcher, h.command);
    if (added) owned.add(own(h));
    changed ||= added;
  }
  return changed;
}

/** Tell the owner what the hook sync leaves to them. */
export function sayHookNotes(
  say: ((msg: string) => void) | undefined,
  skill: string,
  r: { codexTrust: boolean; byHand: boolean },
): void {
  if (r.codexTrust) say?.(CODEX_TRUST_NOTE);
  if (r.byHand) say?.(`${skill}: no hooks for .agents/skills — start \`/${skill} <url>\` by hand there`);
}
