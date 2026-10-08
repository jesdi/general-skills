import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import matter from 'gray-matter';
import { JSDOM } from 'jsdom';
import { describe, expect, it } from 'vitest';

// Acceptance tests for skills/review-page (ticket 02). Black box: they read the
// three skill files and drive template.html in jsdom.
//
// Interface the template must meet:
// - <body data-mode="plan"> by default; "questionnaire" is the other mode.
// - Sections <section data-slot="tickets|questions|corrections|track">, never
//   nested <section>s inside a slot. The session fills a slot's content.
// - Bar <div class="bar"> with #state and no button: the console owns the
//   buttons, and every answers message has submit null.
// - A question: <div class="q" data-q="ID"> with radios name="ID" and
//   <textarea id="note-ID">. Track pills: radios name="track".
// - The script reads the DOM present at load and at event time.
// - Messages: ready / answers / restore, exactly as spec.md, all with v: 1.
//
// Iframe criterion: jsdom does not give a srcdoc iframe a script-visible
// parent we can spy on reliably, so window.parent is stubbed with a getter
// (defined in beforeParse, before any script runs) that returns a second jsdom
// window whose postMessage is a spy. Message events carry that window (or
// another one) as `source`.
//
// Visual criteria (theme rendering, 400 px layout, safe area) cannot be
// measured in jsdom; the CSS checks below are a proxy and the real check is
// agent-ops' Playwright e2e (ticket 06).

const dir = join(__dirname, '..', 'skills', 'review-page');

function read(file: string): string {
  try {
    return readFileSync(join(dir, file), 'utf8');
  } catch {
    throw new Error(`file not found: skills/review-page/${file}`);
  }
}

// ---- fixtures -------------------------------------------------------------

const opts = (id: string) => `
<div class="q" data-q="${id}">
  <h3>Question ${id}</h3>
  <label class="opt"><input type="radio" name="${id}" value="a"><span>A <span class="chip rec">recommended</span></span></label>
  <label class="opt"><input type="radio" name="${id}" value="b"><span>B</span></label>
  <textarea id="note-${id}"></textarea>
</div>`;
const QUESTIONS = ['format', 'limit', 'headers'].map(opts).join('\n');
const TRACK = ['trivial', 'standard', 'security']
  .map((t) => `<label><input type="radio" name="track" value="${t}"${t === 'standard' ? ' checked' : ''}>${t}</label>`)
  .join('');

function fillSlot(html: string, slot: string, content: string): string {
  const re = new RegExp(`(<section[^>]*data-slot="${slot}"[^>]*>)[\\s\\S]*?(</section>)`);
  if (!re.test(html)) throw new Error(`template has no <section data-slot="${slot}">`);
  return html.replace(re, (_m, open, close) => `${open}${content}${close}`);
}

type Msg = Record<string, any>;
interface Page {
  win: any;
  doc: Document;
  sent: Msg[];
  parentWin: any;
  otherWin: any;
}

async function load(mode: 'plan' | 'questionnaire', embedded = true): Promise<Page> {
  let html = read('template.html');
  if (!html.includes('data-mode="plan"')) throw new Error('template body must default to data-mode="plan"');
  html = html.replace('data-mode="plan"', `data-mode="${mode}"`);
  html = fillSlot(html, 'questions', QUESTIONS);
  if (mode === 'plan') html = fillSlot(html, 'track', TRACK);

  const parentWin = new JSDOM('').window as any;
  const otherWin = new JSDOM('').window as any;
  const sent: Msg[] = [];
  parentWin.postMessage = (m: unknown) => sent.push(JSON.parse(JSON.stringify(m)));

  const dom = new JSDOM(html, {
    runScripts: 'dangerously',
    beforeParse(w: any) {
      if (embedded) Object.defineProperty(w, 'parent', { get: () => parentWin, configurable: true });
    },
  });
  const win = dom.window as any;
  if (win.document.readyState !== 'complete') {
    await new Promise((r) => win.addEventListener('load', r));
  }
  return { win, doc: win.document, sent, parentWin, otherWin };
}

const visible = (win: any, el: Element | null): boolean =>
  !!el && !(el as HTMLElement).hidden && win.getComputedStyle(el).display !== 'none';

