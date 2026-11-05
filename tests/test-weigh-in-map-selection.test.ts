// Ticket 03 of specs/test-weigh-in-dashboard-polish: Map legend, hover identity, sorted neighbours, responsive selection (R2, R3, R4).
// Seam: weigh_dashboard.write_dashboard (via tests/map_fixture.py and tests/sorted_map_page.py), page loaded in jsdom.
// Interface the tests pin, inside the Map tab of the Economy pillar (on top of test-weigh-in-map.test.ts):
//   the legend is plain text in the tab: names "0", "1–3" and "4+" unique lines and says that size shows runtime;
//   each [data-testid=map-dot] carries its full test id as a <title> child, or a title / aria-label attribute (hover);
//   [data-testid=map-picked] names the selected test (its full id) and is absent or empty while nothing is selected;
//   [data-testid=map-similar] holds a label naming "Jaccard" and one map-similar-item per neighbour, highest first.
// jsdom timing is a regression guard only: the 200 ms claim on a real browser needs the agreed benchmark machine (design.md).
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { JSDOM } from 'jsdom';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

const skill = join(import.meta.dirname, '..', 'skills/test-weigh-in');
let sandbox: string;
let mapHtml: string;
let sortedHtml: string;

const py = (script: string, dir: string) => {
  const r = spawnSync('python3', [join(skill, 'tests', script), dir], { encoding: 'utf8' });
  expect(r.status, r.stderr).toBe(0);
  return readFileSync(join(dir, 'dashboard.html'), 'utf8');
};

beforeAll(() => {
  sandbox = mkdtempSync(join(tmpdir(), 'weigh-map-selection-'));
  mapHtml = py('map_fixture.py', join(sandbox, 'map'));
  sortedHtml = py('sorted_map_page.py', join(sandbox, 'sorted'));
}, 120_000);
afterAll(() => rmSync(sandbox, { recursive: true, force: true }));

