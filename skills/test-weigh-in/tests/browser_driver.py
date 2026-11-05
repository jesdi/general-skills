"""Ticket 04 browser driver: `uv run --with playwright python browser_driver.py <dashboard.html> [--gauges]` opens the
standalone report from disk in Chrome with the network switched off, drives the visible controls and prints one JSON
document of measurements on stdout; with --gauges it measures only the overview gauges at both viewports. It asserts
nothing: test_dashboard_layout.py reads the measurements.

Page interface the acceptance tests pin, on top of the earlier tickets' markup (metric/overlap/map tabs):
  [data-testid=gauge-title]   inside every [data-testid=gauge]: the pillar title
  [data-testid=gauge-status]  inside every gauge, also a fully measured one: the measurement status text
  [data-testid=gauge] .score  the displayed score (existing)
  the tabs are the <button>s inside [data-testid=details]; the selected one must look different from the others.
"""
import json
import sys
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

URL = "file://" + quote(str(Path(sys.argv[1]).resolve()))
UNIQUE = "test_only_left_xylophone"
GAUGE = "[data-testid=gauge]"
TABS = "[data-testid=details] button"

GAUGES = """() => [...document.querySelectorAll('[data-testid=gauge]')].map((g) => {
  const box = (e) => { if (!e) return null; const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return {top: Math.round(r.top), left: Math.round(r.left), width: Math.round(r.width), height: Math.round(r.height),
            lines: Math.round(r.height / (parseFloat(s.lineHeight) || 1.2 * parseFloat(s.fontSize))), font: parseFloat(s.fontSize),
            clipped: e.scrollWidth > e.clientWidth + 1 || e.scrollHeight > e.clientHeight + 1, text: e.textContent.trim()}; };
  const status = g.querySelector('[data-testid=gauge-status]'), lh = (e) => parseFloat(getComputedStyle(e).lineHeight);
  // the lines each reason of the status spans: an inline one breaks into rects, an inline-block one grows taller
  const reasons = status ? [...status.children].map((s) => Math.max(s.getClientRects().length, Math.round(s.getBoundingClientRect().height / lh(s)))) : [];
  return {card: box(g), title: box(g.querySelector('[data-testid=gauge-title]')), score: box(g.querySelector('.score')),
          status: box(status), reasons};
})"""

SIG = """(el) => { const s = getComputedStyle(el);
  return [s.backgroundColor, s.color, s.fontWeight, s.borderTopColor, s.borderBottomColor, s.borderBottomWidth, s.textDecorationLine,
          s.boxShadow].join('|'); }"""
FOCUS = """() => { const el = document.activeElement, s = getComputedStyle(el);
  return {text: el.textContent.trim(), outline: s.outlineStyle + ' ' + s.outlineWidth + ' ' + s.outlineColor, shadow: s.boxShadow}; }"""
TAB_STATE = """() => { const tabs = [...document.querySelectorAll('[data-testid=details] button')];
  return tabs.filter((b) => ['Metrics', 'Worst tests', 'Smells', 'Overlaps', 'Map'].includes(b.textContent.trim())).map((b) => ({text: b.textContent.trim(), sig: window.sigOf(b)})); }"""
OVERFLOW = """() => ({viewport: document.documentElement.clientWidth, scroll_width: document.documentElement.scrollWidth,
  details_right: Math.round(Math.max(...[...document.querySelectorAll('[data-testid=details] > *')].map((e) => e.getBoundingClientRect().right))),
  details_width: Math.round(document.querySelector('[data-testid=details]').getBoundingClientRect().width)})"""


def click_gauge(page, label):
    page.locator(GAUGE).filter(has_text=label).first.click()


def click_tab(page, label):
    page.locator(TABS).filter(has_text=label).first.click()


def tabs(page):
    return page.evaluate(TAB_STATE)


def table_share(page):
    return page.evaluate("""() => { const t = document.querySelector('[data-testid=details] table'), d = document.querySelector('[data-testid=details]');
      return t ? t.getBoundingClientRect().width / d.getBoundingClientRect().width : null; }""")


def overview(browser, width, height):
    page = browser.new_page(viewport={"width": width, "height": height})
    page.goto(URL)
    page.wait_for_selector(GAUGE)
    out = {"gauges": page.evaluate(GAUGES), "overview": page.evaluate(OVERFLOW)}
    page.close()
    return out


def layout(browser, width, height):
    page = browser.new_page(viewport={"width": width, "height": height})
    page.goto(URL)
    page.wait_for_selector(GAUGE)
    out = {"gauges": page.evaluate(GAUGES), "overview": page.evaluate(OVERFLOW)}
    click_gauge(page, "Fast feedback")
    out["feedback_table_share"] = table_share(page)
    out["feedback_details"] = page.evaluate(OVERFLOW)
    click_gauge(page, "Resistance to refactoring")
    out["refactoring_details"] = page.evaluate(OVERFLOW)
    click_gauge(page, "Economy")
    click_tab(page, "Overlaps")
    out["overlaps"] = page.evaluate(OVERFLOW)
    out["overlap_ids"] = page.evaluate("""() => [...document.querySelectorAll('[data-testid=overlap-test]')].map((e) => {
      const r = e.getBoundingClientRect(), s = getComputedStyle(e);
      return {text: e.textContent, right: Math.round(r.right), clipped: e.scrollWidth > e.clientWidth + 1 && s.overflowX !== 'visible',
              ellipsis: s.textOverflow === 'ellipsis', title: e.getAttribute('title')}; })""")
    click_tab(page, "Map")
    out["map"] = page.evaluate(OVERFLOW)
    page.close()
    return out


