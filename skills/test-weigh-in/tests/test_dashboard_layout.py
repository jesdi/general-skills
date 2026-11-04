"""Ticket 04 of specs/test-weigh-in-dashboard-polish: readable layout, visible tabs and the offline report (R8, R9, R11, R12).

Seam: weigh_dashboard.write_dashboard (via layout_page.py, the real-suite fixture: 2,172 tests, 3,196 cases, 6,757 pairs).
The returned standalone HTML is opened from disk in Chrome with the network off (browser_driver.py, run by `uv run
--with playwright`, needs Chrome or a Playwright chromium) and its visible controls are driven. Latency is not asserted:
the 200 ms claim needs the benchmark machine the operator agrees (design.md).
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from fixture_repo import uv

HERE = Path(__file__).resolve().parent
SPEC = HERE.parents[2] / "specs" / "test-weigh-in-dashboard-polish"
TABS = ["Metrics", "Overlaps", "Map"]


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    out = tmp_path_factory.mktemp("layout04")
    built = subprocess.run([sys.executable, str(HERE / "layout_page.py"), str(out)], capture_output=True, text=True, timeout=600)
    assert built.returncode == 0, built.stderr
    html = Path(built.stdout.strip())
    drove = subprocess.run([uv(), "run", "--with", "playwright", "python", str(HERE / "browser_driver.py"), str(html)],
                           capture_output=True, text=True, timeout=600)
    assert drove.returncode == 0, drove.stderr[-3000:]
    return {"bytes": html.stat().st_size, **json.loads(drove.stdout)}


def only_active(tabs, label):
    """Exactly one tab looks different from the rest, and it is `label`."""
    sigs = [t["sig"] for t in tabs]
    odd = [t["text"] for t in tabs if sigs.count(t["sig"]) == 1]
    assert odd == [label], f"visibly active tabs: {odd}, selected {label}"
    assert len({t["sig"] for t in tabs if t["text"] != label}) == 1, "the inactive tabs do not look alike"


def test_the_active_tab_follows_selection_and_keyboard_focus_shows(report):
    for label in TABS:
        only_active(report["tabs"]["steps"][label], label)
    focus = report["tabs"]["focus"]
    ring = not focus["outline"].startswith("none") and not focus["outline"].split()[1] == "0px"
    assert ring or focus["sig_focused"] != focus["sig_unfocused"], f"no visible focus state: {focus}"


@pytest.mark.parametrize("view", ["desktop", "narrow"])
def test_gauges_align_and_partial_status_stays_readable(report, view):
    gauges = report[view]["gauges"]
    assert len(gauges) == 5
    for g in gauges:
        assert g["title"] and g["status"], "every gauge has a gauge-title and a gauge-status, a fully measured one too"
        assert g["status"]["text"], "a fully measured pillar still states its measurement status"
        assert not g["title"]["clipped"] and not g["status"]["clipped"]
        assert g["status"]["font"] >= 11 and g["status"]["lines"] <= 2, f"partial status unreadable: {g['status']}"
    rows = {}
    for g in gauges:
        rows.setdefault(g["card"]["top"], []).append(g)
    for row in rows.values():
        for part in ("title", "score", "status"):
            assert len({g[part]["top"] for g in row}) == 1, f"{part} not aligned across a row at {view}: {[g[part]['top'] for g in row]}"
    refactoring = next(g for g in gauges if "refactoring" in g["title"]["text"].lower())
    assert "0 of 4" in refactoring["status"]["text"]
    assert report[view]["overview"]["scroll_width"] <= report[view]["overview"]["viewport"], "the page overflows sideways"


@pytest.mark.parametrize("view", ["desktop", "narrow"])
def test_details_use_the_available_width_and_keep_full_identities(report, view):
    r = report[view]
    for key in ("feedback_details", "refactoring_details", "overlaps", "map"):
        assert r[key]["scroll_width"] <= r[key]["viewport"], f"{key} overflows the viewport"
        assert r[key]["details_right"] <= r[key]["viewport"]
    assert r["feedback_table_share"] >= 0.85, "the metrics table leaves the right-hand region unused"
    assert r["overlap_ids"]
    for ident in r["overlap_ids"]:
        assert ident["text"].endswith("x" * 40), "identity is not shown in full"
        assert ident["right"] <= r["overlaps"]["viewport"] and not ident["clipped"] and not ident["ellipsis"]


def test_offline_from_disk_keeps_the_evidence(report):
    j = report["journey"]
    assert j["requests"] == [] and j["failed_requests"] == [] and j["page_errors"] == []
    assert "No metrics were measured" in j["refactoring_text"] and "Total runtime" in j["feedback_text"]
    assert "6,757" in j["count_all"] and j["first_row_page_1"] != j["first_row_page_2"]
    assert re.search(r"\b1\b", j["count_search"]) and j["rows_search"] == 1 and "6,757" in j["count_cleared"]
    assert j["dots"] == 2172 and "test_module_0010" in j["picked"] and j["similar_items"] > 0
    assert "Anchors" in j["metrics_text"]
    only_active(j["active_overlaps"], "Overlaps")
    only_active(j["active_map"], "Map")
    only_active(j["active_metrics"], "Metrics")


def test_the_size_of_the_real_suite_report_is_recorded_without_truncating_it(report):
    record = (SPEC / "validation.md").read_text()
    assert "2172 tests, 3196 cases, 6757 pairs" in record
    recorded = int(re.search(r"html-bytes:\s*([\d,]+)", record).group(1).replace(",", ""))
    assert report["bytes"] / 2 <= recorded <= report["bytes"] * 2, f"recorded {recorded}, this fixture renders {report['bytes']}"
    assert report["bytes"] > 1_000_000
    assert "6,757" in report["journey"]["count_all"] and report["journey"]["dots"] == 2172
