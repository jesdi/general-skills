// Ticket 02 of specs/test-weigh-in-dashboard-polish: searchable, paginated Overlaps tab (R1, R11).
// Seam: weigh_dashboard.write_dashboard (via tests/overlap_page.py, which documents the pinned data-testid markup), page loaded in jsdom.
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { JSDOM, type DOMWindow } from 'jsdom';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

const skill = join(import.meta.dirname, '..', 'skills/test-weigh-in');
let sandbox: string;
type Pair = { tests: [string, string]; jaccard: number };
const fixture: Record<string, { html: string; pairs: Pair[] }> = {};
const UNIQUE_LEFT = 'test_only_left_xylophone';
const UNIQUE_RIGHT = 'test_only_right_zeppelin';
const key = (a: string, b: string) => [a, b].sort().join(' | ');

const build = (order: string) => {
  const dir = join(sandbox, order);
  const r = spawnSync('python3', [join(skill, 'tests/overlap_page.py'), dir, order], { encoding: 'utf8' });
  expect(r.status, r.stderr).toBe(0);
  return { html: readFileSync(join(dir, 'dashboard.html'), 'utf8'), pairs: JSON.parse(readFileSync(join(dir, 'pairs.json'), 'utf8')) as Pair[] };
};

const open = async (html: string, requests: string[] = []) => {
  const dom = new JSDOM(html, {
    url: 'file:///offline/dashboard.html', runScripts: 'dangerously', pretendToBeVisual: true,
    beforeParse(window) {
      new window.MutationObserver(() => {
        for (const el of window.document.querySelectorAll('script[src],link[href],iframe[src],img[src],source[src]')) requests.push(el.getAttribute('src') ?? el.getAttribute('href') ?? '');
      }).observe(window.document, { subtree: true, childList: true, attributes: true, attributeFilter: ['src', 'href'] });
      window.fetch = async (input) => { requests.push(String(input)); return undefined as unknown as Response; };
      window.XMLHttpRequest.prototype.open = function (_m: string, url: string | URL) { requests.push(String(url)); };
      window.XMLHttpRequest.prototype.send = function () {};
    },
  });
  await new Promise((resolve) => setTimeout(resolve, 100));
  const doc = dom.window.document;
  const click = (label: string) => {
    const el = [...doc.querySelectorAll<HTMLElement>('[data-testid=gauge], [data-testid=details] button')].find((e) => (e.textContent ?? '').includes(label));
    expect(el, `no control "${label}"`).toBeTruthy();
    el!.click();
  };
  click('Economy');
  click('Overlaps');
  return { doc, window: dom.window };
};

const q = (doc: Document, id: string) => doc.querySelector<HTMLElement>(`[data-testid=${id}]`);
const rows = (doc: Document) => [...doc.querySelectorAll<HTMLElement>('[data-testid=overlap-pair]')];
const identities = (row: HTMLElement) => [...row.querySelectorAll('[data-testid=overlap-test]')].map((e) => e.textContent ?? '');
const similarity = (row: HTMLElement) => Number(/Jaccard[^\d]*(\d+(?:\.\d+)?)/i.exec(row.textContent ?? '')?.[1]);
const search = (doc: Document, window: DOMWindow, text: string) => {
  const input = q(doc, 'overlap-search') as HTMLInputElement | null;
  expect(input, 'no overlap search input').toBeTruthy();
  input!.value = text;
  input!.dispatchEvent(new window.Event('input', { bubbles: true }));
};
const next = (doc: Document) => {
  const b = q(doc, 'overlap-next') as HTMLButtonElement | null;
  expect(b, 'no next-page control').toBeTruthy();
  if (b!.disabled) return false;
  b!.click();
  return true;
};
const allRows = (doc: Document) => {
  const seen: { ids: string[]; jaccard: number }[] = [];
  for (let page = 0; page < 1000; page++) {
    expect(rows(doc).length, 'page rows').toBeLessThanOrEqual(100);
    for (const r of rows(doc)) seen.push({ ids: identities(r), jaccard: similarity(r) });
    if (!next(doc)) return seen;
  }
  throw new Error('pagination never ends');
};

beforeAll(() => {
  sandbox = mkdtempSync(join(tmpdir(), 'weigh-overlaps-'));
  fixture.forward = build('forward');
  fixture.reversed = build('reversed');
}, 120_000);
afterAll(() => rmSync(sandbox, { recursive: true, force: true }));

