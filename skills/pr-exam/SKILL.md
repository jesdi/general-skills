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

## Generating an exam

You get a generation request (`design.md`, "Generation request") and write `exam.json` to its `out` path.
Read the `diff`, the `body` and, when `glossary` is set, `GLOSSARY.md`: use its terms in titles and prompts.

1. List the concepts the PR teaches, then keep the highest-priority ones. Priority, highest first:
   why (the reason for the change), concepts, architecture, risk (from the merge-risk block of the body),
   behaviour. Never ask implementation trivia: names, line counts, formatting, syntax.
2. One question per concept, 8 at most. A trivial PR (a typo, a rename, a comment) gets 1 question.
3. Type mix: `choice` (multiple choice) and `text` (free text). With 2 questions or more use both;
   with 3 or more, at least one `text` question is on a why concept.
4. A `choice` has at least 2 options with the same word count, no two equal, and nothing in the
   formatting that hints at the answer. This is retrieval practice: ask what the reader must recall,
   not what the diff shows.
5. Concept ids are slugs. Run `python3 pr_exam.py learned --records <records> --today <today>` and reuse
   the id of a concept already listed there. `skip`: do not ask it. `due`: ask it as `choice` only.
   `misconception`: ask it unless the cap drops it.
6. Write the key before any answer exists: each question has an `explanation` and a `hunk` copied
   verbatim from the diff; a `choice` has `answer` (an index into `options`); a `text` has a `rubric`
   with `pass` and `partial`, saying what a passing and a partial answer must contain.
7. Run `python3 pr_exam.py validate --request <request.json> <exam.json>` and fix every error it names,
   then run it again. Finish only on exit 0.

Shape: `version` 1, `mode`, `repo`, `pr`, `head_sha` (copied from the request), `concepts`
(`id`, `title`, `kind`), `questions` (`id`, `concept`, `type`, `prompt`, `options` for `choice`), `key`
(by question id). A question has no other field.

### Modes

- `first`: the rules above.
- `followup`: one new question per concept in `only_concepts`, and no other concept. Do not repeat a
  prompt in `prior_prompts`: ask the concept from another angle.
- `delta`: list and ask only concepts absent from `prior_concepts` (the push added them). When there
  is none, write an empty exam: zero questions, and `questions`, `concepts` and `key` empty.

## Grading answers

A grading session is given the diff, one question, its rubric and the answer, and
nothing from generation: no other question, no explanation, no earlier attempt. Judge the answer against the rubric
only. Write `grades.json`, one entry per `text` question:
`{"q1": {"grade": "pass", "feedback": "one line"}}`, where `grade` is `pass`, `partial` or `fail`
and the feedback is one line of feedback. `choice` questions are never graded by the session:
`score` marks them from the key, and an entry for one is refused.
Then run `python3 pr_exam.py score --exam <exam.json> --answers <answers.json> --grades <grades.json>`
and fix every error it names.

## Never

Never apply a bypass label. Never post an `exam` status. The host decides both.
