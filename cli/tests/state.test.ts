import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { loadGlobalState, loadState, saveGlobalState, saveState } from '../src/state.js';

async function makeCtx() {
  return {
    home: await mkdtemp(join(tmpdir(), 'home-')),
    project: await mkdtemp(join(tmpdir(), 'proj-')),
  };
}

describe('state', () => {
  it('returns an empty default when no file exists', async () => {
    const ctx = await makeCtx();
    expect(await loadState('global', ctx)).toEqual({
      schemaVersion: 1,
      skills: {},
      declined: {},
    });
    expect(await loadState('local', ctx)).toEqual({ schemaVersion: 1, skills: {} });
  });

  it('round-trips global state including declines', async () => {
    const ctx = await makeCtx();
    const state = await loadGlobalState(ctx);
    state.skills['hello-world'] = { version: '0.1.0', agents: ['claude'] };
    state.declined['hello-world'] = '0.1.1';
    await saveGlobalState(ctx, state);
    expect(await loadGlobalState(ctx)).toEqual(state);
  });

  it('round-trips project state', async () => {
    const ctx = await makeCtx();
    const state = { schemaVersion: 1 as const, skills: { x: { version: '0.1.0', agents: ['opencode' as const] } } };
    await saveState('local', ctx, state);
    expect(await loadState('local', ctx)).toEqual(state);
  });
});