describe('overlaps evidence (R1)', () => {
  it('shows 6,757 pairs in bounded pages with distinguishable full identities and labelled Jaccard values', async () => {
    const { doc } = await open(fixture.forward.html);
    expect(q(doc, 'overlap-count')?.textContent, 'result count').toMatch(/6,?757/);
    expect(rows(doc).length).toBeGreaterThan(0);
    expect(rows(doc).length).toBeLessThanOrEqual(100);
    const row = rows(doc)[0];
    const ids = identities(row);
    expect(ids).toHaveLength(2);
    expect(ids[0]).not.toBe(ids[1]);
    expect(ids.every((i) => i.length > 200)).toBe(true); // long ids are not truncated
    expect(row.textContent).toMatch(/Jaccard/i);
    expect(Number.isFinite(similarity(row))).toBe(true);
    const wanted = new Set(fixture.forward.pairs.map((p) => key(...p.tests)));
    const shown = allRows(doc);
    expect(shown).toHaveLength(6757);
    expect(new Set(shown.map((s) => key(s.ids[0], s.ids[1]))).size).toBe(6757);
    for (const s of shown) expect(wanted.has(key(s.ids[0], s.ids[1]))).toBe(true);
  });

  it('orders descending by similarity across pages, in the same order whatever order the pairs arrive in', async () => {
    const a = allRows((await open(fixture.forward.html)).doc);
    const b = allRows((await open(fixture.reversed.html)).doc);
    for (let i = 1; i < a.length; i++) expect(a[i].jaccard, `row ${i}`).toBeLessThanOrEqual(a[i - 1].jaccard);
    expect(a.map((s) => key(s.ids[0], s.ids[1]))).toEqual(b.map((s) => key(s.ids[0], s.ids[1])));
  });

  it('finds a pair by either of its identities', async () => {
    const { doc, window } = await open(fixture.forward.html);
    for (const needle of [UNIQUE_LEFT, UNIQUE_RIGHT]) {
      search(doc, window, needle);
      expect(rows(doc)).toHaveLength(1);
      expect(identities(rows(doc)[0]).join(' ')).toContain(UNIQUE_LEFT);
    }
  });

  it('shows the first page with a count of 1 when searching from a later page; empty state; clearing restores 6,757', async () => {
    const { doc, window } = await open(fixture.forward.html);
    for (let i = 0; i < 3; i++) expect(next(doc)).toBe(true);
    search(doc, window, UNIQUE_RIGHT);
    expect(rows(doc)).toHaveLength(1);
    expect(q(doc, 'overlap-count')?.textContent).toMatch(/(^|\D)1(\D|$)/);
    expect((q(doc, 'overlap-count')?.textContent ?? '')).not.toMatch(/6,?757/);
    expect(q(doc, 'overlap-empty')).toBeNull();
    search(doc, window, 'no-such-test-anywhere');
    expect(rows(doc)).toHaveLength(0);
    expect(q(doc, 'overlap-empty')?.textContent ?? '', 'empty state').toMatch(/no .*(match|pair)/i);
    search(doc, window, '');
    expect(q(doc, 'overlap-empty')).toBeNull();
    expect(q(doc, 'overlap-count')?.textContent).toMatch(/6,?757/);
    expect(rows(doc).length).toBeGreaterThan(0);
    expect(allRows(doc)).toHaveLength(6757);
  });

  it('renders markup in a test identity literally', async () => {
    const { doc, window } = await open(fixture.forward.html);
    search(doc, window, 'test_hostile');
    expect(rows(doc)).toHaveLength(1);
    expect(identities(rows(doc)[0])).toContain(fixture.forward.pairs.find((p) => p.tests[0].includes('hostile'))!.tests[0]);
    expect(doc.querySelector('#pwn, img[src=x]')).toBeNull();
    expect((window as unknown as { pwned?: unknown }).pwned).toBeUndefined();
  });
});

describe('offline (R11)', () => {
  it('searching and paging make no network request', async () => {
    const requests: string[] = [];
    const { doc, window } = await open(fixture.forward.html, requests);
    next(doc);
    search(doc, window, UNIQUE_LEFT);
    expect(rows(doc)).toHaveLength(1);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(requests).toEqual([]);
  });
});
