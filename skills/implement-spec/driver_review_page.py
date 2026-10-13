"""The review page of the open questions: decisions.json in, HTML out.

The page is the template of the review-page skill in questionnaire mode with its slots filled. Every text is escaped.
"""
from __future__ import annotations

import re
from html import escape

import driver_agents

TEMPLATE = driver_agents.SKILL.parent / "review-page" / "template.html"
SLOTS = ("header", "tickets", "questions", "corrections", "track")


def _option(question: str, option: dict, recommended: bool) -> str:
    name, chip = escape(option["id"]), ' <span class="chip rec">recommended</span>' * recommended
    return (f'<label class="opt"><input type="radio" name="{question}" value="{name}"><span><span class="t">{name}'
            f'{chip}</span><br><span class="d">{escape(option["description"])}</span></span></label>')


def _question(q: dict) -> str:
    name = escape(q["id"])
    options = "\n".join(_option(name, option, option["id"] == q["recommended"]) for option in q["options"])
    evidence = " · ".join(f"<code>{escape(pointer)}</code>" for pointer in q["evidence"])
    return (f'<div class="q" data-q="{name}">\n'
            f'<header><h3>{escape(q["title"])}</h3><span class="chip waiting">needs you</span></header>\n'
            f'<p class="why">{escape(q["why"])}</p>\n<p class="why">Evidence: {evidence}</p>\n'
            f'<div class="opts">\n{options}\n</div>\n'
            f'<textarea id="note-{name}" placeholder="Note (optional)"></textarea>\n</div>')


def _filled(page: str, pattern: str, new: str) -> str:
    """`page` with the one match of `pattern` replaced; ValueError when the template has not exactly one."""
    page, count = re.subn(pattern, lambda match: new, page, flags=re.S)
    if count != 1:
        raise ValueError(f"{TEMPLATE} has {count} matches of {pattern}, not 1")
    return page


def render(decisions: dict) -> str:
    """The page for decisions.json. Raises OSError with no template, ValueError when it has another shape."""
    questions = decisions["questions"]
    header = (f'<div class="eyebrow">implement-spec · decisions {escape(decisions["revision"][:12])}</div>\n'
              f"<h1>{len(questions)} open question{'s' * (len(questions) != 1)} of the implementation</h1>\n"
              '<p class="lede">Pick an option under each question, then send.</p>')
    slots = {"header": header, "questions": "\n".join(["<h2>Open questions</h2>", *map(_question, questions)])}
    page = _filled(TEMPLATE.read_text(), r'<body data-mode="[a-z]*">', '<body data-mode="questionnaire">')
    for slot in SLOTS:
        page = _filled(page, rf"(?<=<!-- slot: {slot} -->).*?(?=<!-- /slot -->)", f"\n{slots.get(slot, '')}\n")
    return page
