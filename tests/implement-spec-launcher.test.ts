// Ticket 07: the implement-spec skill is a launcher, its method lives in the README.
// The flag list of `run.sh --help` is checked against the README in
// skills/implement-spec/test_models.py (derived from the help at test time); the ticket's
// eleven flags are not repeated here.
import { execFileSync } from 'node:child_process';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { describe, expect, it } from 'vitest';

const root = join(import.meta.dirname, '..');
const skill = join(root, 'skills/implement-spec');
const read = (...p: string[]) => readFileSync(join(root, ...p), 'utf8');
const readme = () => read('skills/implement-spec/README.md');
const frontmatter = (text: string) => text.match(/^---\n([\s\S]*?)\n---\n/)![1];
const body = (text: string) => text.replace(/^---\n[\s\S]*?\n---\n/, '').replace(/<!--[\s\S]*?-->/, '');

describe('implement-spec SKILL.md as a launcher', () => {
  const text = read('skills/implement-spec/SKILL.md');

  it('keeps the name and disable-model-invocation: true', () => {
    expect(frontmatter(text)).toMatch(/^name: implement-spec$/m);
    expect(frontmatter(text)).toMatch(/^disable-model-invocation: true$/m);
  });

  it('keeps the fork header: upstream commit and a Changes line', () => {
    const header = text.match(/<!--([\s\S]*?)-->/)![1];
    expect(header).toContain('5b15a47f2d7150f545fbcacbfe381787fc0230dc');
    expect(header).toMatch(/^Changes:/m);
  });

  it('runs run.sh with the operator flags and relays summary.md', () => {
    expect(body(text)).toMatch(/sh [^\n]*run\.sh/);
    expect(body(text)).toContain('summary.md');
    expect(body(text)).toMatch(/flags/i);
  });

  it('asks the session to decide, dispatch and merge nothing', () => {
    const old = [/dispatch/i, /merger/i, /subagent/i, /fix loop/i, /rule, don't stall/i, /ledger/i, /frontier/i];
    for (const word of old) expect(body(text)).not.toMatch(word);
  });
});

describe('implement-spec README.md', () => {
  it('documents the exit codes 0, 1, 2 and 3 with their meanings', () => {
    const meaning: Record<string, RegExp> = {
      '0': /done|success|complete|passed|nothing to do/i,
      '1': /blocked/i,
      '2': /park|question|stop|awaiting/i,
      '3': /invalid|precondition|refus|error/i,
    };
    const lines = readme().split('\n');
    for (const [code, word] of Object.entries(meaning)) {
      const hit = lines.some((l) => new RegExp(`(^|[^\\w.-])${code}([^\\w.-]|$)`).test(l) && word.test(l));
      expect(hit, `exit code ${code}`).toBe(true);
    }
  });

  it('documents the layout under .agent/implement/', () => {
    const text = readme();
    expect(text).toContain('.agent/implement/');
    for (const name of ['state.json', 'ledger.md', 'summary.md', 'decisions.json', 'final/'])
      expect(text, name).toContain(name);
    expect(text).toMatch(/per-ticket|<NN>|\bNN\b/);
  });

  it('has a report schema section naming each reporting role', () => {
    const text = readme();
    expect(text).toMatch(/^#+ .*schema/im);
    const section = text.slice(text.search(/^#+ .*schema/im));
    for (const role of ['test-writer', 'implementer', 'reviewer', 'fixer', 'final-reviewer', 'conflict'])
      expect(section, role).toContain(role);
  });

  it('names the question kinds of the driver', () => {
    const text = readme();
    const start = text.search(/^## Question kinds$/m);
    expect(start, 'a "Question kinds" section').toBeGreaterThanOrEqual(0);
    const rest = text.slice(start + 1);
    const end = rest.search(/^## /m);
    const section = text.slice(start, end < 0 ? undefined : start + 1 + end);
    const kinds: Record<string, string[]> = {
      'spec-ambiguous': ['keep-test', 'correct-test', 'edit-spec'],
      'ticket-blocked': ['retry', 'edit'],
    };
    const rows = section.split('\n').filter((l) => /^\| `[a-z-]+` \|/.test(l));
    expect(rows.map((l) => l.split('`')[1])).toEqual(Object.keys(kinds));
    for (const [kind, options] of Object.entries(kinds)) {
      const row = rows.find((l) => l.startsWith(`| \`${kind}\``));
      for (const option of options) expect(row, `${kind}: ${option}`).toContain(`\`${option}\``);
    }
    expect(section).toMatch(/`final-finding`.*`ticket-blocked` question of the end of run.*`final-ticket-blocked-<n>`/);
  });

  it('lists exactly the four non-negotiable checks, each with a pointer to a file', () => {
    const text = readme();
    const start = text.search(/^## The non-negotiable checks$/m);
    expect(start, 'a "The non-negotiable checks" section').toBeGreaterThanOrEqual(0);
    const rest = text.slice(start + 1);
    const section = rest.slice(0, rest.search(/^## /m));
    const rows = section.split('\n').filter((l) => l.startsWith('| ') && !l.startsWith('| Check |'));
    const pointer = /`[\w./-]*\.(py|md|sh|json)`/;
    const checks = [/red plus green/i, /changed only by the test-writer/i, /full gate at landing and at the end of run/i,
      /ticket reviewer and the final correctness lens.*another provider/i];
    expect(rows).toHaveLength(checks.length);
    checks.forEach((check, index) => {
      expect(rows[index], String(check)).toMatch(check);
      expect(rows[index], String(check)).toMatch(pointer);
    });
  });
});

describe('implement-spec documents', () => {
  it('link to no missing file, and the launcher points at the README', () => {
    const docs = readdirSync(skill, { recursive: true, encoding: 'utf8' }).filter((f) => f.endsWith('.md'));
    expect(docs).toContain('README.md');
    expect(read('skills/implement-spec/SKILL.md')).toContain('README.md');
    for (const doc of docs) {
      const links = [...read('skills/implement-spec', doc).matchAll(/\]\(([^)\s#]+)(?:#[^)]*)?\)/g)].map((m) => m[1]);
      for (const link of links.filter((l) => !/^[a-z]+:/i.test(l)))
        expect(existsSync(join(skill, dirname(doc), link)), `${doc} -> ${link}`).toBe(true);
    }
  });
});

describe('to-tickets template', () => {
  const text = read('skills/to-tickets/SKILL.md');
  const template = text.match(/<ticket-template>([\s\S]*?)<\/ticket-template>/)![1];

  it('shows optional Risk and Suspicions lines', () => {
    expect(template).toMatch(/^\*\*Risk:\*\*/m);
    expect(template).toMatch(/^\*\*Suspicions:\*\*/m);
    expect(template).toMatch(/optional/i);
  });

  it('says in the rules what each is for', () => {
    const rules = text.slice(text.indexOf('</ticket-template>'));
    expect(rules).toMatch(/\*\*Risk\*\*/);
    expect(rules).toMatch(/\*\*Suspicions\*\*/);
    expect(rules).toMatch(/reviewer checks it as one more criterion/i);
  });
});

describe('skills-manifest.json', () => {
  const git = (...a: string[]) => execFileSync('git', a, { cwd: root, encoding: 'utf8' }).trim();
  let base = '';
  try {
    base = git('merge-base', 'HEAD', 'origin/main');
  } catch {
    // origin/main is absent: the test below is skipped
  }

  it.skipIf(!base)('is unchanged against the merge-base with origin/main (skipped: no origin/main)', () => {
    expect(git('diff', '--name-only', base, '--', 'skills-manifest.json')).toBe('');
  });
});
