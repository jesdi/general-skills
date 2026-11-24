# Recorded runs of the `pr-exam` skill

Each directory holds the inputs and outputs of one headless session that followed `SKILL.md`, started
with the prompt the host uses (agent-ops `dispatcher/exam_runner.py`). One live run per mode:

- `typo-readme/`: `first`, on a typo-only README diff → `exam.json`, 1 question.
- `twelve-concepts/`: `first`, on a lease store with fencing tokens, a diff with 12 concepts → `exam.json`,
  8 questions; the 4 behaviour concepts are listed but dropped by priority.
- `grading/`: one session grading the three `text` answers of an attempt at the twelve-concept exam
  (`request.json`, `questions.json`, `answers.json`) → `grades.json`. `attempt.json` holds every answer
  of that attempt; `score` on it fails the attempt and names three missed concepts.
- `followup/`: `followup`, on those three concepts, with the learning records `record` wrote for the
  failed attempt → `exam.json`, 3 new questions.
- `delta-new-concept/`: `delta`, after a pass: the push adds a retry budget (`diff.patch` against
  `passed.diff`) → `exam.json`, 2 questions on concepts no earlier exam covered.
- `delta-nothing-new/`: `delta`, after a pass: the push only rewords a docstring and renames a
  variable → an empty `exam.json`.
- `tip-choice/`, `tip-text/`, `chat-answer/`: three live turns on the twelve-concept exam (a tip on a
  `choice` question, tip 1 on a `text` question, a `question` chat turn "what is the answer?"). Each has
  `turn.json`, `prompt.md` (what `context` prints for it) and `reply.md` (`claude -p --tools ''` on the prompt).

The requests are written as the host writes them, with paths made relative. Every generation run
finished on `validate` exit 0. `test_skill_fixtures.py` checks the outputs again.