function pick(p: Page, name: string, value: string) {
  const r = p.doc.querySelector(`input[name="${name}"][value="${value}"]`) as HTMLInputElement;
  r.checked = true;
  r.dispatchEvent(new p.win.Event('change', { bubbles: true }));
}
function note(p: Page, id: string, text: string) {
  const t = p.doc.getElementById(`note-${id}`) as HTMLTextAreaElement;
  t.value = text;
  t.dispatchEvent(new p.win.Event('input', { bubbles: true }));
}
const click = (p: Page, sel: string) =>
  (p.doc.querySelector(sel) as HTMLElement).dispatchEvent(new p.win.MouseEvent('click', { bubbles: true }));
function deliver(p: Page, data: unknown, from: any) {
  p.win.dispatchEvent(new p.win.MessageEvent('message', { data, source: from }));
}
const checked = (p: Page, name: string) =>
  (p.doc.querySelector(`input[name="${name}"]:checked`) as HTMLInputElement | null)?.value;

// ---- SKILL.md and schema.md ----------------------------------------------

describe('review-page SKILL.md', () => {
  it('has the frontmatter name review-page', () => {
    const { data } = matter(read('SKILL.md'));
    expect(data.name).toBe('review-page');
    expect(typeof data.description).toBe('string');
  });

  it('describes modes, slots, the script rule, the id rule, one recommended option, and no network/storage/external file', () => {
    const body = matter(read('SKILL.md')).content;
    expect(body).toMatch(/\bplan\b/);
    expect(body).toMatch(/\bquestionnaire\b/);
    for (const slot of ['tickets', 'questions', 'corrections', 'track']) expect(body).toContain(slot);
    expect(body).toContain('data-slot');
    expect(body).toMatch(/never\s+(edit|modif|chang)\w*[^.\n]*script|script[^.\n]*never\s+(edited|modified|changed)/i);
    expect(body).toContain('[a-z0-9_-]{1,64}');
    expect(body).toMatch(/one recommended/i);
    expect(body).toMatch(/\b(no|never|without|not)\b[^.\n]*network/i);
    expect(body).toMatch(/\b(no|never|without|not)\b[^.\n]*storage/i);
    expect(body).toMatch(/\b(no|never|without|not)\b[^.\n]*external/i);
  });
});

