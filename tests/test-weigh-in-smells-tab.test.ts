// The Smells tab, loaded in jsdom from the file weigh.py wrote for the smelly suite (skills/test-weigh-in/tests/smell_repo.py).
// Page contract: the Smells tab of a gauge holds one [data-testid=smell] heading per smell of the pillar, with its test
// count, followed by one [data-testid=smell-test] per test that has it.
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
  sandbox = mkdtempSync(join(tmpdir(), 'weigh-smells-'));
  const repo = join(sandbox, 'work/demo');
  const build = spawnSync(
    'python3',
    ['-c', 'import sys; sys.path.insert(0, sys.argv[1]); from fixture_repo import uv; from smell_repo import smelly_suite; smelly_suite(sys.argv[2]); print(uv())', join(skill, 'tests'), repo],
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

async function smellsTab(pillar: string) {
  const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true });
  await new Promise((resolve) => setTimeout(resolve, 100));
  const doc = dom.window.document;
  [...doc.querySelectorAll<HTMLElement>('[data-testid=gauge]')].find((g) => g.textContent?.includes(pillar))!.click();
  [...doc.querySelectorAll<HTMLElement>('[data-testid=details] button')].find((b) => b.textContent?.trim() === 'Smells')!.click();
  return [...doc.querySelectorAll('[data-testid=smell], [data-testid=smell-test]')].map((e) => e.textContent ?? '');
}

describe('Smells tab', () => {
  it('lists each smell of Resistance to refactoring with the tests that have it', async () => {
    expect(await smellsTab('Resistance to refactoring')).toEqual([
      'Interaction-only (1 test)',
      'tests/test_smells.py::test_notify',
      'Mock-heavy (1 test)',
      'tests/test_smells.py::test_b_heavy',
      'Private access (1 test)',
      'tests/test_smells.py::test_b_private',
    ]);
  });

  it('lists the 4 hollow tests under No assertion for Protection against regressions', async () => {
    const rows = await smellsTab('Protection against regressions');
    expect(rows[0]).toBe('No assertion (4 tests)');
    expect(rows.slice(1)).toEqual([
      'tests/test_smells.py::test_board_renders',
      'tests/test_smells.py::test_error_hollow',
      'tests/test_smells.py::test_fail_hollow',
      'tests/test_smells.py::test_skipped_hollow',
    ]);
  });
});
