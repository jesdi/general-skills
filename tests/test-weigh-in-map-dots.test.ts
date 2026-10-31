// The Map tab keeps every dot clickable: tests with the same coverage (test_intent_1 and its 3 twins in the map
// fixture) share an MDS place, and tests with nearly the same coverage land a fraction of a pixel apart, so the
// page spreads them and no dot's centre lies under another dot.
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { JSDOM } from 'jsdom';
import { afterAll, beforeAll, expect, it } from 'vitest';

const skill = join(import.meta.dirname, '..', 'skills/test-weigh-in');
let sandbox: string;
let html: string;

beforeAll(() => {
  sandbox = mkdtempSync(join(tmpdir(), 'weigh-dots-'));
  const build = spawnSync('python3', [join(skill, 'tests/map_fixture.py'), sandbox], { encoding: 'utf8' });
  expect(build.status, build.stderr).toBe(0);
  html = readFileSync(join(sandbox, 'dashboard.html'), 'utf8');
});

afterAll(() => rmSync(sandbox, { recursive: true, force: true }));

const expectNoDotOverAnother = async (page: string) => {
  const dom = new JSDOM(page, { runScripts: 'dangerously' });
  await new Promise((resolve) => setTimeout(resolve, 100));
  const doc = dom.window.document;
  [...doc.querySelectorAll<HTMLElement>('[data-testid=gauge]')].find((g) => g.textContent?.includes('Economy'))!.click();
  [...doc.querySelectorAll<HTMLElement>('[data-testid=details] button')].find((b) => b.textContent === 'Map')!.click();
  const dots = [...doc.querySelectorAll('[data-testid=map-dot]')].map((c) => ({
    test: c.getAttribute('data-test'),
    x: Number(c.getAttribute('cx')),
    y: Number(c.getAttribute('cy')),
    r: Number(c.getAttribute('r')),
  }));
  expect(dots).toHaveLength(10);
  for (const a of dots)
    for (const b of dots) if (a !== b) expect(Math.hypot(a.x - b.x, a.y - b.y), `${b.test} over ${a.test}`).toBeGreaterThan(b.r);
  const [x0, y0, w, h] = doc.querySelector('svg')!.getAttribute('viewBox')!.split(' ').map(Number);
  for (const d of dots) expect(d.x >= x0 && d.y >= y0 && d.x <= x0 + w && d.y <= y0 + h, d.test ?? '').toBe(true);
};

it('draws no dot over the centre of another', () => expectNoDotOverAnother(html));

it('draws no dot over the centre of another when places differ by a fraction of a pixel', async () => {
  const [head, rest] = html.split('const data = ');
  const end = rest.indexOf(';\n');
  const data = JSON.parse(rest.slice(0, end));
  data.map = data.map.map((d: { x: number; y: number }, i: number) => ({ ...d, x: 0.5 + i * 0.001, y: 0.5 }));
  await expectNoDotOverAnother(head + 'const data = ' + JSON.stringify(data) + rest.slice(end));
});
