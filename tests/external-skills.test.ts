import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const root = new URL('..', import.meta.url).pathname;
const file = JSON.parse(readFileSync(join(root, 'external-skills.json'), 'utf8'));
const FORKS = ['implement-spec', 'prototype', 'to-spec', 'to-tickets', 'wizard'];
const OPERATOR_ONLY_FORKS = ['implement-spec'];

describe('external-skills.json', () => {
  it('declares exactly the five forks as vendored copies that must not be installed from upstream', () => {
    expect(file.schemaVersion).toBe(2);
    expect([...file.forks.skills].sort()).toEqual(FORKS);
    expect(file.forks.source).toBe('mattpocock/skills');
    expect(file.forks.doNotInstallUpstream).toBe(true);
  });

  it('never lists a fork among the upstream-installed skills', () => {
    const external = file.skills.map((s: { name: string }) => s.name);
    for (const fork of FORKS) expect(external).not.toContain(fork);
  });

  it('pins every upstream a box skill comes from to a commit', () => {
    const source = new Map<string, string>(
      file.skills.map((s: { name: string; source: string }) => [s.name, s.source]),
    );
    for (const name of file.sets.box.external) {
      const src = source.get(name);
      expect(src, `${name} must be listed in skills[]`).toBeDefined();
      expect(file.pins[src as string]?.ref, `${src} must be pinned`).toMatch(/^[0-9a-f]{40}$/);
    }
  });

  it('box own set is the box forks plus deep-quality-review, all present in skills/', () => {
    const boxForks = FORKS.filter((f) => !OPERATOR_ONLY_FORKS.includes(f));
    expect([...file.sets.box.own].sort()).toEqual([...boxForks, 'deep-quality-review'].sort());
    for (const name of file.sets.box.own) {
      expect(existsSync(join(root, 'skills', name, 'SKILL.md')), name).toBe(true);
    }
  });
});
