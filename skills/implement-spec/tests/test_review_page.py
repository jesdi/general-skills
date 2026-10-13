"""Ticket 05 acceptance tests for `driver_review_page.render(decisions) -> html`.

Interface choice: `decisions` is the dict of decisions.json (`revision`, `questions[]` with `id`, `title`, `why`,
`evidence` (list of strings), `options` (list of `{id, description}`), `recommended` (an option id)); the result is the
whole page as a string. The checks are only those that skills/review-page/SKILL.md and schema.md state.
"""
import re
from pathlib import Path

import driver_review_page

REVIEW_PAGE = Path(__file__).resolve().parents[2] / "review-page"
TEMPLATE = (REVIEW_PAGE / "template.html").read_text()
ID = re.compile(r"[a-z0-9_-]{1,64}")
WHAT = "<b>&\"quoted\"</b>"


def question(qid, options, recommended, title=None):
    return {"id": qid, "title": title or f"Title of {qid}", "why": f"Why {qid} matters",
            "evidence": [".agent/implement/01/implementer.json", ".agent/implement/ledger.md:3"],
            "options": [{"id": o, "description": f"Trade-off of {o}"} for o in options], "recommended": recommended}


DECISIONS = {"revision": "ab" * 32, "questions": [
    question("01-locked-test-1", ["overrule-test", "overrule-implementer"], "overrule-implementer"),
    question("02-finding-1", ["accept", "split", "redesign", "other"], "split", title=WHAT)]}


def blocks(html):
    """(data-q value, block html) per question block, in page order."""
    return re.findall(r'<div class="q" data-q="([^"]*)">(.*?)</textarea>', html, re.S)


def check_page(html, decisions):
    """Every check the review-page skill states, for a page in questionnaire mode."""
    questions = decisions["questions"]
    assert 'name="agent-ops-review"' in html and 'content="1"' in html, "the dispatcher looks for the marker"
    assert '<body data-mode="questionnaire"' in html
    assert html.count('data-q="') == len(questions) == len(blocks(html)), "one data-q block per question"
    for style_or_script in re.findall(r"<(?:style|script)>.*?</(?:style|script)>", TEMPLATE, re.S):
        assert style_or_script in html, "script and CSS are never edited"
    for slot in ("tickets", "questions", "corrections", "track"):
        assert f'<section data-slot="{slot}">' in html, "the wrappers stay"
    for forbidden in ("<link", "src=", "http:", "https:", "<button", 'data-q="track"', 'data-q="format"'):
        assert forbidden not in html, forbidden
    for (qid, block), want in zip(blocks(html), questions):
        assert qid == want["id"] and ID.fullmatch(qid), qid
        assert f'id="note-{qid}"' in block and 'id="state"' not in block, "the script owns id state"
        radios = re.findall(r'<input type="radio" name="([^"]*)" value="([^"]*)"', block)
        assert radios == [(qid, o["id"]) for o in want["options"]], "name is the question id, value is the option id"
        labels = block.split('<label class="opt">')[1:]
        recommended = [i for i, label in enumerate(labels) if "chip rec" in label]
        assert block.count("chip rec") == 1 and len(recommended) == 1, "exactly one recommended option"
        assert f'value="{want["recommended"]}"' in labels[recommended[0]]


def test_the_page_carries_the_marker_questionnaire_mode_and_one_block_per_question():
    check_page(driver_review_page.render(DECISIONS), DECISIONS)


def test_titles_whys_and_descriptions_show_up_and_markup_in_them_is_escaped():
    html = driver_review_page.render(DECISIONS)
    for want in DECISIONS["questions"]:
        assert f"Why {want['id']} matters" in html
        assert all(f"Trade-off of {o['id']}" in html for o in want["options"])
    assert "Title of 01-locked-test-1" in html
    assert WHAT not in html and "&lt;b&gt;" in html, "a title is text, never markup"
