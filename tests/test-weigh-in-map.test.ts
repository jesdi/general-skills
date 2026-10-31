// Ticket tw/s4-coverage/08: Economy's Overlaps and Map tabs, loaded in jsdom from a fixture snapshot rendered by the skill.
// Page contract on top of test-weigh-in-dashboard.test.ts:
//   tabs of the selected gauge are the <button>s inside [data-testid=details], labelled by their text;
//   the Map tab holds one [data-testid=map-dot] per passing test, its node id in the data-test attribute;
//   clicking a dot sets data-highlighted="true" on the dots of the tests similar to it (>= 0.6, so a test at exactly 0.60 counts and one at 0.50 does not) and fills
//   [data-testid=map-similar] with one [data-testid=map-similar-item] per such test, showing its node id and the similarity as 0.00.
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { JSDOM } from 'jsdom';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

const root = join(import.meta.dirname, '..');
const skill = join(root, 'skills/test-weigh-in');
const INTENT = 'tests/test_intent.py::test_intent_1';
const TWINS = ['test_twin_1', 'test_twin_2', 'test_twin_3'].map((n) => `tests/test_twin.py::${n}`);
const OTHERS = [1, 2, 3, 4].map((n) => `tests/test_other.py::test_other_${n}`);
const EDGE_AT = 'tests/test_edge.py::test_edge_at';
const EDGE_BELOW = 'tests/test_edge.py::test_edge_below';
let sandbox: string;
let html: string;

beforeAll(() => {
  sandbox = mkdtempSync(join(tmpdir(), 'weigh-map-'));
  const build = spawnSync('python3', [join(skill, 'tests/map_fixture.py'), sandbox], { encoding: 'utf8' });
  expect(build.status, build.stderr).toBe(0);
  html = readFileSync(join(sandbox, 'dashboard.html'), 'utf8');
});

afterAll(() => rmSync(sandbox, { recursive: true, force: true }));

const open = async () => {
  const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true });
  await new Promise((resolve) => setTimeout(resolve, 100));
  return dom.window.document;
};
const tabs = (doc: Document) => [...doc.querySelectorAll<HTMLElement>('[data-testid=details] button')];
const tabNames = (doc: Document) => tabs(doc).map((b) => b.textContent?.trim());
const gauges = (doc: Document) => [...doc.querySelectorAll<HTMLElement>('[data-testid=gauge]')];

describe('Economy details tabs', () => {
  it('shows Metrics, Worst tests, Smells, Overlaps and Map for Economy and no Map tab for any other pillar', async () => {
    const doc = await open();
    const all = gauges(doc);
    expect(all).toHaveLength(5);
    const economy = all.findIndex((g) => g.textContent?.includes('Economy'));
    all[economy].click();
    expect(tabNames(doc)).toEqual(['Metrics', 'Worst tests', 'Smells', 'Overlaps', 'Map']);
    for (const [i] of all.entries()) {
      if (i === economy) continue;
      gauges(doc)[i].click();
      expect(tabNames(doc), `gauge ${i}`).not.toContain('Map');
    }
  });
});

describe('Overlaps tab', () => {
  it('lists the 12 pairs at 1.00 inside the twins and inside the others, and no pair with an edge test', async () => {
    const doc = await open();
    gauges(doc).find((g) => g.textContent?.includes('Economy'))!.click();
    tabs(doc).find((b) => b.textContent?.trim() === 'Overlaps')!.click();
    const rows = [...doc.querySelectorAll('[data-testid=details] p')].map((r) => r.textContent ?? '');
    expect(rows).toHaveLength(12);
    for (const group of [[INTENT, ...TWINS], OTHERS]) {
      for (const [i, a] of group.entries()) {
        for (const b of group.slice(i + 1)) {
          expect(rows.find((r) => r.includes(a) && r.includes(b)), `${a} ~ ${b}`).toContain('1.00');
        }
      }
    }
    for (const edge of [EDGE_AT, EDGE_BELOW]) expect(rows.some((r) => r.includes(edge))).toBe(false);
  });
});

describe('Map tab', () => {
  it('highlights and lists the 3 twins (1.00) and the test at exactly 0.60 of test_intent_1, and no other test', async () => {
    const doc = await open();
    gauges(doc).find((g) => g.textContent?.includes('Economy'))!.click();
    tabs(doc).find((b) => b.textContent?.trim() === 'Map')!.click();
    const dots = () => [...doc.querySelectorAll<HTMLElement>('[data-testid=map-dot]')];
    expect(dots().map((d) => d.dataset.test).sort()).toEqual([INTENT, ...TWINS, ...OTHERS, EDGE_AT, EDGE_BELOW].sort());

    dots().find((d) => d.dataset.test === INTENT)!.dispatchEvent(new doc.defaultView!.MouseEvent('click', { bubbles: true }));

    const highlighted = dots().filter((d) => d.dataset.highlighted === 'true').map((d) => d.dataset.test);
    expect(highlighted.filter((n) => n !== INTENT).sort()).toEqual([...TWINS, EDGE_AT].sort());
    for (const other of [...OTHERS, EDGE_BELOW]) expect(highlighted).not.toContain(other);
    const rows = [...doc.querySelectorAll('[data-testid=map-similar-item]')].map((r) => r.textContent ?? '');
    expect(rows).toHaveLength(4);
    for (const twin of TWINS) {
      const row = rows.find((r) => r.includes(twin));
      expect(row, twin).toBeDefined();
      expect(row).toContain('1.00');
    }
    // a test at exactly 0.6 is similar, a test at 0.5 is not
    expect(rows.find((r) => r.includes(EDGE_AT))).toContain('0.60');
    for (const other of [...OTHERS, EDGE_BELOW]) expect(rows.some((r) => r.includes(other))).toBe(false);
    const listed = doc.querySelector('[data-testid=map-similar]')?.textContent ?? '';
    for (const other of OTHERS) expect(listed).not.toContain(other.split('::')[1]);
  });
});
