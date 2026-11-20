---
name: pr-exam
description: >-
  Deterministic rules for a PR exam: validate a generated exam.json against its
  generation request, project the served (answer-free) exam, score an attempt,
  read and write learning records, and validate a repo's .pr-exam.json. Use when
  generating, serving or grading a PR exam, or when the user says "pr-exam".
---

# pr-exam

Every exam rule lives in `pr_exam.py`, next to this file. Never re-implement one: run the script.
Each command reads files and prints JSON, or exits non-zero with every error on
stderr.

| Command | Does |
|---|---|
| `python3 pr_exam.py validate --request <request.json> <exam.json>` | Checks a generated exam against its request; prints its summary. |
| `python3 pr_exam.py public <exam.json>` | Prints the exam the page may show: no key, concepts or answers. |
| `python3 pr_exam.py score --exam <exam.json> --answers <answers.json> --grades <grades.json>` | Scores an attempt and names what was missed. |
| `python3 pr_exam.py learned --records <dir> --today <YYYY-MM-DD>` | Lists concepts known, due or wrong. |
| `python3 pr_exam.py record --records <dir> --exam <exam.json> --result <result.json> --today <YYYY-MM-DD>` | Writes learning records for a scored attempt. |
| `python3 pr_exam.py config [<.pr-exam.json>]` | Reads and validates the repo's exam config. |
| `python3 pr_exam.py setup [--check] <owner/repo>` | Run by the operator: makes `exam` a required check of the default branch. `--check` only reports whether it is one. |

## Generating an exam

You get a generation request, a JSON file with these fields:

| Field | Holds |
|---|---|
| `mode` | `first`, `followup` or `delta`: see "Modes". |
| `repo`, `pr`, `head_sha` | The PR. Copy them into the exam unchanged. |
| `diff` | Path of the PR's diff. |
| `body` | Path of the PR's body. Its merge-risk block names the risks. |
| `glossary` | Path of the repo's `GLOSSARY.md`, or null. When set, use its terms in titles and prompts. |
| `records`, `today` | The learning-record directory and the date: the arguments of `learned`. |
| `only_concepts`, `prior_prompts` | `followup` only: the concepts to ask, and the prompts already asked. |
| `prior_concepts` | `delta` only: the concepts an earlier exam of this PR covered. |
| `passed_diff` | `delta` only, when present: path of the PR's diff as it was when the exam was passed. |
| `out` | Where to write `exam.json`. |

Write this shape to `out`. A question has no field other than the ones shown:

```json
{
  "version": 1,
  "mode": "first",
  "repo": "owner/name", "pr": 240, "head_sha": "<from the request>",
  "concepts": [
    {"id": "merge-lease", "title": "Merge lease", "kind": "why"},
    {"id": "retry-budget", "title": "Retry budget", "kind": "concept"}
  ],
  "questions": [
    {"id": "q1", "concept": "merge-lease", "type": "text", "prompt": "…"},
    {"id": "q2", "concept": "retry-budget", "type": "choice", "prompt": "…", "options": ["…", "…"]}
  ],
  "key": {
    "q1": {"rubric": {"pass": "…", "partial": "…"}, "explanation": "…", "hunk": "…"},
    "q2": {"answer": 1, "explanation": "…", "hunk": "…"}
  }
}
```

1. List every concept you find in `concepts`, asked or not. A concept `id` is a slug (lowercase
   letters, digits, single hyphens). Its `kind` is one of these, highest priority first:
   - `why`: the reason behind a decision;
   - `concept`: a new concept or domain term;
   - `architecture`: a change of structure or boundary;
   - `risk`: a risky area, from the merge-risk block of the body;
   - `behaviour`: behaviour a user can see.

   Never list or ask implementation trivia: names, line counts, formatting, syntax.
2. One question per concept, 8 at most. With more than 8 concepts, ask the 8 of the highest-priority
   kinds and leave the others listed, unasked. A trivial PR (a typo, a rename, a comment) gets 1 question.
3. Run `python3 pr_exam.py learned --records <records> --today <today>` and reuse the id of a concept
   already listed there. `skip`: list it, do not ask it. `due`: ask it as `choice` only.
   `misconception`: list it and ask it, unless the cap of 8 drops it.
4. Type mix: `choice` (multiple choice) and `text` (free text). With 2 questions or more use both;
   with 3 or more, at least one `text` question is on a `why` concept.
5. A `choice` has at least 2 options with the same word count, no two equal, and nothing in the
   formatting that hints at the answer. This is retrieval practice: ask what the reader must recall,
   not what the diff shows.
6. Write the key before any answer exists, one entry per question id. Every entry has an
   `explanation` and a `hunk` copied verbatim from the diff. A `choice` entry has `answer` (an index
   into `options`) and no rubric. A `text` entry has a `rubric` with `pass` and `partial`, saying
   what a passing and a partial answer must contain, and no answer.
7. Run `python3 pr_exam.py validate --request <request.json> <exam.json>` and fix every error it names,
   then run it again. Finish only on exit 0.

### Modes

- `first`: the rules above.
- `followup`: one new question per concept in `only_concepts`, and no other concept: `concepts` lists
  exactly those. Do not repeat a prompt in `prior_prompts`: ask the concept from another angle. The
  type mix of step 4 does not apply.
- `delta`: list and ask only concepts absent from `prior_concepts`: the ones the last push added.
  Compare `diff` with `passed_diff` to find them. The type mix of step 4 does not apply. When
  nothing is new, write an empty exam: zero questions, and `questions`, `concepts` and `key` empty.

## Grading answers

One fresh session grades every `text` question of one attempt. You get a grading request, a JSON
file with these fields:

| Field | Holds |
|---|---|
| `mode` | `grade`. |
| `diff` | Path of the PR's diff. |
| `questions` | Path of a JSON list, one entry per `text` question: `{"id": "q1", "prompt": "…", "rubric": {"pass": "…", "partial": "…"}}`. |
| `answers` | Path of a JSON object: the answer to each of those questions, by question id. |
| `out` | Where to write `grades.json`. |

That is all you get: the diff, the questions, their rubrics and the answers, and
nothing else from generation. No key, no explanation, no earlier attempt.

1. Judge each answer against its rubric only: `pass` when it contains what `rubric.pass` asks for,
   `partial` when it contains only what `rubric.partial` asks for, `fail` otherwise. Read the diff
   to check what an answer claims. Wording, length and confidence earn nothing.
2. Write `grades.json` to `out`, one entry per question in `questions` and no other entry:
   `{"q1": {"grade": "pass", "feedback": "…"}}`. `feedback` is one line of feedback: what the answer
   got right or left out.
3. Check the shape of what you wrote: an entry for every question id, each `grade` one of `pass`,
   `partial`, `fail`, each `feedback` a one-line string. Then finish.

`choice` questions are never graded by the session, and you do not score the attempt: the host runs
`pr_exam.py score`, which marks them from the key and refuses a grade for one.

## Never

Never apply a bypass label. Never post an `exam` status. The host decides both.
