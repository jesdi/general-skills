import { describe, expect, it } from 'vitest';
import { buildProgram } from '../src/program.js';

describe('buildProgram', () => {
  it('is named skills-cli and reports a semver version', () => {
    const program = buildProgram();
    expect(program.name()).toBe('skills-cli');
    expect(program.version()).toMatch(/^\d+\.\d+\.\d+$/);
  });
});
