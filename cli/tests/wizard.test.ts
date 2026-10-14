import { existsSync } from 'node:fs';
import { mkdtemp, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { opInstall } from '../src/ops.js';
import { runWizard } from '../src/wizard.js';
import { fixtureFetch } from './fixture-fetch.js';

const CANCEL = Symbol('cancel');
const calls: string[] = [];
const answers = {
  confirm: [] as unknown[],
  multiselect: [] as unknown[],
  select: [] as unknown[],
};

vi.mock('@clack/prompts', () => ({
  intro: () => {},
  outro: () => calls.push('outro'),
  cancel: () => calls.push('cancel'),
  isCancel: (value: unknown) => value === CANCEL,
  confirm: async () => answers.confirm.shift(),
  multiselect: async () => answers.multiselect.shift(),
  select: async () => answers.select.shift(),
  spinner: () => ({ start: () => calls.push('spinner'), stop: () => {} }),
}));

beforeEach(() => {
  calls.length = 0;
  for (const list of Object.values(answers)) list.length = 0;
});

async function makeCtx() {
  return {
    home: await mkdtemp(join(tmpdir(), 'home-')),
    project: await mkdtemp(join(tmpdir(), 'proj-')),
    fetchImpl: await fixtureFetch(),
    promptAutoUpdate: async () => {
      calls.push('autoUpdate');
      return true;
    },
  };
}

const globalState = async (ctx: { home: string }) =>
  JSON.parse(await readFile(join(ctx.home, '.config', 'my-skills', 'state.json'), 'utf8'));

describe('runWizard', () => {
  it('asks about auto-update before the install spinner on a first global install', async () => {
    const ctx = await makeCtx();
    answers.multiselect.push(['hello-world'], ['claude']);
    answers.select.push('global');
    await runWizard(ctx);
    expect(calls).toEqual(['autoUpdate', 'spinner', 'outro']);
    const state = await globalState(ctx);
    expect(state.agents).toEqual(['claude']);
    expect(state.autoUpdate).toBe(true);
    expect(state.skills['hello-world'].agents).toBeUndefined();
    expect(await readFile(join(ctx.home, '.claude', 'settings.json'), 'utf8')).toContain('.session-update.sh');
  });

  it('does not ask about auto-update for a project install', async () => {
    const ctx = await makeCtx();
    answers.multiselect.push(['hello-world'], ['opencode']);
    answers.select.push('local');
    await runWizard(ctx);
    expect(calls).toEqual(['spinner', 'outro']);
    expect(existsSync(join(ctx.project, '.agents', 'skills', 'hello-world', 'SKILL.md'))).toBe(true);
  });

  it('applies an accepted update, remembers a declined one, and keeps a different agent choice per skill', async () => {
    const ctx = { ...(await makeCtx()), promptAgents: async () => ['claude'] as const };
    await opInstall(['hello-world'], undefined, 'global', ctx);
    await opInstall(['hello-world'], ['claude'], 'local', ctx);
    ctx.fetchImpl = await fixtureFetch('1.0.1', '0.1.1');
    answers.confirm.push(true, false);
    answers.multiselect.push(['other'], ['opencode']);
    answers.select.push('global');
    await runWizard(ctx);
    const state = await globalState(ctx);
    expect(state.skills['hello-world'].version).toBe('0.1.1');
    expect(state.skills.other.agents).toEqual(['opencode']);
    const local = JSON.parse(await readFile(join(ctx.project, '.my-skills.json'), 'utf8'));
    expect(local.skills['hello-world'].version).toBe('0.1.0');
    expect(state.declined['hello-world']).toBe('0.1.1');
  });

  it.each([
    ['an update prompt', { confirm: [CANCEL] }],
    ['the skill picker', { multiselect: [CANCEL] }],
    ['the agent picker', { multiselect: [['hello-world'], CANCEL] }],
    ['the scope picker', { multiselect: [['hello-world'], ['claude']], select: [CANCEL] }],
  ] as [string, Partial<typeof answers>][])('stops without installing when %s is cancelled', async (_, given) => {
    const ctx = await makeCtx();
    if (given.confirm) {
      await opInstall(['hello-world'], ['claude'], 'global', ctx);
      ctx.fetchImpl = await fixtureFetch('1.0.1', '0.1.1');
      calls.length = 0;
    }
    for (const [key, list] of Object.entries(given)) answers[key as keyof typeof answers] = [...list];
    await runWizard(ctx);
    expect(calls).toEqual(['cancel']);
    expect(existsSync(join(ctx.home, '.agents', 'skills', 'other'))).toBe(false);
  });
});