def tab_styles(browser):
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(URL)
    page.wait_for_selector(GAUGE)
    page.evaluate("() => { window.sigOf = " + SIG + "; }")
    click_gauge(page, "Economy")
    steps = {}
    for label in ("Metrics", "Overlaps", "Map"):
        click_tab(page, label)
        steps[label] = tabs(page)
    # keyboard focus: from the active Map tab, Shift+Tab lands on a neighbouring tab
    before = {t["text"]: t["sig"] for t in tabs(page)}
    page.keyboard.press("Shift+Tab")
    focus = page.evaluate(FOCUS)
    focus["sig_unfocused"] = before.get(focus["text"])
    focus["sig_focused"] = page.evaluate("() => window.sigOf(document.activeElement)")
    page.close()
    return {"steps": steps, "focus": focus}


TAB_BOXES = """() => { const row = document.querySelector('[data-testid=details] .tabs'), o = row.getBoundingClientRect();
  return [...row.querySelectorAll('button')].map((b) => { const r = b.getBoundingClientRect();
    return {text: b.textContent.trim(), left: r.left - o.left + row.scrollLeft, width: r.width}; }); }"""


def tab_boxes(browser, width, height):
    """Each tab's unrounded box in its row (scroll-independent) after every tab of Economy is selected in turn."""
    page = browser.new_page(viewport={"width": width, "height": height})
    page.goto(URL)
    page.wait_for_selector(GAUGE)
    click_gauge(page, "Economy")
    labels = [b["text"] for b in page.evaluate(TAB_BOXES)]
    steps = {}
    for label in labels:
        click_tab(page, label)
        steps[label] = page.evaluate(TAB_BOXES)
    out = {"steps": steps, "overview": page.evaluate(OVERFLOW)}
    page.close()
    return out


def journey(browser):
    context = browser.new_context(viewport={"width": 1440, "height": 900}, offline=True)
    requests = []
    context.on("request", lambda r: requests.append(r.url))
    failures = []
    context.on("requestfailed", lambda r: failures.append(r.url))
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(URL)
    page.wait_for_selector(GAUGE)
    page.evaluate("() => { window.sigOf = " + SIG + "; }")
    log = {}
    count = lambda: (page.locator("[data-testid=overlap-count]").first.text_content() or "")
    click_gauge(page, "Fast feedback")
    log["feedback_text"] = page.inner_text("[data-testid=details]")
    click_gauge(page, "Resistance to refactoring")
    log["refactoring_text"] = page.inner_text("[data-testid=details]")
    click_gauge(page, "Economy")
    click_tab(page, "Overlaps")
    log["active_overlaps"] = tabs(page)
    log["count_all"] = count()
    log["first_row_page_1"] = page.locator("[data-testid=overlap-pair]").first.text_content()
    page.locator("[data-testid=overlap-next]").click()
    log["first_row_page_2"] = page.locator("[data-testid=overlap-pair]").first.text_content()
    page.locator("[data-testid=overlap-search]").fill(UNIQUE)
    page.wait_for_timeout(300)
    log["count_search"] = count()
    log["rows_search"] = page.locator("[data-testid=overlap-pair]").count()
    page.locator("[data-testid=overlap-search]").fill("")
    page.wait_for_timeout(300)
    log["count_cleared"] = count()
    click_tab(page, "Map")
    log["active_map"] = tabs(page)
    dot = page.locator("[data-testid=map-dot]").nth(10)
    log["dots"] = page.locator("[data-testid=map-dot]").count()
    dot.dispatch_event("click")
    page.wait_for_timeout(300)
    log["picked"] = (page.locator("[data-testid=map-picked]").first.text_content() or "")
    log["similar_items"] = page.locator("[data-testid=map-similar-item]").count()
    click_tab(page, "Metrics")
    log["active_metrics"] = tabs(page)
    log["metrics_text"] = page.inner_text("[data-testid=details]")
    log["requests"] = [u for u in requests if not u.startswith("file:")]
    log["failed_requests"] = failures
    log["page_errors"] = errors
    context.close()
    return log


with sync_playwright() as p:
    try:
        browser = p.chromium.launch(channel="chrome")
    except Exception:
        browser = p.chromium.launch()
    if sys.argv[2:] == ["--gauges"]:
        result = {"desktop": overview(browser, 1440, 900), "narrow": overview(browser, 390, 844)}
    else:
        result = {"desktop": layout(browser, 1440, 900), "narrow": layout(browser, 390, 844), "tabs": tab_styles(browser),
                  "tab_boxes": {"desktop": tab_boxes(browser, 1440, 900), "narrow": tab_boxes(browser, 390, 844)},
                  "journey": journey(browser)}
    browser.close()
print(json.dumps(result))
