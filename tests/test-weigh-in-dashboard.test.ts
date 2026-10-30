// Ticket tw/s3-skeleton/06: the dashboard page, loaded in jsdom from the file weigh.py wrote.
// Page contract: [data-testid=inventory], five [data-testid=gauge] (each with its pillar name and a
// score), and [data-testid=details] holding the selected gauge's pillar name and a Metrics tab.
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { JSDOM } from 'jsdom';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

const root = join(import.meta.dirname, '..');
const skill = join(root, 'skills/test-weigh-in');
const PILLARS = ['Protection against regressions', 'Resistance to refactoring', 'Fast feedback', 'Maintainability', 'Economy'];
let sandbox: string;
let html: string;

beforeAll(() => {
  sandbox = mkdtempSync(join(tmpdir(), 'weigh-dashboard-'));
  const repo = join(sandbox, 'work/demo');
  const build = spawnSync(
    'python3',
    ['-c', 'import sys; sys.path.insert(0, sys.argv[1]); from fixture_repo import build_repo; build_repo(sys.argv[2])', join(skill, 'tests'), repo],
    { encoding: 'utf8' },
  );
  expect(build.status, build.stderr).toBe(0);
  const home = join(sandbox, 'home');
  const env = { ...process.env, HOME: home, TEST_WEIGH_IN_CACHE_DIR: join(sandbox, 'cache') };
  delete env.VIRTUAL_ENV;
  delete env.PYTHONPATH;
  const weigh = spawnSync('python3', [join(skill, 'weigh.py'), repo, '--no-open'], { env, encoding: 'utf8' });
  expect(weigh.status, weigh.stdout + weigh.stderr).toBe(0);
  html = readFileSync(join(home, '.test-weigh-in/github.com/jesdi/demo/dashboard.html'), 'utf8');
}, 300_000);

afterAll(() => rmSync(sandbox, { recursive: true, force: true }));

describe('dashboard.html offline', () => {
  it('renders the inventory line, five gauges and the details, and makes no network request', async () => {
    const requests: string[] = [];
    const record = (name: string) => () => {
      requests.push(name);
      throw new Error(`network: ${name}`);
    };
    const dom = new JSDOM(html, {
      runScripts: 'dangerously',
      pretendToBeVisual: true,
      beforeParse(window) {
        Object.assign(window, { fetch: record('fetch'), XMLHttpRequest: record('xhr'), WebSocket: record('websocket'), EventSource: record('eventsource') });
        window.navigator.sendBeacon = record('beacon');
      },
    });
    await new Promise((resolve) => setTimeout(resolve, 100));
    const doc = dom.window.document;

    expect(doc.querySelector('[data-testid=inventory]')?.textContent).toMatch(/40 tests/);
    const gauges = [...doc.querySelectorAll('[data-testid=gauge]')];
    expect(gauges).toHaveLength(5);
    PILLARS.forEach((name, i) => {
      expect(gauges[i].textContent).toContain(name);
      expect(gauges[i].textContent).toMatch(/\d/);
    });
    const details = doc.querySelector('[data-testid=details]')?.textContent ?? '';
    expect(PILLARS.some((name) => details.includes(name))).toBe(true);
    expect(details).toContain('Metrics');

    expect(requests).toEqual([]);
    const external = [...doc.querySelectorAll('[src], link[href]')].map((el) => el.getAttribute('src') ?? el.getAttribute('href'));
    expect(external.filter((url) => /^(https?:)?\/\//.test(url ?? ''))).toEqual([]);
    const css = [...doc.querySelectorAll('style')].map((el) => el.textContent).concat([...doc.querySelectorAll('[style]')].map((el) => el.getAttribute('style') ?? '')).join('\n');
    expect(css).not.toMatch(/@import|url\(\s*['"]?\s*(https?:)?\/\//i);
  });
});
