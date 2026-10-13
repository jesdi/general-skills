"""The answers file `.agent/implement-answers.json`, in the shape of skills/review-page/schema.md.

    {"v": 1, "stage": "implement", "submitted": "changes", "revision": <decisions.json revision>,
     "answers": {<question id>: <option id>, "<question id>.note": <text>}}
"""
from __future__ import annotations

import json
from pathlib import Path

FILE = Path(".agent/implement-answers.json")


def _offered(document: dict) -> dict[str, list[str]]:
    """question id -> its option ids, for the open questions."""
    return {q["id"]: [option["id"] for option in q["options"]] for q in document["questions"]}


def pair(text: str) -> tuple[str, str]:
    """One `--answer <question id>=<option id>`."""
    question, equals, option = text.partition("=")
    if not (question and equals and option):
        raise ValueError(text)
    return question, option


def given(pairs: list[tuple[str, str]], document: dict) -> list[str]:
    """Write the answers file for `--answer`, bound to `document`. Returns the usage errors; then no file is written."""
    offered = _offered(document)
    errors = [f"--answer {question}={option}: " + (f"{question} offers {', '.join(offered[question])}"
                                                   if question in offered else f"no open question {question}")
              for question, option in pairs if option not in offered.get(question, [])]
    if pairs and not errors:
        FILE.write_text(json.dumps({"v": 1, "stage": "implement", "submitted": "changes",
                                    "revision": document["revision"], "answers": dict(pairs)}, indent=1) + "\n")
    return errors


def _unbound(file: object, revision: str) -> str:
    """Why no answer of the file counts; "" when the file is a submission for this revision."""
    if not isinstance(file, dict) or not isinstance(file.get("answers"), dict):
        return "it is not an object with an `answers` object"
    if (file.get("v"), file.get("stage")) != (1, "implement"):
        return "it is not a version 1 file of the implement stage"
    if not file.get("submitted"):
        return "it is a draft: `submitted` is not set"
    if file.get("revision") != revision:
        return (f"its revision {str(file.get('revision'))[:12]} is not the revision {revision[:12]} of the "
                "decisions file: the questions changed after it was written")
    return ""


def _each(given: dict, offered: dict[str, list[str]]) -> tuple[dict, list[str]]:
    """(the answers and notes that count, a sentence per ignored one)."""
    answers, ignored = {}, []
    for key, value in given.items():
        question = key.removesuffix(".note")
        if question not in offered:
            ignored.append(f"The answer for {key} in {FILE} is ignored: there is no open question {question}.")
        elif key == question and value not in offered[question]:
            ignored.append(f"The answer {value!r} for {question} in {FILE} is ignored: the question offers "
                           f"{', '.join(offered[question])}.")
        elif isinstance(value, str):
            answers[key] = value
    return answers, ignored


def read(document: dict) -> tuple[dict, list[str]]:
    """(the answers that count, a ledger sentence per ignored thing). With no open question nothing is read."""
    offered = _offered(document)
    if not (offered and FILE.exists()):
        return {}, []
    try:
        file = json.loads(FILE.read_text())
    except (OSError, ValueError) as exc:
        return {}, [f"The answers file {FILE} is ignored: it is not readable JSON: {exc}."]
    if why := _unbound(file, document["revision"]):
        return {}, [f"The answers file {FILE} is ignored: {why}."]
    return _each(file["answers"], offered)
