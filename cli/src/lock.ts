import { mkdir, rm } from 'node:fs/promises';
import { dirname } from 'node:path';

/**
 * Run `fn` holding the lock directory `lock`, so two CLI runs never interleave
 * their read-modify-write of the agent configs and the state file.
 * ponytail: mkdir lock polled every 50ms for 30s; a run killed mid-way leaves
 * the directory behind — delete it by hand, or add staleness by mtime.
 */
export async function withLock<T>(lock: string, fn: () => Promise<T>): Promise<T> {
  await mkdir(dirname(lock), { recursive: true });
  for (let waited = 0; ; waited += 50) {
    try {
      await mkdir(lock);
      break;
    } catch (e) {
      if ((e as NodeJS.ErrnoException).code !== 'EEXIST' || waited >= 30_000) throw e;
      await new Promise((r) => setTimeout(r, 50));
    }
  }
  try {
    return await fn();
  } finally {
    await rm(lock, { recursive: true, force: true });
  }
}
