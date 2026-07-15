import rank


def test_parse_blocked_by_basic():
    assert rank.parse_blocked_by("Blocked by: #12, #34") == [12, 34]


def test_parse_blocked_by_case_insensitive_and_multiline():
    body = "Some idea.\n\nblocked BY: #5\nMore text\nBlocked by: #5, #9"
    assert rank.parse_blocked_by(body) == [5, 9]  # deduped + sorted


def test_parse_blocked_by_none_and_empty():
    assert rank.parse_blocked_by(None) == []
    assert rank.parse_blocked_by("no blockers here") == []


def test_merge_sources_joins_fields_and_bodies():
    project_items = [
        {"content": {"number": 12, "title": "Add X", "url": "u/12"},
         "status": "Ready", "impact": 4, "effort": 2, "area": "feature"},
        {"content": {"number": 34, "title": "Do Y", "url": "u/34"}},  # untriaged, no fields
        {"title": "a draft note"},  # draft item, no content -> skipped
    ]
    issue_rows = [
        {"number": 12, "body": "Blocked by: #34", "state": "open"},
        {"number": 34, "body": "loose idea", "state": "open"},
    ]
    out = rank.merge_sources(project_items, issue_rows)
    assert [i["number"] for i in out] == [12, 34]
    twelve = out[0]
    assert twelve["state"] == "OPEN"
    assert twelve["impact"] == 4 and twelve["effort"] == 2
    assert twelve["area"] == "feature" and twelve["status"] == "Ready"
    assert twelve["body"] == "Blocked by: #34"
    thirtyfour = out[1]
    assert thirtyfour["impact"] is None and thirtyfour["status"] is None


def test_merge_sources_missing_body_row_defaults_open_empty():
    out = rank.merge_sources(
        [{"content": {"number": 7, "title": "T", "url": "u/7"}, "impact": 3, "effort": 1}],
        [],  # no matching issue row
    )
    assert out[0]["state"] == "OPEN" and out[0]["body"] == ""


def _issue(number, body="", state="OPEN", status=None):
    return {"number": number, "title": f"i{number}", "url": "", "state": state,
            "body": body, "status": status, "impact": None, "effort": None, "area": None}


def test_availability_and_blockers():
    a = _issue(1, body="Blocked by: #2, #3, #4")
    by_number = {
        1: a,
        2: _issue(2, state="CLOSED"),          # satisfied — closed
        3: _issue(3, state="OPEN", status="Done"),  # satisfied — Status Done
        4: _issue(4, state="OPEN", status="Ready"),  # NOT satisfied — still open/active
    }
    assert rank.is_available(a, by_number) is False
    assert rank.blockers_of(a, by_number) == [4]


def test_available_when_no_blockers():
    a = _issue(1, body="just an idea")
    assert rank.is_available(a, {1: a}) is True
    assert rank.blockers_of(a, {1: a}) == []


def test_unknown_blocker_is_dropped_edge():
    a = _issue(1, body="Blocked by: #999")  # #999 not in the graph
    assert rank.is_available(a, {1: a}) is True   # dropped, not blocking
    assert rank.blockers_of(a, {1: a}) == []


def _scored(number, impact, effort, body="", state="OPEN", status="Ready"):
    return {"number": number, "title": f"i{number}", "url": "", "state": state,
            "body": body, "status": status, "impact": impact, "effort": effort, "area": None}


def test_score_ratio_and_unscored():
    assert rank.score(_scored(1, 4, 2)) == 2.0
    assert rank.score(_scored(1, None, 2)) is None
    assert rank.score(_scored(1, 4, None)) is None
    assert rank.score(_scored(1, 4, 0)) is None  # guard divide-by-zero


def test_rank_orders_available_and_separates_blocked_and_done():
    issues = [
        _scored(1, 2, 2),                       # score 1.0
        _scored(2, 5, 1),                       # score 5.0  -> rank 1
        _scored(3, None, None),                 # unscored -> last, needs triage
        _scored(4, 9, 1, body="Blocked by: #2"),  # blocked (2 is open/Ready)
        _scored(5, 4, 1, status="Done"),        # Done -> excluded entirely
    ]
    result = rank.rank_issues(issues)
    assert [i["number"] for i in result["available"]] == [2, 1, 3]
    assert [i["number"] for i in result["blocked"]] == [4]
    assert 5 not in [i["number"] for i in result["available"] + result["blocked"]]


