import { cp, lstat, mkdir, readlink, rm, symlink } from 'node:fs/promises';
import { join, resolve } from 'node:path';

export class ForeignEntryError extends Error {}

async function removeOurEntry(linkPath: string, storeDir: string): Promise<void> {
  const stat = await lstat(linkPath).catch(() => null);
  if (!stat) return;
  if (!stat.isSymbolicLink()) {
    throw new ForeignEntryError(
      `${linkPath} exists and was not created by skills-cli — refusing to touch it`,
    );
  }
  const target = await readlink(linkPath);
  if (!resolve(target).startsWith(resolve(storeDir))) {
    throw new ForeignEntryError(
      `${linkPath} is a symlink to ${target}, outside the skills-cli store — refusing to touch it`,
    );
  }
  await rm(linkPath);
}

export async function installSkill(opts: {
  name: string;
  sourceDir: string;
  storeDir: string;
  agentDirs: string[];
}): Promise<void> {
  const storeCopy = join(opts.storeDir, opts.name);
  // Validate every agent entry BEFORE mutating anything.
  for (const dir of opts.agentDirs) {
    const stat = await lstat(join(dir, opts.name)).catch(() => null);
    if (stat && !stat.isSymbolicLink()) {
      throw new ForeignEntryError(
        `${join(dir, opts.name)} exists and was not created by skills-cli — refusing to touch it`,
      );
    }
  }
  await rm(storeCopy, { recursive: true, force: true });
  await mkdir(opts.storeDir, { recursive: true });
  await cp(opts.sourceDir, storeCopy, { recursive: true });
  for (const dir of opts.agentDirs) {
    await mkdir(dir, { recursive: true });
    const linkPath = join(dir, opts.name);
    await removeOurEntry(linkPath, opts.storeDir);
    await symlink(storeCopy, linkPath, 'dir');
  }
}

export async function uninstallSkill(opts: {
  name: string;
  storeDir: string;
  agentDirs: string[];
}): Promise<void> {
  for (const dir of opts.agentDirs) {
    await removeOurEntry(join(dir, opts.name), opts.storeDir);
  }
  await rm(join(opts.storeDir, opts.name), { recursive: true, force: true });
}
