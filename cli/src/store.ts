import { cp, lstat, mkdir, mkdtemp, readlink, rename, rm, symlink } from 'node:fs/promises';
import { dirname, join, resolve, sep } from 'node:path';

export class ForeignEntryError extends Error {}

async function ownedTarget(linkPath: string, storeDir: string): Promise<string | null> {
  const stat = await lstat(linkPath).catch(() => null);
  if (!stat) return null;
  if (!stat.isSymbolicLink()) {
    throw new ForeignEntryError(
      `${linkPath} exists and was not created by skills-cli — refusing to touch it`,
    );
  }
  const target = await readlink(linkPath);
  // A relative target is relative to the link's directory, not the cwd.
  if (!resolve(dirname(linkPath), target).startsWith(resolve(storeDir) + sep)) {
    throw new ForeignEntryError(
      `${linkPath} is a symlink to ${target}, outside the skills-cli store — refusing to touch it`,
    );
  }
  return target;
}

async function removeOurEntry(linkPath: string, storeDir: string): Promise<void> {
  if (await ownedTarget(linkPath, storeDir) !== null) await rm(linkPath);
}

export async function installSkill(opts: {
  name: string;
  sourceDir: string;
  storeDir: string;
  agentDirs: string[];
}): Promise<void> {
  const storeCopy = join(opts.storeDir, opts.name);
  // Reject foreign symlink targets before replacing the installed manifest.
  for (const dir of opts.agentDirs) await ownedTarget(join(dir, opts.name), opts.storeDir);
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

/** Keep the installed tree and links recoverable until state persistence succeeds. */
export async function withStoreRollback<T>(
  opts: { name: string; storeDir: string; agentDirs: string[] },
  fn: () => Promise<T>,
): Promise<T> {
  const links = [...new Set(opts.agentDirs)].map((dir) => join(dir, opts.name));
  const targets = await Promise.all(links.map((link) => ownedTarget(link, opts.storeDir)));
  await mkdir(opts.storeDir, { recursive: true });
  const backup = await mkdtemp(join(opts.storeDir, '.rollback-'));
  const installed = join(opts.storeDir, opts.name);
  const saved = join(backup, opts.name);
  const existed = await lstat(installed).then(() => true, () => false);
  try {
    if (existed) await cp(installed, saved, { recursive: true, verbatimSymlinks: true });
    try {
      return await fn();
    } catch (err) {
      await rm(installed, { recursive: true, force: true });
      if (existed) await rename(saved, installed);
      await Promise.all(links.map((link, i) => restoreLink(link, targets[i], opts.storeDir)));
      throw err;
    }
  } finally {
    await rm(backup, { recursive: true, force: true });
  }
}

async function restoreLink(link: string, target: string | null, storeDir: string): Promise<void> {
  if (await ownedTarget(link, storeDir) === target) return;
  await removeOurEntry(link, storeDir);
  if (target !== null) await symlink(target, link, 'dir');
}
