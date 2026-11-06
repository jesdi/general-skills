// Ticket tw/s4-coverage/07: the dashboard of a suite that is not green, loaded in jsdom from the file weigh.py wrote.
// Page contract on top of test-weigh-in-dashboard.test.ts: [data-testid=banner] names the number of failing tests;
// the details of some gauge or tab list each skipped test by node id with the word "skipped"; each partial
// [data-testid=gauge-status] and the Metrics tab of its pillar say why it is partial.
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { JSDOM } from 'jsdom';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

const root = join(import.meta.dirname, '..');
const skill = join(root, 'skills/test-weigh-in');
const SKIPPED = ['test_config_2', 'test_config_3', 'test_config_4', 'test_render_2'];
let sandbox: string;
const pages: Record<string, string> = {};

function weighSuite(kind: 'red_suite' | 'skipped_suite'): string {
  const repo = join(sandbox, kind, 'work/demo');
  const build = spawnSync(
    'python3',
    ['-c', `import sys; sys.path.insert(0, sys.argv[1]); from fixture_repo import uv; from red_repo import ${kind}; ${kind}(sys.argv[2]); print(uv())`, join(skill, 'tests'), repo],
    { encoding: 'utf8' },
  );
  expect(build.status, build.stderr).toBe(0);
  const home = join(sandbox, kind, 'home');
  const env = { ...process.env, HOME: home, TEST_WEIGH_IN_CACHE_DIR: join(sandbox, 'cache'), TEST_WEIGH_IN_UV: build.stdout.trim() };
  delete env.VIRTUAL_ENV;
  delete env.PYTHONPATH;
  const weigh = spawnSync('python3', [join(skill, 'weigh.py'), repo, '--no-open'], { env, encoding: 'utf8' });
  expect(weigh.status, weigh.stdout + weigh.stderr).toBe(0);
  return readFileSync(join(home, '.test-weigh-in/github.com/jesdi/demo/dashboard.html'), 'utf8');
}

beforeAll(() => {
  sandbox = mkdtempSync(join(tmpdir(), 'weigh-red-dashboard-'));
  pages.red_suite = weighSuite('red_suite');
  pages.skipped_suite = weighSuite('skipped_suite');
}, 600_000);

afterAll(() => rmSync(sandbox, { recursive: true, force: true }));

const open = async (html: string) => {
  const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true });
  await new Promise((resolve) => setTimeout(resolve, 100));
  return dom.window.document;
};

describe('dashboard.html of a suite that is not green', () => {
  it('shows a banner naming the 3 failing tests', async () => {
    const doc = await open(pages.red_suite);
    expect(doc.querySelector('[data-testid=banner]')?.textContent).toMatch(/3 tests are failing/);
  });

  it('shows no banner for a suite with only skipped tests', async () => {
    const doc = await open(pages.skipped_suite);
    expect(doc.querySelector('[data-testid=banner]')).toBeNull();
  });

  it('lists the 4 skipped tests in the details', async () => {
    const doc = await open(pages.skipped_suite);
    let text = '';
    for (const gauge of [...doc.querySelectorAll<HTMLElement>('[data-testid=gauge]')]) {
      gauge.click();
      for (const tab of [...doc.querySelectorAll<HTMLElement>('[data-testid=details] button')]) {
        tab.click();
        text += doc.querySelector('[data-testid=details]')?.textContent ?? '';
      }
      text += doc.querySelector('[data-testid=details]')?.textContent ?? '';
    }
    expect(text).toMatch(/skipped/i);
    for (const name of SKIPPED) expect(text).toContain(name);
  });

  // Regression, Refactoring and Economy use a value metric, so a suite that is not green leaves them partial (req 14)
  const statuses = (doc: Document) =>
    [...doc.querySelectorAll('[data-testid=gauge-status]')].map((s) => s.textContent ?? '');

  it.each([
    ['red_suite', '3 tests failing'],
    ['skipped_suite', '4 tests skipped'],
  ])('says on each value pillar of the %s that it is partial because %s', async (kind, reason) => {
    const doc = await open(pages[kind]);
    const shown = statuses(doc);
    for (const i of [0, 1, 4]) expect(shown[i]).toMatch(new RegExp(`^partial: .*${reason}$`));
    for (const i of [2, 3]) expect(shown[i]).not.toMatch(/partial|failing|skipped/);
    // a count of measured metrics alone never explains a pillar whose metrics were all measured
    for (const s of shown) expect(s).not.toMatch(/partial: (\d+) of \1 metrics measured$/);
  });

  it('names both reasons when a pillar is missing metrics in a red suite', async () => {
    const regression = statuses(await open(pages.red_suite))[0];
    expect(regression).toMatch(/^partial: \d of 4 measured; 3 tests failing$/);
  });

  it('explains in the Metrics tab of a value pillar that the failing tests are left out', async () => {
    const doc = await open(pages.red_suite);
    doc.querySelectorAll<HTMLElement>('[data-testid=gauge]')[1].click();
    expect(doc.querySelector('[data-testid=partial-reason]')?.textContent).toMatch(/3 tests failing/);
    doc.querySelectorAll<HTMLElement>('[data-testid=gauge]')[2].click();
    expect(doc.querySelector('[data-testid=partial-reason]')).toBeNull();
  });
});
