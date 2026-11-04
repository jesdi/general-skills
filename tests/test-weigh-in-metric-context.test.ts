// Ticket 01 of specs/test-weigh-in-dashboard-polish: metric context, unmeasured reasons, failing identities, score bounds.
// Seam: weigh_dashboard.write_dashboard (via tests/metric_page.py fixtures) and the weigh.py CLI, pages loaded in jsdom.
// Pinned interface: snapshot["unmeasured"] = {metric id: reason}; an absent entry reads as "unavailable" / "not recorded".
import { spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

const root = join(import.meta.dirname, '..');
const skill = join(root, 'skills/test-weigh-in');
const fixtures = join(skill, 'tests');
let sandbox: string;
const pages: Record<string, string> = {};
// Slashes in the script-closing identity are directory separators in the CLI fixture.
const hostile = 'test_<b id="pwn">x<img src=x onerror="pwned=1">/test_</script><script>window.pwned=1</script>/test_hostile.py';
const hostileIdentity = `tests/${hostile}::test_hostile`;

const python = (code: string, ...args: string[]) => {
  const r = spawnSync('python3', ['-c', code, ...args], { encoding: 'utf8' });
  expect(r.status, r.stderr).toBe(0);
  return r.stdout.trim();
};

const open = async (html: string, requests: string[] = []) => {
  const dom = new JSDOM(html, {
    url: 'file:///offline/dashboard.html',
    runScripts: 'dangerously', pretendToBeVisual: true,
    beforeParse(window) {
      // jsdom blocks subresources by default; record dynamic resource references too.
      new window.MutationObserver(() => {
        for (const el of window.document.querySelectorAll('script[src],link[href],iframe[src],img[src],source[src],video[src],audio[src]')) {
          requests.push(el.getAttribute('src') ?? el.getAttribute('href') ?? '');
        }
      }).observe(window.document, { subtree: true, childList: true, attributes: true, attributeFilter: ['src', 'href'] });
      window.fetch = async (input) => { requests.push(String(input)); return undefined as unknown as Response; };
      window.XMLHttpRequest.prototype.open = function (_method: string, url: string | URL) { requests.push(String(url)); };
      window.XMLHttpRequest.prototype.send = function () {};
      window.navigator.sendBeacon = (url) => { requests.push(String(url)); return false; };
    },
  });
  await new Promise((resolve) => setTimeout(resolve, 50));
  return dom.window.document;
};
const click = (doc: Document, label: string, within = '[data-testid=gauge], [data-testid=details] button') => {
  const el = [...doc.querySelectorAll<HTMLElement>(within)].find((e) => (e.textContent ?? '').includes(label));
  expect(el, `no control "${label}"`).toBeTruthy();
  el!.click();
};
const details = (doc: Document) => doc.querySelector('[data-testid=details]')?.textContent ?? '';
const row = (doc: Document, name: string) => {
  const r = [...doc.querySelectorAll('tr')].find((tr) => (tr.textContent ?? '').includes(name));
  expect(r, `no row "${name}"`).toBeTruthy();
  return r!;
};

beforeAll(() => {
  sandbox = mkdtempSync(join(tmpdir(), 'weigh-metric-context-'));
  for (const s of ['values', 'zero', 'scores', 'default']) pages[s] = readFileSync(python('import sys; sys.path.insert(0, sys.argv[1]); import metric_page; from pathlib import Path; print(metric_page.build(Path(sys.argv[2]), sys.argv[3]))', fixtures, join(sandbox, s), s), 'utf8');
  // R7 fixture: test_checkout fails, test_setup errors in its fixture, test_ok passes, test_hostile fails in a file named with markup
  const repo = join(sandbox, 'work/demo');
  const uv = python('import sys; sys.path.insert(0, sys.argv[1]); from fixture_repo import build_repo, git, uv; r = build_repo(sys.argv[2]); print(uv())', fixtures, repo);
  // a third failing test lives in a file whose name holds markup: its identity must render literally and in full
  mkdirSync(dirname(join(repo, 'tests', hostile)), { recursive: true });
  writeFileSync(join(repo, 'tests', hostile), 'def test_hostile():\n    assert 1 == 2\n');
  writeFileSync(join(repo, 'tests/test_polish.py'), `import pytest\n@pytest.fixture\ndef boom():\n    raise RuntimeError("setup")\ndef test_checkout():\n    assert 1 == 2\ndef test_setup(boom):\n    pass\ndef test_ok():\n    assert True\n`);
  const home = join(sandbox, 'home');
  const env = { ...process.env, HOME: home, TEST_WEIGH_IN_CACHE_DIR: join(sandbox, 'cache'), TEST_WEIGH_IN_UV: uv, BROWSER: 'true' };
  delete env.VIRTUAL_ENV;
  delete env.PYTHONPATH;
  const weigh = spawnSync('python3', [join(skill, 'weigh.py'), repo, '--no-open'], { env, encoding: 'utf8' });
  expect(weigh.status, weigh.stdout + weigh.stderr).toBe(0);
  pages.red = readFileSync(join(home, '.test-weigh-in/github.com/jesdi/demo/dashboard.html'), 'utf8');
}, 600_000);

afterAll(() => rmSync(sandbox, { recursive: true, force: true }));

describe('metrics tab context (R5, R6)', () => {
  it('R5: separate value and score headers, units, and directional anchors from the supplied rules', async () => {
    const doc = await open(pages.values);
    click(doc, 'Fast feedback');
    const heads = [...doc.querySelectorAll('th')].map((th) => th.textContent ?? '');
    for (const h of [/metric/i, /value/i, /score/i]) expect(heads.some((t) => h.test(t)), `header ${h}`).toBe(true);
    const runtime = row(doc, 'Total runtime').textContent ?? '';
    expect(runtime).toMatch(/64\.16\s?s/);
    expect(runtime).toMatch(/target\s*≤\s*30\s?s/);
    expect(runtime).toMatch(/bad\s*≥\s*300\s?s/);
    click(doc, 'Protection against regressions');
    const coverage = row(doc, 'Branch coverage').textContent ?? '';
    expect(coverage).toMatch(/94\.84\s?%/);
    expect(coverage).toMatch(/target\s*≥\s*90\s?%/);
    expect(coverage).toMatch(/bad\s*≤\s*50\s?%/);
  });

  it('R6: a pillar with 0 of 4 measured names all four metrics with reasons and says none was measured', async () => {
    const doc = await open(pages.zero);
    click(doc, 'Resistance to refactoring');
    const text = details(doc);
    for (const [name, reason] of [['Brittle test files', 'no git history'], ['Tests with private access', 'no ruff'], ['Mock-heavy tests', 'no ruff'], ['Interaction-only tests', 'no ruff']]) {
      expect(text).toContain(name);
      expect(text).toContain(reason);
    }
    expect(text).toMatch(/no metrics (were|was) measured|none of the metrics (were|was) measured/i);
  });

  it('R6: the default run names the unmeasured mutation metrics with the mutation-tier reason, keeping measured ones', async () => {
    const doc = await open(pages.red);
    click(doc, 'Protection against regressions');
    const text = details(doc);
    expect(text).toContain('Branch coverage');
    expect(text).toContain('Mutation score');
    expect(text).toContain('Pseudo-tested functions');
    expect(text).toMatch(/mutation tier did not run/i);
    for (const name of ['Mutation score', 'Pseudo-tested functions']) {
      expect(row(doc, name).textContent).toMatch(/unmeasured.*mutation tier did not run/i);
    }
  });

  it('R5/R6: a measured zero stays measured; a missing metric with no recorded cause is unavailable, with no invented reason or score', async () => {
    const doc = await open(pages.zero);
    click(doc, 'Fast feedback');
    const zero = row(doc, 'Tests over 10 s').textContent ?? '';
    expect(zero).toMatch(/(^|[^\d.])0(\.0+)?\s?%/);
    expect(zero).not.toMatch(/unmeasured|not measured|unavailable/i);
    const missing = row(doc, 'p95 test duration').textContent ?? '';
    expect(missing).toMatch(/unmeasured|not measured/i);
    expect(missing).toMatch(/unavailable|not recorded/i);
    expect(missing).not.toMatch(/\d\s?s\b|\b\d+\s*$/);
  });
});

describe('score bounds (R10)', () => {
  it('shows raw 0.2 and 100 as 1 and 99, keeps 90 for zero tests over 10 s, and explains rounding, bounds and anchors', async () => {
    const doc = await open(pages.scores);
    click(doc, 'Maintainability');
    const cells = (name: string) => [...row(doc, name).querySelectorAll('td')].map((td) => (td.textContent ?? '').trim());
    expect(cells('Tests with a maintainability smell')).toContain('1');
    expect(cells('Tests longer than 50 lines')).toContain('99');
    click(doc, 'Fast feedback');
    expect(cells('Tests over 10 s')).toContain('90');
    const page = doc.body.textContent ?? '';
    expect(page).toMatch(/rounded/i);
    expect(page).toMatch(/1\s*(–|-|to)\s*99/);
    expect(page).toMatch(/target[^.]*\b90\b/i);
    expect(page).toMatch(/bad[^.]*\b10\b/i);
    expect(page).toMatch(/not\s+100|100\b[^.]*(not|never)/i);
  });
});

describe('default run through the CLI (R6)', () => {
  it('the CLI-generated report names the omitted mutation metrics with the mutation-tier reason', async () => {
    const doc = await open(pages.red);
    click(doc, 'Protection against regressions');
    const text = details(doc);
    expect(text).toContain('Mutation score');
    expect(text).toContain('Pseudo-tested functions');
    expect(text).toMatch(/mutation tier did not run/i);
    for (const name of ['Mutation score', 'Pseudo-tested functions']) {
      expect(row(doc, name).textContent).toMatch(/unmeasured.*mutation tier did not run/i);
    }
  });
});

describe('safe identities and offline evidence (risk)', () => {
  it('renders a full test identity containing HTML literally, with no injected markup or script', async () => {
    const doc = await open(pages.red);
    const names = [...doc.querySelectorAll('[data-testid=failing-test]')].map((e) => e.textContent ?? '');
    expect(names, 'complete literal identity: ' + JSON.stringify(names)).toContain(hostileIdentity);
    expect(doc.getElementById('pwn')).toBeNull();
    expect(doc.querySelector('img[onerror]')).toBeNull();
    expect((doc.defaultView as unknown as { pwned?: number }).pwned).toBeUndefined();
  });

  it('keeps metric evidence available with no external resource and no network call', async () => {
    const html = pages.red;
    expect(html).not.toMatch(/(src|href)\s*=\s*["']?https?:/i);
    expect(html).not.toMatch(/\bfetch\s*\(|XMLHttpRequest|@import\s+url\(\s*["']?https?:/);
    const requests: string[] = [];
    const doc = await open(html, requests);
    click(doc, 'Fast feedback');
    expect(details(doc)).toMatch(/Total runtime/);
    click(doc, 'Protection against regressions');
    expect(row(doc, 'Mutation score').textContent).toMatch(/mutation tier did not run/i);
    click(doc, 'Worst tests');
    expect(doc.querySelector('[data-testid=worst-test]')).toBeTruthy();
    click(doc, 'Economy');
    click(doc, 'Overlaps');
    expect(doc.querySelector('[data-testid=overlap-pair]')).toBeTruthy();
    click(doc, 'Map');
    const dot = doc.querySelector<SVGElement>('[data-testid=map-dot]');
    expect(dot).toBeTruthy();
    dot!.dispatchEvent(new doc.defaultView!.MouseEvent('click', { bubbles: true }));
    expect(doc.querySelector('[data-highlighted=true]')).toBeTruthy();
    click(doc, 'Metrics');
    expect(details(doc)).toMatch(/rounded/i);
    expect([...doc.querySelectorAll('[data-testid=failing-test]')].map((el) => el.textContent)).toContain(hostileIdentity);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(requests, 'offline interactions must not attempt network access').toEqual([]);
    doc.defaultView!.close();
  });
});

describe('failing tests (R7)', () => {
  it('names the failing and erroring tests beside the banner, not the passing one, and agrees with the banner', async () => {
    const doc = await open(pages.red);
    expect(doc.querySelector('[data-testid=banner]')?.textContent).toMatch(/3 tests are failing/);
    const names = [...doc.querySelectorAll('[data-testid=failing-test]')].map((e) => e.textContent ?? '');
    expect(names.some((n) => n.includes('tests/test_polish.py::test_checkout'))).toBe(true);
    expect(names.some((n) => n.includes('tests/test_polish.py::test_setup'))).toBe(true);
    expect(names.some((n) => n.includes('test_ok'))).toBe(false);
    expect(names.some((n) => n.includes('::test_hostile'))).toBe(true);
    expect(names).toHaveLength(3);
  });
});
