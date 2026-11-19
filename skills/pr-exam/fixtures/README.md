# Recorded runs of the `pr-exam` skill

Each directory holds the inputs and outputs of one session that followed `SKILL.md`:

- `typo-readme/`: generation on a typo-only README diff (`request.json`, `diff.patch`, `body.md`) → `exam.json`, 1 question.
- `twelve-concepts/`: generation on a lease store with fencing tokens, a diff with 12 concepts → `exam.json`, 8 questions;
  the 4 behaviour concepts are listed but dropped by priority.
- `grading/`: grading of one attempt (`answers.json`) at the twelve-concept exam → `grades.json`, accepted by `score`.

Each run started from empty learning records (`learned` printed `{}`) and finished on `validate` or `score` exit 0.
`test_skill_fixtures.py` checks the outputs again.
