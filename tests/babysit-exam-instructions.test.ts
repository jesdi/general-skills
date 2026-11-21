import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

// Public seam: the instructions a session reads, not babysit.py internals.
// Each scenario starts with the repository's actual published SKILL.md.
const file = join(__dirname, '..', 'skills', 'babysit-pr', 'SKILL.md');
const read = async () => (await readFile(file, 'utf8')).toLowerCase();
const section = (text: string, heading: string) => {
  const match = text.match(new RegExp(`^## ${heading}[^\\n]*\\n([\\s\\S]*?)(?=^## |$(?![\\s\\S]))`, 'm'));
  expect(match, `document has ${heading} section`).not.toBeNull();
  return match![1];
};
// Ignore Markdown wrapping; check directives within a paragraph/list item.
const paragraphs = (text: string) => text.split(/\n\s*\n/).map((p) => p.replace(/\s+/g, ' '));
const requires = (text: string, patterns: RegExp[]) => patterns.every((pattern) => pattern.test(text));

describe('babysit-pr SKILL.md exam step', () => {
  it('places exam after green and before gate, judge and merge, on every approval path', async () => {
    const text = await read();
    const loop = section(text, 'the loop');
    const steps = loop.split(/(?=^\d+\. )/m);
    const greenIndex = steps.findIndex((step) => /^\d+\. green:/i.test(step));
    const greenAndFollowing = steps.slice(greenIndex).join('\n');
    const exam = greenAndFollowing.search(/\bexam\b/);
    const ordered = greenIndex >= 0 && exam >= 0 && ['gate', 'judge', 'merge'].every(
      (action) => exam < greenAndFollowing.search(new RegExp(`\\b${action}\\b`)),
    );
    const allPaths = paragraphs(text).some((p) => requires(p, [
      /\bexam\b/, /every (approval|merge) path|all (approval|merge) paths|regardless of.*approval/,
      /session/, /owner/,
    ]));
    const notApproval = paragraphs(text).some((p) => /pass.*(?:not|never).*merge approval/.test(p));
    expect(ordered && allPaths && notApproval, 'exam must precede every merge approval path without granting approval').toBe(true);
  });

  it('shows the operator the exam link and waits indefinitely in the Claude Code background', async () => {
    const text = await read();
    const waiting = paragraphs(text).some((p) => requires(p, [
      /exam --wait/, /claude code/, /background/,
      /no (?:time )?limit|without (?:a )?(?:time )?limit|never time out/,
      /(?:never|not).*ci stalled/,
    ]));
    const link = paragraphs(text).some((p) => requires(p, [/exam link/, /show|print|display/, /operator/]));
    expect(waiting && link, 'exam wait must show its link and run without the CI stall timeout').toBe(true);
  });

  it('returns to waiting without handoff when merge refuses with exam not passed', async () => {
    const recovery = paragraphs(await read()).some((p) => requires(p, [
      /exam not passed/, /\bmerge\b/, /(?:back|return|resume|continue|keep|go).*wait/,
      /(?:no|not|never).*hand\s?off/,
    ]));
    expect(recovery, 'exam not passed must return merge to waiting without handoff').toBe(true);
  });

  it('requires exam again before merging after either a push or a rebase following a pass', async () => {
    const recheck = paragraphs(await read()).some((p) => requires(p, [
      /push/, /rebase/, /after.*pass|pass.*(?:push|rebase)/,
      /exam.*(?:again|re-?run)|(?:again|re-?run).*exam/, /before.*merg/,
    ]));
    expect(recheck, 'push and rebase after a pass must rerun exam before merging').toBe(true);
  });

  it('forbids all agent bypass-label mutations and exam status posts and accepts only owner labels', async () => {
    const text = await read();
    const labels = paragraphs(text).some((p) => requires(p, [
      /agent/, /never/, /appl(?:y|ies)|add/, /remov/, /re-?appl/, /bypass label/,
    ]));
    const status = paragraphs(text).some((p) => /never post.*`?exam`? status/.test(p));
    const owner = paragraphs(text).some((p) => /only.*(?:repo owner|operator).*label.*count|only.*(?:repo owner|operator).*label.*(?:accepted|valid)|only.*label.*(?:repo owner|operator).*count/.test(p));
    expect(labels && status && owner, 'agents must never mutate bypass labels or post exam status; only owner labels count').toBe(true);
  });

  it('lists PR_EXAM_HOST and the pr-exam install alongside pr-risk before anything', async () => {
    const before = section(await read(), 'before anything');
    const prerequisites = requires(before, [/pr_exam_host/, /pr-risk/, /pr-exam/]) &&
      paragraphs(before).some((p) => requires(p, [/pr-exam/, /install/]));
    expect(prerequisites, 'before anything must require PR_EXAM_HOST and installing pr-exam beside pr-risk').toBe(true);
  });
});
