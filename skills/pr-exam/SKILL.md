---
name: pr-exam
description: >-
  Deterministic rules for a PR exam: validate a generated exam.json against its
  generation request, project the served (answer-free) exam, score an attempt,
  read and write learning records, and validate a repo's .pr-exam.json. Use when
  generating, serving or grading a PR exam, or when the user says "pr-exam".
---

# pr-exam

Every exam rule lives in `pr_exam.py`. Never re-implement one: run the script.
Each command reads files and prints JSON, or exits non-zero with every error on
stderr. Shapes and rules: `specs/pr-exam/design.md`, "Data model".

| Command | Does |
|---|---|
| `python3 pr_exam.py validate --request <request.json> <exam.json>` | Checks a generated exam against its request; prints its summary. |
| `python3 pr_exam.py public <exam.json>` | Prints the exam the page may show: no key, concepts or answers. |
| `python3 pr_exam.py score --exam <exam.json> --answers <answers.json> --grades <grades.json>` | Grades an attempt and names what was missed. |
| `python3 pr_exam.py learned --records <dir> --today <YYYY-MM-DD>` | Lists concepts known, due or wrong. |
| `python3 pr_exam.py record --records <dir> --exam <exam.json> --result <result.json> --today <YYYY-MM-DD>` | Writes learning records for a scored attempt. |
| `python3 pr_exam.py config [<.pr-exam.json>]` | Reads and validates the repo's exam config. |

When you generate an exam, run `validate` on it before handing it over, and fix
every error it names.
