// The Map tab keeps every dot clickable: tests with the same coverage (test_intent_1 and its 3 twins in the map
// fixture) share an MDS place, so the page spreads them and no dot's centre lies under another dot.
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { JSDOM } from 'jsdom';
import { expect, it } from 'vitest';

const skill = join(import.meta.dirname, '..', 'skills/test-weigh-in');

it('draws no dot over the centre of another', async () => {
  const sandbox = mkdtempSync(join(tmpdir(), 'weigh-dots-'));
  try {
    const build = spawnSync('python3', [join(skill, 'tests/map_fixture.py'), sandbox], { encoding: 'utf8' });
    expect(build.status, build.stderr).toBe(0);
    const dom = new JSDOM(readFileSync(join(sandbox, 'dashboard.html'), 'utf8'), { runScripts: 'dangerously' });
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
  } finally {
    rmSync(sandbox, { recursive: true, force: true });
  }
});
