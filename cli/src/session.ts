import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import lockfile from 'proper-lockfile';
import { opApplyUpdates, opCheckUpdates, type CliCtx } from './ops.js';
import { storeDir } from './paths.js';
import { DAY } from './external-catalogue.js';
import { opSyncExternal } from './external.js';

/** Daily own-skill refresh is separate from the per-session external check. */
async function updateOwnSkills(ctx: CliCtx, stamp: string, last: number): Promise<void> {
  if (Number.isFinite(last) && Date.now() - last < DAY) return;
  const boundedCtx: CliCtx = { ...ctx, fetchImpl: (input, init) =>
    (ctx.fetchImpl ?? fetch)(input, { ...init, signal: AbortSignal.timeout(30_000) }) };
  const candidates = await opCheckUpdates('global', boundedCtx);
  if (candidates.length) await opApplyUpdates(candidates.map((c) => c.name), 'global', boundedCtx);
  await writeFile(stamp, String(Date.now()));
}

/** Called in the background by both agents. One reconciler owns the stores at a time. */
export async function opSessionUpdate(ctx: CliCtx): Promise<void> {
  const store = storeDir('global', ctx);
  await mkdir(store, { recursive: true });
  let release: () => Promise<void>;
  try { release = await lockfile.lock(store, { realpath: false, lockfilePath: join(store, '.session-update.lock') }); }
  catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ELOCKED') {
      console.log('another session is already checking the skills');
      return;
    }
    throw error;
  }
  try {
    const stamp = join(store, '.session-update.stamp');
    const last = Number(await readFile(stamp, 'utf8').catch(() => '0'));
    const errors: string[] = [];
    // Cached external repairs must not wait for the npm registry.
    try { await opSyncExternal(ctx); }
    catch (error) { errors.push(String(error)); }
    try { await updateOwnSkills(ctx, stamp, last); }
    catch (error) { errors.push(`own skill update: ${error}`); }
    if (errors.length) throw new Error(errors.join('\n'));
  } finally { await release(); }
}