describe('review-page schema.md', () => {
  it('names the three messages and the v rule', () => {
    const s = read('schema.md');
    for (const t of ['ready', 'answers', 'restore']) expect(s).toContain(t);
    expect(s).toMatch(/[`"]v[`"]/);
    expect(s).toMatch(/integer\s+`?1`?/i);
  });

  it('names the answers file fields', () => {
    const s = read('schema.md');
    for (const f of ['v', 'stage', 'submitted', 'submitted_at', 'actor', 'answers']) {
      expect(s).toMatch(new RegExp(`[\`"]${f}[\`"]`));
    }
  });
});

// ---- template.html: static ------------------------------------------------

describe('review-page template.html (static)', () => {
  it('carries the marker', () => {
    expect(read('template.html')).toContain('<meta name="agent-ops-review" content="1">');
  });

  it('has no external URL, script src or link', () => {
    const html = read('template.html');
    expect(html).not.toMatch(/https?:\/\//i);
    expect(html).not.toMatch(/<script[^>]*\ssrc\s*=/i);
    expect(html).not.toMatch(/<link\b/i);
  });
});

// ---- template.html: theme and width (proxy for the visual criteria) -------

describe('review-page template.html (stylesheet)', () => {
  const css = () => [...read('template.html').matchAll(/<style[^>]*>([\s\S]*?)<\/style>/gi)].map((m) => m[1]).join('\n');

  it('defines the console tokens for light, prefers-color-scheme dark and data-theme dark', () => {
    const c = css();
    expect(c).toMatch(/@media\s*\(prefers-color-scheme:\s*dark\)/);
    expect(c).toContain(':root[data-theme="dark"]');
    for (const t of ['surface', 'raised', 'ink', 'muted', 'border', 'accent']) {
      // one definition each for :root, the media query and the data-theme rule
      expect(c.match(new RegExp(`--${t}\\s*:`, 'g'))?.length ?? 0, `--${t}`).toBeGreaterThanOrEqual(3);
    }
  });

  it('keeps the bar clear of the safe area and sets no fixed width above 400px', () => {
    const c = css();
    expect(c).toContain('env(safe-area-inset-bottom');
    for (const [, sel, body] of c.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const targets = sel.split(',').map((s) => s.trim());
      if (!targets.some((s) => ['main', '.bar', 'body'].includes(s))) continue;
      for (const [, px] of body.matchAll(/(?<![-\w])(?:min-)?width\s*:\s*(\d+(?:\.\d+)?)px/g)) {
        expect(Number(px), `${sel.trim()} { width: ${px}px }`).toBeLessThanOrEqual(400);
      }
    }
  });
});

// ---- template.html: behaviour ---------------------------------------------

describe('review-page template.html (plan mode)', () => {
  it('shows the four sections, a recommended chip and a bar with no button', async () => {
    const p = await load('plan');
    for (const slot of ['tickets', 'questions', 'corrections', 'track']) {
      expect(visible(p.win, p.doc.querySelector(`section[data-slot="${slot}"]`)), slot).toBe(true);
    }
    expect(p.doc.querySelector('.q .chip.rec')).not.toBeNull();
    expect(visible(p.win, p.doc.querySelector('.bar'))).toBe(true);
    // The console owns the buttons: a page that could submit could approve itself.
    expect(p.doc.querySelectorAll('button')).toHaveLength(0);
  });

  it('never posts a submission: every answers message is a draft', async () => {
    const p = await load('plan');
    pick(p, 'format', 'a');
    const sets = p.sent.filter((m) => m.type === 'answers');
    expect(sets.length).toBeGreaterThan(0);
    expect(sets.every((m) => m.submit === null)).toBe(true);
    expect(read('template.html')).not.toMatch(/submit: *['"`]|'approve'|"approve"/);
  });
});

describe('review-page template.html (questionnaire mode)', () => {
  it('hides tickets, corrections and track and has no button', async () => {
    const p = await load('questionnaire');
    expect(visible(p.win, p.doc.querySelector('section[data-slot="questions"]'))).toBe(true);
    for (const slot of ['tickets', 'corrections', 'track']) {
      const el = p.doc.querySelector(`section[data-slot="${slot}"]`);
      expect(el === null || !visible(p.win, el), slot).toBe(true);
    }
    expect(p.doc.querySelectorAll('button')).toHaveLength(0);
  });
});

describe('review-page template.html (messages in an iframe)', () => {
  it('posts ready once on load, before any answers', async () => {
    const p = await load('plan');
    expect(p.sent[0]).toEqual({ type: 'ready', v: 1 });
    expect(p.sent.filter((m) => m.type === 'ready')).toHaveLength(1);
    expect(p.sent.filter((m) => m.type === 'answers')).toHaveLength(0);
  });

  it('posts the full answers set on every radio and note change', async () => {
    const p = await load('plan');
    pick(p, 'format', 'a');
    expect(p.sent.at(-1)).toEqual({
      type: 'answers', v: 1, answers: { format: 'a', track: 'standard' }, submit: null,
    });
    pick(p, 'limit', 'b');
    note(p, 'limit', 'cap at 10k');
    expect(p.sent.at(-1)).toEqual({
      type: 'answers', v: 1,
      answers: { format: 'a', limit: 'b', 'limit.note': 'cap at 10k', track: 'standard' },
      submit: null,
    });
    pick(p, 'track', 'security');
    expect(p.sent.at(-1)!.answers.track).toBe('security');
  });

  it('applies a restore from the parent', async () => {
    const p = await load('plan');
    deliver(p, { type: 'restore', v: 1, answers: { format: 'b', 'limit.note': 'hello', track: 'security' } }, p.parentWin);
    expect(checked(p, 'format')).toBe('b');
    expect((p.doc.getElementById('note-limit') as HTMLTextAreaElement).value).toBe('hello');
    expect(checked(p, 'track')).toBe('security');
  });

  it('ignores a restore from another window', async () => {
    const p = await load('plan');
    deliver(p, { type: 'restore', v: 1, answers: { format: 'b', 'limit.note': 'x', track: 'security' } }, p.otherWin);
    expect(checked(p, 'format')).toBeUndefined();
    expect((p.doc.getElementById('note-limit') as HTMLTextAreaElement).value).toBe('');
    expect(checked(p, 'track')).toBe('standard');
  });

  it('ignores a message whose v is not 1', async () => {
    const p = await load('plan');
    deliver(p, { type: 'restore', v: 2, answers: { format: 'b' } }, p.parentWin);
    expect(checked(p, 'format')).toBeUndefined();
  });
});
