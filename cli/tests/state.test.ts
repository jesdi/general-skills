import { mkdtemp, mkdir, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  agentsFor,
  loadGlobalState,
  loadState,
  saveGlobalState,
  saveState,
  type ProjectState,
} from '../src/state.js';

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

  it('round-trips a top-level agents default with an inheriting entry', async () => {
    const ctx = await makeCtx();
    const state: ProjectState = {
      schemaVersion: 1,
      agents: ['claude', 'opencode'],
      skills: { x: { version: '0.1.0' } },
    };
    await saveState('local', ctx, state);
    expect(await loadState('local', ctx)).toEqual(state);
  });

  it('throws a clear error naming the file when the state file is malformed', async () => {
    const ctx = await makeCtx();
    const file = join(ctx.project, '.my-skills.json');
    await writeFile(file, '{ not json');
    await expect(loadState('local', ctx)).rejects.toThrow(file);

    const globalFile = join(ctx.home, '.config', 'my-skills', 'state.json');
    await mkdir(join(ctx.home, '.config', 'my-skills'), { recursive: true });
    await writeFile(globalFile, '');
    await expect(loadState('global', ctx)).rejects.toThrow(globalFile);
  });
});

describe('agentsFor', () => {
  const base = (over: Partial<ProjectState>): ProjectState => ({
    schemaVersion: 1,
    skills: {},
    ...over,
  });

  it('prefers the per-skill override over the top-level default', () => {
    const state = base({
      agents: ['claude', 'opencode'],
      skills: { x: { version: '1.0.0', agents: ['opencode'] } },
    });
    expect(agentsFor(state, 'x')).toEqual(['opencode']);
  });

  it('falls back to the top-level default when the entry has no agents', () => {
    const state = base({ agents: ['claude', 'opencode'], skills: { x: { version: '1.0.0' } } });
    expect(agentsFor(state, 'x')).toEqual(['claude', 'opencode']);
  });

  it('errors with a friendly message when neither is configured', () => {
    const state = base({ skills: { x: { version: '1.0.0' } } });
    expect(() => agentsFor(state, 'x')).toThrow(/no agents configured for x/);
    expect(() => agentsFor(state, 'x')).toThrow(/--agent/);
  });

  it('errors on an unknown agent id, wherever it comes from', () => {
    const top = base({ agents: ['cursor' as never], skills: { x: { version: '1.0.0' } } });
    expect(() => agentsFor(top, 'x')).toThrow(/unknown agent: cursor \(expected claude\|opencode\)/);
    const entry = base({ skills: { x: { version: '1.0.0', agents: ['cursor' as never] } } });
    expect(() => agentsFor(entry, 'x')).toThrow(/unknown agent: cursor/);
  });

  it('errors when the top-level default is empty', () => {
    const state = base({ agents: [], skills: { x: { version: '1.0.0' } } });
    expect(() => agentsFor(state, 'x')).toThrow(/no agents configured for x/);
  });
});
