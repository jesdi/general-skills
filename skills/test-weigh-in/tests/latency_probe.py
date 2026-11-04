"""Ticket 03 latency evidence (R3, design.md "Latency evidence"): `uv run --with playwright python latency_probe.py <out-dir>`.

Writes a 2,172-dot map (test_hub has 148 neighbours) through weigh_dashboard.write_dashboard, opens it from disk in Chrome
with no CPU throttling, and prints one JSON document: machine, OS, browser version, fixture, the cold map opening (Map tab
click to the first frame painted with every dot) and 20 alternating selections (dot click to the first frame painted with
the new name, highlights and neighbours). It asserts nothing: the 200 ms claim needs the benchmark machine the operator agrees.
"""
import json
import platform
import random
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import map_fixture
from playwright.sync_api import sync_playwright

N, NEAR = 2172, 148
HUB, OTHER = "tests/hub/test_hub.py::test_hub", "tests/scale/test_module_0005.py::test_case_0005"
FIXTURE = f"{N} dots, {HUB} with {NEAR} neighbours, alternating with {OTHER}"

FRAME = "() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))"
COLD = """async () => {
  const t0 = performance.now();
  [...document.querySelectorAll('[data-testid=details] button')].find((b) => b.textContent.trim() === 'Map').click();
  await new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)));
  return {ms: performance.now() - t0, dots: document.querySelectorAll('[data-testid=map-dot]').length};
}"""
SELECT = """async ([hub, other]) => {
  const out = [];
  for (let i = 0; i < 20; i++) {
    const target = i % 2 === 0 ? hub : other;
    const dot = document.querySelector(`[data-testid=map-dot][data-test="${target}"]`);
    const t0 = performance.now();
    dot.dispatchEvent(new MouseEvent('click', {bubbles: true}));
    await new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)));
    const ms = performance.now() - t0;
    const shown = document.querySelector('[data-testid=map-picked]').textContent.endsWith(target);
    out.push({target, ms, shown, neighbours: document.querySelectorAll('[data-testid=map-similar-item]').length});
  }
  return out;
}"""


def page(out):
    map_fixture.build(out)  # a write_dashboard page whose map is replaced by the scaled one
    html = out / "dashboard.html"
    text = html.read_text()
    head, rest = text.split("const data = ", 1)
    end = rest.index(";\n")
    data = json.loads(rest[:end])
    names = [HUB] + [f"tests/scale/test_module_{i:04d}.py::test_case_{i:04d}" for i in range(1, N)]
    similar = {n: [] for n in names}
    for k in range(1, NEAR + 1):
        s = 0.7 + (k * 7 % 19) / 100
        similar[HUB].append([names[k], s])
        similar[names[k]].append([HUB, s])
    rnd = random.Random(7)
    data["map"] = [{"test": n, "x": rnd.random(), "y": rnd.random(), "runtime": 0.01, "unique": 0, "similar": similar[n]} for n in names]
    html.write_text(head + "const data = " + json.dumps(data).replace("</", "<\\/") + rest[end:])
    return html


def machine():
    if sys.platform == "darwin":
        return subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()
    return platform.processor() or platform.machine()


def main(out):
    html = page(out)
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome")
        except Exception:
            browser = p.chromium.launch()
        tab = browser.new_page(viewport={"width": 1440, "height": 900})
        tab.goto("file://" + quote(str(html.resolve())))
        tab.locator("[data-testid=gauge]").filter(has_text="Economy").first.click()
        tab.evaluate(FRAME)
        cold = tab.evaluate(COLD)
        selections = tab.evaluate(SELECT, [HUB, OTHER])
        result = {"machine": machine(), "os": platform.platform(), "browser": f"{browser.browser_type.name} {browser.version}",
                  "fixture": FIXTURE, "cold_map_opening_ms": round(cold["ms"]), "dots": cold["dots"],
                  "selections_ms": [round(s["ms"], 1) for s in selections], "max_selection_ms": round(max(s["ms"] for s in selections), 1),
                  "all_shown": all(s["shown"] for s in selections),
                  "neighbours": sorted({s["neighbours"] for s in selections})}
        browser.close()
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
