import { existsSync, readFileSync } from 'node:fs';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { storeDir, type Ctx } from './paths.js';

const pkg = JSON.parse(
  readFileSync(new URL('../package.json', import.meta.url), 'utf8'),
);

/**
 * Runs at the start of an agent session. The first session of a day starts
 * `update --all --global` in the background, so the session does not wait for
 * the network; pinned skills are not update candidates. Later sessions only
 * read the stamp.
 *
 * The session's directory can be an untrusted checkout, so the script leaves
 * it before npx reads a project `.npmrc` or `node_modules`. The CLI runs at
 * the version that wrote the script, never at an unseen newer one.
 */
const SESSION_UPDATE_SCRIPT = `#!/bin/sh
# Written by @jesdi/skills-cli; a global install or \`skills-cli hook\` writes it again.
dir="$HOME/.my-skills"
cd "$dir" || exit 0
stamp="$dir/.session-update.stamp"
[ -n "$(find "$stamp" -mtime -1 2>/dev/null)" ] && exit 0
touch "$stamp" 2>/dev/null || exit 0
(npx -y @jesdi/skills-cli@${pkg.version} update --all --global </dev/null >"$dir/.session-update.log" 2>&1 &)
exit 0
`;

/** Agent config files that take a `hooks.SessionStart` list, by the directory that owns them. */
const HOOK_CONFIGS = [
  ['.claude', 'settings.json'],
  ['.codex', 'hooks.json'],
] as const;

interface HookGroup {
  hooks?: { type: string; command: string; timeout?: number }[];
}

/** Add the command to the file's SessionStart hooks. False when it is already there. */
async function register(file: string, command: string): Promise<boolean> {
  let config: { hooks?: { SessionStart?: HookGroup[] } } = {};
  if (existsSync(file)) {
    try {
      config = JSON.parse(await readFile(file, 'utf8'));
    } catch (err) {
      throw new Error(`malformed config file ${file}: ${(err as Error).message}`);
    }
  }
  const groups = ((config.hooks ??= {}).SessionStart ??= []);
  if (groups.some((g) => g.hooks?.some((h) => h.command === command))) return false;
  groups.push({ hooks: [{ type: 'command', command, timeout: 10 }] });
  await writeFile(file, JSON.stringify(config, null, 2) + '\n');
  return true;
}

/**
 * Write the session update script into the global store and register it as a
 * SessionStart hook of every agent whose config directory exists. Returns the
 * config files it changed.
 */
export async function ensureSessionHook(ctx: Ctx): Promise<string[]> {
  const store = storeDir('global', ctx);
  const script = join(store, '.session-update.sh');
  await mkdir(store, { recursive: true });
  await writeFile(script, SESSION_UPDATE_SCRIPT);
  const changed: string[] = [];
  for (const [dir, name] of HOOK_CONFIGS) {
    if (!existsSync(join(ctx.home, dir))) continue;
    const file = join(ctx.home, dir, name);
    if (await register(file, `sh '${script}'`)) changed.push(file);
  }
  return changed;
}
