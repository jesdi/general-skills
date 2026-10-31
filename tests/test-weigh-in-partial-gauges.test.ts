// Ticket tw/s5-smells/10: the partial gauges and the 1-99 clamp, loaded in jsdom from the file weigh.py wrote.
// Page contract on top of test-weigh-in-dashboard.test.ts: each [data-testid=gauge] holds its displayed score in
// an element of class "score" and says "partial" in its text when the pillar is partial.
// Snapshot contract: pillars {<key>: {score, partial}} with unrounded scores; metrics {<id>: {value, score}}.
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { JSDOM } from 'jsdom';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

const root = join(import.meta.dirname, '..');
const skill = join(root, 'skills/test-weigh-in');
const KEY = '.test-weigh-in/github.com/jesdi/demo';
const PILLARS = ['Protection against regressions', 'Resistance to refactoring', 'Fast feedback', 'Maintainability', 'Economy'];
let sandbox: string;
const runs: Record<string, { html: string; snap: any }> = {};

function weighSuite(kind: 'perfect_suite' | 'clone_suite') {
  const repo = join(sandbox, kind, 'work/demo');
  const build = spawnSync(
    'python3',
    ['-c', `import sys; sys.path.insert(0, sys.argv[1]); from fixture_repo import uv; from maint_repo import ${kind}; ${kind}(sys.argv[2]); print(uv())`, join(skill, 'tests'), repo],
    { encoding: 'utf8' },
  );
  expect(build.status, build.stderr).toBe(0);
  const home = join(sandbox, kind, 'home');
  const env = { ...process.env, HOME: home, TEST_WEIGH_IN_CACHE_DIR: join(sandbox, 'cache'), TEST_WEIGH_IN_UV: build.stdout.trim() };
  delete env.VIRTUAL_ENV;
  delete env.PYTHONPATH;
  const weigh = spawnSync('python3', [join(skill, 'weigh.py'), repo, '--no-open'], { env, encoding: 'utf8' });
  expect(weigh.status, weigh.stdout + weigh.stderr).toBe(0);
  const dir = join(home, KEY);
  const snapName = readdirSync(dir).filter((f) => f.endsWith('.json')).sort().pop()!;
  return { html: readFileSync(join(dir, 'dashboard.html'), 'utf8'), snap: JSON.parse(readFileSync(join(dir, snapName), 'utf8')) };
}

beforeAll(() => {
  sandbox = mkdtempSync(join(tmpdir(), 'weigh-partial-'));
  runs.perfect = weighSuite('perfect_suite');
  runs.clone = weighSuite('clone_suite');
}, 600_000);

afterAll(() => rmSync(sandbox, { recursive: true, force: true }));

const gauges = async (html: string) => {
  const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true });
  await new Promise((resolve) => setTimeout(resolve, 100));
  return [...dom.window.document.querySelectorAll('[data-testid=gauge]')].map((g) => ({
    text: g.textContent ?? '',
    score: Number(g.querySelector('.score')?.textContent),
  }));
};

describe('dashboard.html gauges', () => {
  it('shows no gauge at 100 for a repo with 0% hollow tests, 100% branch coverage and 0% duplicate clusters', async () => {
    const { snap, html } = runs.perfect;
    expect(snap.metrics.hollow_tests.value).toBe(0);
    expect(snap.metrics.branch_coverage.value).toBe(100);
    expect(snap.metrics.duplicate_clusters.value).toBe(0);
    const shown = await gauges(html);
    expect(shown).toHaveLength(5);
    for (const g of shown) {
      expect(g.score).toBeGreaterThanOrEqual(1);
      expect(g.score).toBeLessThanOrEqual(99);
    }
  });

  it('shows 1 for a pillar computed below 0.5', async () => {
    const { snap, html } = runs.clone;
    expect(snap.metrics.duplicate_clusters.value).toBeGreaterThan(90);
    expect(snap.pillars.economy.score).toBeLessThan(0.5);
    const shown = await gauges(html);
    expect(shown[4].score).toBe(1);
  });

  it('marks Regression and Economy partial, scored with their partial weights', async () => {
    const { snap, html } = runs.perfect;
    const shown = await gauges(html);
    PILLARS.forEach((name, i) => expect(shown[i].text).toContain(name));
    expect(shown[0].text).toMatch(/partial/i);
    expect(shown[4].text).toMatch(/partial/i);
    expect(shown[2].text).not.toMatch(/partial/i);
    expect(shown[3].text).not.toMatch(/partial/i);
    const m = snap.metrics;
    const economy = 0.6 * m.no_unique_coverage.score + 0.4 * m.duplicate_clusters.score;
    expect(snap.pillars.economy.score).toBeCloseTo(economy, 6);
    expect(shown[4].score).toBe(Math.min(99, Math.max(1, Math.round(economy))));
  });
});
