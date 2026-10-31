// Ticket tw/s5-smells/09: the Worst tests tab, loaded in jsdom from the file weigh.py wrote for a suite with 25 mock-heavy tests.
// Page contract on top of test-weigh-in-dashboard.test.ts: the Worst tests tab of a gauge holds one [data-testid=worst-test]
// per listed test, its text holding the node id and the reason (the name of the smell).
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { JSDOM } from 'jsdom';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

const root = join(import.meta.dirname, '..');
const skill = join(root, 'skills/test-weigh-in');
let sandbox: string;
let html: string;

beforeAll(() => {
  sandbox = mkdtempSync(join(tmpdir(), 'weigh-worst-'));
  const repo = join(sandbox, 'work/demo');
  const build = spawnSync(
    'python3',
    ['-c', 'import sys; sys.path.insert(0, sys.argv[1]); from fixture_repo import uv; from smell_repo import mock_heavy_suite; mock_heavy_suite(sys.argv[2]); print(uv())', join(skill, 'tests'), repo],
    { encoding: 'utf8' },
  );
  expect(build.status, build.stderr).toBe(0);
  const home = join(sandbox, 'home');
  const env = { ...process.env, HOME: home, TEST_WEIGH_IN_CACHE_DIR: join(sandbox, 'cache'), TEST_WEIGH_IN_UV: build.stdout.trim() };
  delete env.VIRTUAL_ENV;
  delete env.PYTHONPATH;
  const weigh = spawnSync('python3', [join(skill, 'weigh.py'), repo, '--no-open'], { env, encoding: 'utf8' });
  expect(weigh.status, weigh.stdout + weigh.stderr).toBe(0);
  html = readFileSync(join(home, '.test-weigh-in/github.com/jesdi/demo/dashboard.html'), 'utf8');
}, 600_000);

afterAll(() => rmSync(sandbox, { recursive: true, force: true }));

describe('Worst tests tab', () => {
  it('lists 10 of the 25 mock-heavy tests for Resistance to refactoring, each with its reason', async () => {
    const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true });
    await new Promise((resolve) => setTimeout(resolve, 100));
    const doc = dom.window.document;
    [...doc.querySelectorAll<HTMLElement>('[data-testid=gauge]')].find((g) => g.textContent?.includes('Resistance to refactoring'))!.click();
    [...doc.querySelectorAll<HTMLElement>('[data-testid=details] button')].find((b) => b.textContent?.trim() === 'Worst tests')!.click();
    const rows = [...doc.querySelectorAll('[data-testid=worst-test]')].map((r) => r.textContent ?? '');
    expect(rows).toHaveLength(10);
    for (const row of rows) {
      expect(row).toMatch(/tests\/test_heavy\.py::test_heavy_\d\d/);
      expect(row).toContain('Mock-heavy');
    }
  });
});
