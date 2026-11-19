import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { hashSkillDir } from '../lib/manifest.js';
import { listSkills } from '../lib/skills.js';

// Seam: skills/pr-exam/SKILL.md as a session reads it. Spec specs/pr-exam, ticket 06.
const SKILLS = join(__dirname, '..', 'skills');
const DIR = join(SKILLS, 'pr-exam');
const read = async () => (await readFile(join(DIR, 'SKILL.md'), 'utf8')).toLowerCase();
const has = (text: string, ...patterns: RegExp[]) =>
  patterns.forEach((p) => expect(text, String(p)).toMatch(p));

describe('pr-exam SKILL.md', () => {
  it('has valid frontmatter, a section per role, and a manifest entry generated from its content', async () => {
    const skill = (await listSkills(SKILLS)).find((s) => s.name === 'pr-exam');
    expect(skill?.description).toMatch(/generat/i);
    expect(skill?.description).toMatch(/grad/i);
    const text = await read();
    has(text, /^#+ .*generat/m, /^#+ .*grad/m);
    const manifest = JSON.parse(await readFile(join(SKILLS, '..', 'skills-manifest.json'), 'utf8'));
    const entry = manifest.skills.find((s: { name: string }) => s.name === 'pr-exam');
    expect(entry?.hash).toBe(await hashSkillDir(DIR));
  });

  it('states the generation rules', async () => {
    const t = await read();
    has(
      t,
      /why[\s\S]*concept[\s\S]*architecture[\s\S]*risk[\s\S]*behaviou?r/,
      /merge-risk/,
      /trivia/,
      /one question (per|for each|for every) concept/,
      /\b8\b/,
      /trivial[^\n]*\b1 question|\b1 question[^\n]*trivial|one question[^\n]*trivial|trivial[^\n]*one question/,
      /multiple.choice[\s\S]*free.text|free.text[\s\S]*multiple.choice|choice[\s\S]*\btext\b/,
      /same (word count|number of words)/,
      /glossary\.md/,
      /pr_exam\.py learned/,
      /rubric[^\n]*before|before[^\n]*(answer|rubric)/,
      /pr_exam\.py validate/,
      /every error/,
    );
  });

  it('describes the followup and delta modes', async () => {
    const t = await read();
    has(
      t,
      /followup/,
      /only_concepts/,
      /one (new )?question (per|for each) concept in `?only_concepts/,
      /delta/,
      /prior_concepts/,
      /absent from `?prior_concepts|not in `?prior_concepts/,
      /empty exam|no questions|zero questions|0 questions/,
    );
  });

  it('states the grading rules', async () => {
    const t = await read();
    has(
      t,
      /diff[\s\S]*question[\s\S]*rubric[\s\S]*answer/,
      /nothing (else )?(else )?from generation|nothing from the generat/,
      /`pass`[\s\S]*`partial`[\s\S]*`fail`/,
      /one line of feedback|one-line feedback|feedback[^\n]*one line/,
      /grades\.json/,
      /pr_exam\.py score/,
      /`choice`[^\n]*(not|never)[^\n]*grade|(not|never)[^\n]*grade[^\n]*`choice`/,
    );
  });

  it('forbids applying a bypass label and posting an exam status', async () => {
    const t = await read();
    has(
      t,
      /never apply[^\n]*bypass label|never[^\n]*bypass label/,
      /never post[^\n]*`?exam`? status|never[^\n]*`?exam`? status/,
    );
  });
});
