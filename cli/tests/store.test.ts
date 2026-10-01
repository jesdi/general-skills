import { lstat, mkdir, mkdtemp, readFile, readlink, symlink, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { ForeignEntryError, installSkill, uninstallSkill } from '../src/store.js';

async function setup() {
  const root = await mkdtemp(join(tmpdir(), 'store-'));
  const sourceDir = join(root, 'src', 'hello-world');
  await mkdir(sourceDir, { recursive: true });
  await writeFile(join(sourceDir, 'SKILL.md'), 'v1');
  const storeDir = join(root, 'store');
  const agentDir = join(root, 'claude', 'skills');
  return { root, sourceDir, storeDir, agentDir };
}

describe('installSkill', () => {
  it('copies to the store and symlinks agent dirs', async () => {
    const { sourceDir, storeDir, agentDir } = await setup();
    await installSkill({ name: 'hello-world', sourceDir, storeDir, agentDirs: [agentDir] });
    const link = join(agentDir, 'hello-world');
    expect((await lstat(link)).isSymbolicLink()).toBe(true);
    expect(await readlink(link)).toBe(join(storeDir, 'hello-world'));
    expect(await readFile(join(link, 'SKILL.md'), 'utf8')).toBe('v1');
  });

  it('is idempotent and refreshes content', async () => {
    const { sourceDir, storeDir, agentDir } = await setup();
    await installSkill({ name: 'hello-world', sourceDir, storeDir, agentDirs: [agentDir] });
    await writeFile(join(sourceDir, 'SKILL.md'), 'v2');
    await installSkill({ name: 'hello-world', sourceDir, storeDir, agentDirs: [agentDir] });
    expect(await readFile(join(agentDir, 'hello-world', 'SKILL.md'), 'utf8')).toBe('v2');
  });

  it('refuses to clobber a foreign entry in an agent dir', async () => {
    const { sourceDir, storeDir, agentDir } = await setup();
    await mkdir(join(agentDir, 'hello-world'), { recursive: true });
    await writeFile(join(agentDir, 'hello-world', 'SKILL.md'), 'user-made');
    await expect(
      installSkill({ name: 'hello-world', sourceDir, storeDir, agentDirs: [agentDir] }),
    ).rejects.toThrow(ForeignEntryError);
  });

  it('replaces a relative symlink that points into the store', async () => {
    const { sourceDir, storeDir, agentDir } = await setup();
    await mkdir(agentDir, { recursive: true });
    await symlink('../../store/hello-world', join(agentDir, 'hello-world'), 'dir');
    await installSkill({ name: 'hello-world', sourceDir, storeDir, agentDirs: [agentDir] });
    expect(await readlink(join(agentDir, 'hello-world'))).toBe(join(storeDir, 'hello-world'));
  });

  it('refuses a symlink that points outside the store', async () => {
    const { root, sourceDir, storeDir, agentDir } = await setup();
    await mkdir(agentDir, { recursive: true });
    await symlink(join(root, 'store-other'), join(agentDir, 'hello-world'), 'dir');
    await expect(
      installSkill({ name: 'hello-world', sourceDir, storeDir, agentDirs: [agentDir] }),
    ).rejects.toThrow(ForeignEntryError);
  });
});

describe('uninstallSkill', () => {
  it('removes links and store copy, tolerating missing entries', async () => {
    const { sourceDir, storeDir, agentDir } = await setup();
    await installSkill({ name: 'hello-world', sourceDir, storeDir, agentDirs: [agentDir] });
    await uninstallSkill({ name: 'hello-world', storeDir, agentDirs: [agentDir] });
    expect(existsSync(join(agentDir, 'hello-world'))).toBe(false);
    expect(existsSync(join(storeDir, 'hello-world'))).toBe(false);
    await uninstallSkill({ name: 'hello-world', storeDir, agentDirs: [agentDir] });
  });
});
