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

describe('pair layout (R1, R9)', () => {
  it('sets the two identities apart with a divider between them and breaks them only between their parts', async () => {
    const { doc } = await open(fixture.forward.html);
    await new Promise((resolve) => setTimeout(resolve, 50)); // the breaks are added before the next paint
    const row = rows(doc)[0];
    expect(row.querySelector('strong')?.textContent).toMatch(/^Jaccard \d\.\d\d$/);
    const [a, b] = [...row.querySelectorAll<HTMLElement>('[data-testid=overlap-test]')];
    expect(a.parentElement).toBe(b.parentElement);
    expect(a.nextElementSibling?.tagName, 'a divider between A and B').toBe('HR');
    expect(a.nextElementSibling?.nextElementSibling).toBe(b);
    for (const id of [a, b]) {
      expect(id.classList.contains('id'), 'identity styled as a wrapping id').toBe(true);
      // every break opportunity follows a separator, never the middle of a word
      for (const w of id.querySelectorAll('wbr')) expect(w.previousSibling?.textContent).toMatch(/(\/|::|\.|_)$/);
      expect(id.innerHTML, 'a file name keeps its .py').not.toMatch(/\.<wbr>py/);
      expect(id.querySelectorAll('wbr').length).toBeGreaterThan(0);
    }
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

// A pager sits above the list as well as below it, so 100 tall rows never stand between the reader and the next page.
describe('pager above and below the list', () => {
  const PAGER = ['overlap-prev', 'overlap-page', 'overlap-next'];
  const before = (a: Node, b: Node) => (a.compareDocumentPosition(b) & 4) !== 0; // b follows a
  const button = (doc: Document, id: string) => q(doc, id) as HTMLButtonElement;
  const state = (doc: Document, suffix: string) => ({
    page: q(doc, 'overlap-page' + suffix)?.textContent?.trim(),
    prev: button(doc, 'overlap-prev' + suffix).disabled,
    next: button(doc, 'overlap-next' + suffix).disabled,
  });

  it('puts one pager before the first pair and one after the last, each control with its own test id', async () => {
    const { doc } = await open(fixture.forward.html);
    const all = rows(doc);
    for (const id of PAGER) {
      expect(doc.querySelectorAll(`[data-testid=${id}]`), id).toHaveLength(1);
      expect(doc.querySelectorAll(`[data-testid=${id}-bottom]`), id + '-bottom').toHaveLength(1);
      expect(before(q(doc, id)!, all[0]), `${id} above the list`).toBe(true);
      expect(before(all[all.length - 1], q(doc, id + '-bottom')!), `${id}-bottom below the list`).toBe(true);
    }
    const navs = [...doc.querySelectorAll('nav')].filter((n) => n.querySelector('[data-testid^=overlap-]'));
    expect(navs).toHaveLength(2);
    expect(new Set(navs.map((n) => n.getAttribute('aria-label'))).size, 'two distinct pager labels').toBe(2);
  });

  it('keeps both pagers in step: counter, and disabled at the first and last page', async () => {
    const { doc } = await open(fixture.forward.html);
    expect(state(doc, '')).toEqual({ page: 'Page 1 of 68', prev: true, next: false });
    expect(state(doc, '-bottom')).toEqual(state(doc, ''));
    button(doc, 'overlap-next-bottom').click();
    expect(state(doc, '')).toEqual({ page: 'Page 2 of 68', prev: false, next: false });
    expect(state(doc, '-bottom')).toEqual(state(doc, ''));
    for (let page = 2; page < 68; page++) button(doc, 'overlap-next').click();
    for (const suffix of ['', '-bottom']) expect(state(doc, suffix), `pager${suffix}`).toEqual({ page: 'Page 68 of 68', prev: false, next: true });
  });

  it('sends focus to the top pager after paging from the bottom, and keeps it on a pager that stays usable', async () => {
    const { doc } = await open(fixture.forward.html);
    button(doc, 'overlap-next-bottom').focus();
    button(doc, 'overlap-next-bottom').click();
    expect(doc.activeElement, 'bottom Next lands on top Next').toBe(button(doc, 'overlap-next'));
    button(doc, 'overlap-prev').focus();
    button(doc, 'overlap-prev').click(); // back on page 1: Previous is now disabled
    expect(doc.activeElement, 'a pager button that became disabled hands focus to its sibling').toBe(button(doc, 'overlap-next'));
  });
});
