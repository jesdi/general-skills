import { mkdir, mkdtemp, readFile, readlink, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { renameNoReplace } from '../src/atomic-rename.js';

describe('atomic no-replace moves', () => {
  it.each(['directory', 'symlink'])('publishes a %s and preserves an existing destination', async (kind) => {
    const root = await mkdtemp(join(tmpdir(), 'exclusive-rename-'));
    const source = join(root, 'source');
    const destination = join(root, 'destination');
    const foreign = join(root, 'foreign');
    await mkdir(foreign);
    await writeFile(join(foreign, 'keep.txt'), 'foreign bytes');
    if (kind === 'directory') { await mkdir(source); await writeFile(join(source, 'own.txt'), 'own bytes'); }
    else await symlink(foreign, source);
    await symlink(foreign, destination);
    await expect(renameNoReplace(source, destination)).rejects.toMatchObject({ code: 'EEXIST' });
    expect(await readlink(destination)).toBe(foreign);
    expect(await readFile(join(destination, 'keep.txt'), 'utf8')).toBe('foreign bytes');
    await renameNoReplace(source, join(root, 'published'));
    const { lstat } = await import('node:fs/promises');
    expect((await lstat(join(root, 'published'))).isSymbolicLink()).toBe(kind === 'symlink');
  });
});