type Dot = { test: string; x: number; y: number; runtime: number; unique: number; similar: [string, number][] };
const withMap = (html: string, edit: (map: Dot[]) => Dot[]) => {
  const [head, rest] = html.split('const data = ');
  const end = rest.indexOf(';\n');
  const data = JSON.parse(rest.slice(0, end));
  data.map = edit(data.map);
  return head + 'const data = ' + JSON.stringify(data).replace(/<\//g, '<\\/') + rest.slice(end);
};

const openMap = async (html: string) => {
  const dom = new JSDOM(html, { runScripts: 'dangerously', pretendToBeVisual: true });
  await new Promise((resolve) => setTimeout(resolve, 100));
  const doc = dom.window.document;
  const click = (label: string) => {
    const el = [...doc.querySelectorAll<HTMLElement>('[data-testid=gauge], [data-testid=details] button')].find((e) => (e.textContent ?? '').includes(label));
    expect(el, `no control "${label}"`).toBeTruthy();
    el!.click();
  };
  click('Economy');
  const start = performance.now();
  click('Map');
  const coldMs = performance.now() - start;
  const dots = () => [...doc.querySelectorAll<SVGElement>('[data-testid=map-dot]')];
  const dot = (test: string) => {
    const d = dots().find((e) => e.getAttribute('data-test') === test);
    expect(d, `no dot ${test}`).toBeTruthy();
    return d!;
  };
  const select = (test: string) => dot(test).dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
  const text = (id: string) => doc.querySelector(`[data-testid=${id}]`)?.textContent ?? '';
  const items = () => [...doc.querySelectorAll('[data-testid=map-similar-item]')].map((r) => r.textContent ?? '');
  // the blue-ringed similar dots; the selected dot is never one of them, it shows through [data-testid=map-selected]
  const highlighted = () => dots().filter((d) => d.getAttribute('data-highlighted') === 'true').map((d) => d.getAttribute('data-test'));
  // the dot the selection mark sits on, null while the mark is hidden
  const marked = () => {
    const mark = doc.querySelector('[data-testid=map-selected]');
    if (!mark || mark.getAttribute('display') === 'none') return null;
    const at = mark.querySelector('circle')!;
    const on = dots().filter((d) => d.getAttribute('cx') === at.getAttribute('cx') && d.getAttribute('cy') === at.getAttribute('cy'));
    return on.length === 1 ? on[0].getAttribute('data-test') : on.map((d) => d.getAttribute('data-test')).join(',');
  };
  return { doc, dots, dot, select, text, items, highlighted, marked, coldMs };
};

describe('legend and hover identity (R2)', () => {
  it('explains the three unique-line colours and runtime-based size, and gives each dot its full identity', async () => {
    const tests = ['tests/test_a.py::test_zero', 'tests/test_a.py::test_one', 'tests/test_a.py::test_three', 'tests/test_a.py::test_four'];
    const html = withMap(mapHtml, (map) => map.slice(0, 4).map((d, i) => ({ ...d, test: tests[i], unique: [0, 1, 3, 4][i], runtime: [0.05, 0.5, 1, 2][i] })));
    const { doc } = await openMap(html);
    const tab = doc.querySelector('[data-testid=details]')?.textContent ?? '';
    expect(tab).toMatch(/grey[^.]*\b0\b/i);
    expect(tab).toMatch(/amber[^.]*1\s*[–-]\s*3/i);
    expect(tab).toMatch(/green[^.]*4\+/i);
    expect(tab).toMatch(/unique/i);
    expect(tab).toMatch(/(size|radius)[^.]*runtime|runtime[^.]*(size|radius)/i);
    for (const t of tests) {
      const c = doc.querySelector(`[data-testid=map-dot][data-test="${t}"]`)!;
      const hover = [c.querySelector('title')?.textContent, c.getAttribute('title'), c.getAttribute('aria-label')];
      expect(hover, `hover identity of ${t}`).toContain(t);
    }
  });
});

describe('sorted neighbours (R4)', () => {
  const P = 'tests/test_pick.py::test_';
  it('names the selected test and lists eligible neighbours as 1.00, 0.95, 0.91 under a Jaccard label', async () => {
    const m = await openMap(sortedHtml);
    m.select(`${P}picked`);
    expect(m.text('map-picked')).toContain(`${P}picked`);
    expect(m.text('map-similar')).toMatch(/jaccard/i);
    const rows = m.items();
    expect(rows.map((r) => /\d\.\d\d/.exec(r)?.[0])).toEqual(['1.00', '0.95', '0.91']);
    expect(rows.map((r) => ['sim_100', 'sim_095', 'sim_091'].find((n) => r.includes(n)))).toEqual(['sim_100', 'sim_095', 'sim_091']);
    expect(rows.some((r) => r.includes('test_below'))).toBe(false);
    expect(m.highlighted().sort()).toEqual(['sim_091', 'sim_095', 'sim_100'].map((n) => P + n).sort());
    expect(m.marked()).toBe(`${P}picked`);
  });

  it('leaves no stale highlight or neighbour row after selecting another test', async () => {
    const m = await openMap(sortedHtml);
    m.select(`${P}picked`);
    m.select(`${P}lonely`);
    expect(m.text('map-picked')).toContain(`${P}lonely`);
    expect(m.text('map-picked')).not.toContain('picked');
    expect(m.highlighted()).toEqual([]);
    expect(m.marked()).toBe(`${P}lonely`);
    expect(m.items()).toEqual([]);
  });
});

describe('selected dot stands out from its similar dots (R4)', () => {
  const P = 'tests/test_pick.py::test_';
  // the selected dot gets a marker of its own: an ink ring, wider than the dot, drawn after every dot so no neighbour
  // covers it, and passing clicks through to the dots beneath; similar dots keep only their blue ring
  const marker = (m: Awaited<ReturnType<typeof openMap>>) => {
    const marks = [...m.doc.querySelectorAll<SVGElement>('[data-testid=map-selected]')];
    expect(marks, 'one selection marker').toHaveLength(1);
    return marks[0];
  };
  it('marks only the selected dot, with a ring that differs in colour and size, on top of every dot', async () => {
    const m = await openMap(sortedHtml);
    m.select(`${P}picked`);
    const mark = marker(m), dot = m.dot(`${P}picked`);
    const svg = m.doc.querySelector('.map svg')!;
    expect(svg.lastElementChild, 'drawn after every dot').toBe(mark);
    expect(mark.getAttribute('pointer-events')).toBe('none');
    const rings = [...mark.querySelectorAll('circle')];
    for (const ring of rings) {
      expect([ring.getAttribute('cx'), ring.getAttribute('cy')]).toEqual([dot.getAttribute('cx'), dot.getAttribute('cy')]);
    }
    const ink = rings.map((r) => r.getAttribute('stroke'));
    const similar = ['sim_100', 'sim_095', 'sim_091'].map((n) => m.dot(P + n).getAttribute('stroke'));
    expect(new Set(similar)).toEqual(new Set(['#2563eb']));
    expect(ink.some((s) => s && !similar.includes(s) && s !== 'none' && s !== '#fff'), `marker strokes ${ink}`).toBe(true);
    expect(Math.max(...rings.map((r) => Number(r.getAttribute('r')))), 'ring wider than the dot').toBeGreaterThan(Number(dot.getAttribute('r')));
  });

  it('moves the marker to the next selection', async () => {
    const m = await openMap(sortedHtml);
    m.select(`${P}picked`);
    m.select(`${P}lonely`);
    const dot = m.dot(`${P}lonely`);
    expect(marker(m).querySelector('circle')!.getAttribute('cx')).toBe(dot.getAttribute('cx'));
  });
});

describe('selection panel layout (R4, R9)', () => {
  const P = 'tests/test_pick.py::test_';
  it('puts each neighbour and its Jaccard value on one row under a Jaccard column header, beside the map', async () => {
    const m = await openMap(sortedHtml);
    m.select(`${P}picked`);
    const side = m.doc.querySelector('.map > svg + .map-side');
    expect(side, 'selection panel next to the map').toBeTruthy();
    expect(side!.querySelector('[data-testid=map-picked] .id')?.textContent).toBe(`${P}picked`);
    const table = side!.querySelector('[data-testid=map-similar] .similar')!;
    expect([...table.querySelector('.head')!.children].map((c) => c.textContent)).toEqual(['Test', 'Jaccard']);
    const first = table.querySelector('[data-testid=map-similar-item]')!;
    expect([...first.children].map((c) => c.textContent)).toEqual([`${P}sim_100`, '1.00']);
    expect(first.querySelector('.id')).toBeTruthy();
    expect(m.doc.querySelector('svg')!.hasAttribute('width'), 'the map scales to the width it gets').toBe(false);
  });
});

describe('selection at scale (R3, R4)', () => {
  const N = 2172, HUB = 'tests/hub/test_hub.py::test_hub', NEAR = 148;
  const id = (i: number) => `tests/scale/test_module_${String(i).padStart(4, '0')}.py::test_case_${String(i).padStart(4, '0')}_${'x'.repeat(60)}`;
  const scaled = () => {
    const names = Array.from({ length: N }, (_, i) => (i === 0 ? HUB : id(i)));
    const sim = new Map<string, [string, number][]>(names.map((n) => [n, []]));
    for (let k = 1; k <= NEAR; k++) {
      const s = 0.7 + ((k * 7) % 19) / 100;
      sim.get(HUB)!.push([names[k], s]);
      sim.get(names[k])!.push([HUB, s]);
    }
    let seed = 7;
    const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
    return names.map((test): Dot => ({ test, x: rnd(), y: rnd(), runtime: 0.01, unique: 0, similar: sim.get(test)! }));
  };
  const positions = (dots: SVGElement[]) => dots.map((d) => `${d.getAttribute('data-test')}@${d.getAttribute('cx')},${d.getAttribute('cy')}`);

  it('keeps positions stable and updates name and highlights under 200 ms for 20 alternating selections', async () => {
    const m = await openMap(withMap(mapHtml, scaled));
    console.info(`cold map opening: ${Math.round(m.coldMs)} ms (recorded separately from selections)`);
    expect(m.dots()).toHaveLength(N);
    const before = positions(m.dots());
    const other = id(5);
    for (let i = 0; i < 20; i++) {
      const target = i % 2 === 0 ? HUB : other;
      const start = performance.now();
      m.select(target);
      const ms = performance.now() - start;
      expect(m.text('map-picked'), `selection ${i}`).toContain(target);
      expect(m.highlighted().length, `selection ${i} highlights`).toBe(target === HUB ? NEAR : 1);
      expect(m.marked(), `selection ${i} mark`).toBe(target);
      expect(ms, `selection ${i} took ${Math.round(ms)} ms`).toBeLessThan(200);
    }
    expect(positions(m.dots())).toEqual(before);
    m.select(HUB);
    expect(m.items()).toHaveLength(NEAR);
  }, 300_000);
});
