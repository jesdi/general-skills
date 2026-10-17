import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { register, unregister } from '../src/hook.js';

describe.each([
  ['Claude Code', '.claude', 'settings.json'],
  ['Codex', '.codex', 'hooks.json'],
])('%s generic hook registry', (_agent, directory, filename) => {
  let home: string;
  let file: string;

  beforeEach(async () => {
    home = await mkdtemp(join(tmpdir(), 'hook-registry-'));
    await mkdir(join(home, directory));
    file = join(home, directory, filename);
  });

  afterEach(async () => {
    await rm(home, { recursive: true, force: true });
  });

  it('registers each event, matcher and command identity only once', async () => {
    expect(await register(file, 'PreToolUse', 'Bash', 'check.sh')).toBe(true);
    expect(await register(file, 'PostToolUse', 'Bash', 'check.sh')).toBe(true);
    expect(await register(file, 'PreToolUse', 'Edit', 'check.sh')).toBe(true);
    expect(await register(file, 'PreToolUse', undefined, 'check.sh')).toBe(true);
    expect(await register(file, 'PreToolUse', '', 'check.sh')).toBe(true);
    expect(await register(file, 'PreToolUse', 'Bash', 'other.sh')).toBe(true);
    const registered = await readFile(file, 'utf8');

    expect(await register(file, 'PreToolUse', 'Bash', 'check.sh')).toBe(false);
    expect(await register(file, 'PostToolUse', 'Bash', 'check.sh')).toBe(false);
    expect(await register(file, 'PreToolUse', 'Edit', 'check.sh')).toBe(false);
    expect(await register(file, 'PreToolUse', undefined, 'check.sh')).toBe(false);
    expect(await register(file, 'PreToolUse', '', 'check.sh')).toBe(false);
    expect(await register(file, 'PreToolUse', 'Bash', 'other.sh')).toBe(false);
    expect(await readFile(file, 'utf8')).toBe(registered);
    expect(JSON.parse(registered)).toEqual({
      hooks: {
        PreToolUse: [
          { matcher: 'Bash', hooks: [{ type: 'command', command: 'check.sh', timeout: 10 }] },
          { matcher: 'Edit', hooks: [{ type: 'command', command: 'check.sh', timeout: 10 }] },
          { hooks: [{ type: 'command', command: 'check.sh', timeout: 10 }] },
          { matcher: '', hooks: [{ type: 'command', command: 'check.sh', timeout: 10 }] },
          { matcher: 'Bash', hooks: [{ type: 'command', command: 'other.sh', timeout: 10 }] },
        ],
        PostToolUse: [
          { matcher: 'Bash', hooks: [{ type: 'command', command: 'check.sh', timeout: 10 }] },
        ],
      },
    });
  });

  it('removes only the selected identity and preserves sibling commands and settings', async () => {
    const target = { type: 'command', command: 'check.sh', timeout: 10 };
    const sibling = { type: 'command', command: 'other.sh', timeout: 30 };
    const config = {
      model: 'kept',
      hooks: {
        PreToolUse: [
          { matcher: 'Bash', hooks: [target, sibling] },
          { matcher: 'Bash', hooks: [target] },
          { matcher: 'Edit', hooks: [target] },
          { hooks: [target] },
          { matcher: '', hooks: [target] },
          { matcher: 'Bash', hooks: [] },
          { matcher: 'Bash' },
        ],
        PostToolUse: [{ matcher: 'Bash', hooks: [target] }],
      },
    };
    await writeFile(file, JSON.stringify(config));

    // An existing identity can share a group with a different command.
    expect(await register(file, 'PreToolUse', 'Bash', 'check.sh')).toBe(false);
    expect(await unregister(file, 'PreToolUse', 'Bash', 'check.sh')).toBe(true);
    const remaining = await readFile(file, 'utf8');
    expect(JSON.parse(remaining)).toEqual({
      ...config,
      hooks: {
        ...config.hooks,
        PreToolUse: [
          { matcher: 'Bash', hooks: [sibling] },
          ...config.hooks.PreToolUse.slice(2),
        ],
      },
    });
    expect(await unregister(file, 'PreToolUse', 'Bash', 'check.sh')).toBe(false);
    expect(await unregister(file, 'UnknownEvent', 'Bash', 'check.sh')).toBe(false);
    expect(await readFile(file, 'utf8')).toBe(remaining);

    expect(await unregister(file, 'PreToolUse', undefined, 'check.sh')).toBe(true);
    const withoutUnmatched = JSON.parse(await readFile(file, 'utf8'));
    expect(withoutUnmatched.hooks.PreToolUse).toContainEqual({ matcher: '', hooks: [target] });
    expect(withoutUnmatched.hooks.PreToolUse).not.toContainEqual({ hooks: [target] });
    expect(withoutUnmatched.hooks.PostToolUse).toEqual(config.hooks.PostToolUse);
  });
});