def test_rank_excludes_closed():
    issues = [_scored(1, 3, 1, state="CLOSED"), _scored(2, 1, 1)]
    result = rank.rank_issues(issues)
    assert [i["number"] for i in result["available"]] == [2]


def test_render_contains_ranks_scores_flags_and_blocked_reason():
    issues = [
        _scored(2, 5, 1),                          # score 5.00, rank 1
        _scored(3, None, None),                    # needs triage
        _scored(4, 9, 1, body="Blocked by: #2"),   # blocked on #2
    ]
    text = rank.render(rank.rank_issues(issues))
    assert "#2 i2" in text
    assert "5.00" in text
    assert "[needs triage]" in text
    assert "Blocked" in text and "waiting on #2" in text


def test_render_no_blocked_section_when_none_blocked():
    text = rank.render(rank.rank_issues([_scored(1, 2, 1)]))
    assert "Blocked" not in text


def test_rank_separates_in_progress_claimed():
    issues = [
        _scored(1, 2, 2),                            # score 1.0, available
        _scored(2, 5, 1, status="In progress"),      # claimed -> never recommended
    ]
    result = rank.rank_issues(issues)
    assert [i["number"] for i in result["available"]] == [1]
    assert [i["number"] for i in result["in_progress"]] == [2]
    assert result["blocked"] == []


def test_in_progress_with_blockers_goes_to_claimed_not_blocked():
    issues = [
        _scored(1, 3, 1),
        _scored(2, 4, 1, status="In progress", body="Blocked by: #1"),
    ]
    result = rank.rank_issues(issues)
    assert [i["number"] for i in result["in_progress"]] == [2]
    assert result["blocked"] == []


def test_in_progress_item_still_blocks_its_dependents():
    # A claimed blocker is not yet Done -> dependents stay blocked.
    issues = [
        _scored(1, 3, 1, status="In progress"),
        _scored(2, 4, 1, body="Blocked by: #1"),
    ]
    result = rank.rank_issues(issues)
    assert [i["number"] for i in result["blocked"]] == [2]
    assert [i["number"] for i in result["in_progress"]] == [1]


def test_render_shows_claimed_section_first():
    issues = [_scored(1, 2, 1), _scored(2, 5, 1, status="In progress")]
    text = rank.render(rank.rank_issues(issues))
    assert text.startswith("In progress (claimed):")
    assert "#2 i2" in text


def test_render_no_claimed_section_when_none_in_progress():
    text = rank.render(rank.rank_issues([_scored(1, 2, 1)]))
    assert "claimed" not in text


import os

import pytest


def test_find_project_meta_in_cwd(tmp_path, monkeypatch):
    backlog = tmp_path / ".backlog"
    backlog.mkdir()
    meta_file = backlog / "project-meta.json"
    meta_file.write_text("{}")
    monkeypatch.chdir(tmp_path)
    assert rank.find_project_meta() == str(meta_file)


def test_find_project_meta_walks_up_from_subdir(tmp_path, monkeypatch):
    backlog = tmp_path / ".backlog"
    backlog.mkdir()
    meta_file = backlog / "project-meta.json"
    meta_file.write_text("{}")
    nested = tmp_path / "backend" / "deep"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    assert rank.find_project_meta() == str(meta_file)


def test_find_project_meta_nearest_ancestor_wins(tmp_path, monkeypatch):
    outer = tmp_path / ".backlog"
    outer.mkdir()
    (outer / "project-meta.json").write_text("{}")
    inner_root = tmp_path / "sub"
    inner_backlog = inner_root / ".backlog"
    inner_backlog.mkdir(parents=True)
    inner_meta = inner_backlog / "project-meta.json"
    inner_meta.write_text("{}")
    monkeypatch.chdir(inner_root)
    assert rank.find_project_meta() == str(inner_meta)


def test_find_project_meta_errors_clearly_when_absent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError) as exc:
        rank.find_project_meta()
    assert ".backlog/project-meta.json" in str(exc.value)
    assert "backlog setup" in str(exc.value)
