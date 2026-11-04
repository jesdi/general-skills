# Validation record

Fixture: 2172 tests, 3196 cases, 6757 pairs (tests/layout_page.py, through weigh_dashboard.write_dashboard).

html-bytes: 7,723,012

Nothing is truncated to reduce the size: every test, case and pair stays in the standalone report.

## Map selection latency (R3)

Protocol of design.md "Latency evidence", run by `uv run --with playwright python skills/test-weigh-in/tests/latency_probe.py <out-dir>`:
the page is opened from disk in a real browser with no CPU throttling. Each time runs from the click dispatch on a dot to
the second animation frame after it, the first frame painted with the new name, highlights and neighbours, so it includes
up to one frame interval of waiting for vsync.

- Machine: Apple M4, macOS 15.6.1 arm64
- Browser: Chromium 156.0.8078.80 (Playwright; installed Chrome first, bundled Chromium as fallback)
- Fixture: 2,172 dots; tests/hub/test_hub.py::test_hub has 148 neighbours and alternates with test_case_0005 (1 neighbour)
- Date: 2026-11-04

| Measure | Result |
|---|---|
| 20 alternating selections | 32.7–34.4 ms each, max 34.4 ms; every one showed its name and neighbours (148 or 1) |
| Cold map opening (Map tab click to first frame with all 2,172 dots) | 4,562 ms |

Every measured selection is under 200 ms on this machine. R3 counts as met only once the operator agrees this is the
benchmark machine. Cold opening is recorded apart from selection, as design.md asks: the one-time dot placement
(`place()` in dashboard.html, quadratic) takes about 4.6 s at this size, which is above 200 ms.
