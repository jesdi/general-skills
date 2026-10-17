import { existsSync } from 'node:fs';
import { mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { storeDir, type Ctx } from './paths.js';
import { loadGlobalState, saveGlobalState } from './state.js';

/**
 * Runs a background check on every session. The CLI verifies external skills
 * each time, refreshes their catalogue and updates own skills once a day.
 *
 * The session's directory can be an untrusted checkout, so the script leaves
 * it before npx reads a project `.npmrc` or `node_modules`. The CLI runs at
 * its latest release, so a new release reaches every machine without a new
 * `skills-cli hook`.
 */
const SESSION_UPDATE_SCRIPT = `#!/bin/sh
# Written by @jesdi/skills-cli; \`skills-cli hook\` writes it again, \`skills-cli hook --off\` removes it.
# Read the hook input to its end: a runner that writes it to a closed pipe gets EPIPE.
cat >/dev/null
dir="$HOME/.my-skills"
cd "$dir" || exit 0
(npx -y @jesdi/skills-cli@latest session-update </dev/null >>"$dir/.session-update.log" 2>&1 &)
exit 0
`;

/** Agent config files that take a `hooks.<event>` list, by the directory that owns them. */
const HOOK_CONFIGS = [
  ['.claude', 'settings.json'],
  ['.codex', 'hooks.json'],
] as const;

interface HookGroup {
  matcher?: string;
  hooks?: { type: string; command: string; timeout?: number }[];
}

type HookConfig = { hooks?: Record<string, HookGroup[]> };

async function readConfig(file: string): Promise<HookConfig> {
  if (!existsSync(file)) return {};
  try {
    return JSON.parse(await readFile(file, 'utf8'));
  } catch (err) {
    throw new Error(`malformed config file ${file}: ${(err as Error).message}`);
  }
}

/** Add the command to the file's hooks for the event and matcher. False when it is already there. */
export async function register(file: string, event: string, matcher: string | undefined, command: string): Promise<boolean> {
  const config = await readConfig(file);
  const groups = ((config.hooks ??= {})[event] ??= []);
  if (groups.some((g) => g.matcher === matcher && g.hooks?.some((h) => h.command === command))) return false;
  groups.push({ ...(matcher === undefined ? {} : { matcher }), hooks: [{ type: 'command', command, timeout: 10 }] });
  await writeFile(file, JSON.stringify(config, null, 2) + '\n');
  return true;
}

/** Remove the command from the file's hooks for the event and matcher. False when it is not there. */
export async function unregister(file: string, event: string, matcher: string | undefined, command: string): Promise<boolean> {
  if (!existsSync(file)) return false;
  const config = await readConfig(file);
  const groups = config.hooks?.[event] ?? [];
  const kept = groups
    .map((g) => (g.matcher === matcher ? { ...g, hooks: g.hooks?.filter((h) => h.command !== command) } : g))
    .filter((g, i) => g.hooks?.length || !groups[i].hooks?.length);
  if (JSON.stringify(kept) === JSON.stringify(groups)) return false;
  config.hooks![event] = kept;
  await writeFile(file, JSON.stringify(config, null, 2) + '\n');
  return true;
}

function scriptPath(ctx: Ctx): string {
  return join(storeDir('global', ctx), '.session-update.sh');
}

/**
 * Write the session update script into the global store and register it as a
 * SessionStart hook of every agent whose config directory exists. Returns the
 * config files it changed.
 */
export async function ensureSessionHook(ctx: Ctx): Promise<string[]> {
  const script = scriptPath(ctx);
  await mkdir(storeDir('global', ctx), { recursive: true });
  await writeFile(script, SESSION_UPDATE_SCRIPT);
  const changed: string[] = [];
  for (const [dir, name] of HOOK_CONFIGS) {
    if (!existsSync(join(ctx.home, dir))) continue;
    const file = join(ctx.home, dir, name);
    if (await register(file, 'SessionStart', undefined, `sh '${script}'`)) changed.push(file);
  }
  return changed;
}

/** Unregister the hook from every agent config and delete the script. Returns the config files it changed. */
export async function removeSessionHook(ctx: Ctx): Promise<string[]> {
  const script = scriptPath(ctx);
  const changed: string[] = [];
  for (const [dir, name] of HOOK_CONFIGS) {
    const file = join(ctx.home, dir, name);
    if (await unregister(file, 'SessionStart', undefined, `sh '${script}'`)) changed.push(file);
  }
  await rm(script, { force: true });
  return changed;
}

/** Record the auto-update choice and add or remove the hook to match. Returns the config files it changed. */
export async function setAutoUpdate(ctx: Ctx, on: boolean): Promise<string[]> {
  const state = await loadGlobalState(ctx);
  state.autoUpdate = on;
  await saveGlobalState(ctx, state);
  return on ? ensureSessionHook(ctx) : removeSessionHook(ctx);
}
