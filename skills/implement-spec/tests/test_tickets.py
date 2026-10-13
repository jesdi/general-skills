"""Ticket parser acceptance tests, through driver_tickets.parse only."""
from pathlib import Path

import pytest

from driver_tickets import parse


def body(number, title, blocked, *, seam="`export_csv()`", touches="`demo/export.py: export_csv`", extra=()):
    lines = [f"# {number}: {title}", "", "**What to build:** Something.", "",
             f"**Blocked by:** {blocked}", "", f"**Seam:** {seam}", ""]
    if touches:
        lines += [f"**Touches:** {touches}", ""]
    for line in extra:
        lines += [line, ""]
    return "\n".join(lines + ["- [ ] It works", ""])


def write_set(root, files):
    root.mkdir(parents=True)
    for name, text in files.items():
        (root / name).write_text(text)
    return root


@pytest.mark.parametrize("blocked", [
    "01", "01, 03", "Export rows", "01 (Export rows)", "01: Export rows", "01-export-rows.md", "Export rows, 03",
])
def test_blocked_by_accepts_template_forms(tmp_path, blocked):
    root = write_set(tmp_path / "t", {
        "01-export-rows.md": body("01", "Export rows", "None (can start immediately)"),
        "03-cli-flag.md": body("03", "CLI flag", "None (can start immediately)"),
        "04-more.md": body("04", "More", blocked),
    })
    tickets, errors = parse(root)
    assert errors == []
    wanted = {"01": ["01"], "01, 03": ["01", "03"], "Export rows, 03": ["01", "03"]}.get(blocked, ["01"])
    assert sorted(next(t for t in tickets if t.number == "04").blocked_by) == wanted


def test_none_means_no_blockers(tmp_path):
    root = write_set(tmp_path / "t", {"01-a.md": body("01", "A", "None (can start immediately)")})
    tickets, errors = parse(root)
    assert errors == [] and [t.blocked_by for t in tickets] == [[]]


def test_risk_and_suspicions_are_optional_and_exposed(tmp_path):
    root = write_set(tmp_path / "t", {
        "01-a.md": body("01", "A", "None (can start immediately)",
                        extra=["**Risk:** persistence", "**Suspicions:** a sibling caller keys on the old name"]),
        "02-b.md": body("02", "B", "01"),
    })
    tickets, errors = parse(root)
    assert errors == []
    first, second = sorted(tickets, key=lambda t: t.number)
    assert first.risk == "persistence"
    assert first.suspicions == "a sibling caller keys on the old name"
    assert second.risk is None and second.suspicions is None


def test_missing_touches_error_names_file_and_seam_line(tmp_path):
    root = write_set(tmp_path / "t", {
        "01-a.md": body("01", "A", "None (can start immediately)"),
        "02-quote-fields.md": body("02", "Quote fields", "01", touches=None),
        "03-c.md": body("03", "C", "None (can start immediately)"),
    })
    tickets, errors = parse(root)
    assert tickets == []
    (error,) = errors
    assert error.startswith("02-quote-fields.md")
    assert "missing Touches" in error
    assert " 7" in error or ":7" in error  # the Seam line of the fixture ticket
