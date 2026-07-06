import { describe, expect, it } from 'vitest';
import {
  agentSkillsDir,
  cacheDir,
  globalStateFile,
  projectStateFile,
  storeDir,
} from '../src/paths.js';

const ctx = { home: '/home/u', project: '/proj' };

describe('paths', () => {
  it('resolves agent skills dirs per scope', () => {
    expect(agentSkillsDir('claude', 'global', ctx)).toBe('/home/u/.claude/skills');
    expect(agentSkillsDir('claude', 'local', ctx)).toBe('/proj/.claude/skills');
    expect(agentSkillsDir('opencode', 'global', ctx)).toBe('/home/u/.agents/skills');
    expect(agentSkillsDir('opencode', 'local', ctx)).toBe('/proj/.agents/skills');
  });

  it('resolves store, state and cache locations', () => {
    expect(storeDir('global', ctx)).toBe('/home/u/.my-skills');
    expect(storeDir('local', ctx)).toBe('/proj/.my-skills');
    expect(globalStateFile(ctx)).toBe('/home/u/.config/my-skills/state.json');
    expect(projectStateFile(ctx)).toBe('/proj/.my-skills.json');
    expect(cacheDir(ctx)).toBe('/home/u/.cache/my-skills');
  });
});
