# PR exam — Spec

## Requirements

1. **The `pr-exam` skill** exists at `skills/pr-exam/` and was written with `writing-for-agents`.
   It instructs a session how to generate an exam and how to grade one. The host runs both
   sessions (see `pr-exam-host`).
2. **What an exam contains:** questions, an answer key, and a rubric for every free-text question.
   The rubric is fixed when the exam is generated, before any answer exists. The host serves the
   questions without the key or the rubrics.
3. **Question priority.** Questions are drawn from the PR in this order: the why behind a decision,
   new concepts or domain terms, architecture or boundary changes, risky areas (the PR's
   merge-risk block), user-visible behaviour. The exam never asks about implementation trivia,
   such as names or individual lines.
4. **Exam size** is set by how many distinct concepts the PR introduces, not by how many lines it
   changes:
   - trivial PRs: 1 question;
   - normal PRs: 3–5 questions;
   - at most 8 questions.
5. **Question types:**
   - A first exam with 2 or more questions mixes multiple-choice and free-text questions.
   - A first exam with at least 3 questions has at least one free-text *why* question.
   - Follow-up and delta exams are exempt from both rules: their questions are fixed by the missed
     or new concepts, and a due concept must be multiple choice.
   - All options of a multiple-choice question have the same word count.
   - Where the repo has a `GLOSSARY.md`, questions use its terms.
6. **Scoring:**
   - A multiple-choice question scores 1 or 0.
   - A fresh grader judges each free-text answer against its rubric: pass 1, partial 0.5, fail 0.
   - The exam passes when the score is at least 80% of the maximum *and* every *why* question
     scored pass.
7. **Failed attempts:**
   - A failed attempt shows an explanation of each missed question, quoting the relevant diff
     hunk.
   - The next attempt has one new question for each missed concept. Passed concepts are not
     asked again.
   - There is no limit on attempts.
8. **Learning records.** Generation reads the operator's learning records for the repo.
   - **Demonstrated less than 30 days ago:** the concept is skipped.
   - **Demonstrated 30 days ago or more:** the concept is due and may be asked again, always as
     multiple choice, never as free text.
   - **Recorded as a misconception:** the concept is included.
9. **`.pr-exam.json`** in a repo root configures exams:
   - `enabled`: boolean. Exams are on in repos without the file.
   - bypass labels, each mapped to the mode `skip` or `defer`.
   An invalid file stops the run with every error named; it is never treated as "exams off".
10. **When a bypass label counts.** It counts only if the label's most recent `labeled` event was
    made by the operator: the GitHub login configured on the box. In these requirements and
    scenarios, "the repo owner" means that login, also for a repo owned by an organization.
    - **`skip`:** no exam; status `exam` = success, "bypassed".
    - **`defer`:** status `exam` = success, "deferred"; the exam becomes debt and stays open after
      the merge.
    - **Label added by anyone else:** it is ignored, and the PR is gated as usual.
11. **babysit-pr waits for the exam:**
    - It requests the exam from the host once everything else is ready (CI green, no blocking
      reviews or threads).
    - It waits for the `exam` status with no time limit and does not report it as "CI stalled".
    - It shows the exam link in its status output.
12. **babysit-pr refuses to merge** a PR in a repo with exams enabled unless the PR's head commit
    has `exam` = success, posted by the box's GitHub App. This holds whatever the approval path,
    including session approval. A status posted by anyone else doesn't count.
13. **Pushes after the exam:**
    - While the exam is open, a new push replaces it with an exam on the new head.
    - After a pass, a new push gets a delta judgment from the host:
      - **new concept:** a follow-up exam covering only the new concepts;
      - **otherwise:** success is re-posted on the new head.
14. **`pr-exam setup <owner/repo>`** is run by hand.
    - On a repo that supports branch protection, it adds `exam` to the default branch's
      required status checks and keeps every existing required check. `exam` must come from the
      agent-ops box's GitHub App, so a status posted by anyone else doesn't count.
    - On a repo that can't be protected (GitHub replies "Upgrade to GitHub Pro or make this
      repository public"), it changes nothing and prints that the gate is agent-enforced only.
15. **Missing required check.** babysit-pr warns once per run when exams are enabled in a public
    repo but `exam` is not a required check.
16. **Agents never apply bypass labels.** The skill and babysit-pr instructions say so explicitly.

## Scenarios

### Scenario: babysit-pr waits for the exam once the PR is otherwise ready

- **Given** a repo with no `.pr-exam.json` and an open non-draft PR whose CI is green
- **When** babysit-pr reaches the merge decision
- **Then** it requests an exam from the host, prints the exam link, and keeps waiting while `exam`
  is pending, even past `waitMinutes`, without reporting "CI stalled"

### Scenario: the exam isn't requested before CI is green

- **Given** an open PR whose CI is still running
- **When** babysit-pr polls
- **Then** it does not request an exam

### Scenario: merge refused without a pass

- **Given** a PR whose head commit `abc123` has `exam` = pending, and the operator has approved
  the merge in the session
- **When** babysit-pr runs `merge --session-approved`
- **Then** the merge is refused with "exam not passed", and `gh pr merge` is not called

### Scenario: merge goes ahead after a pass

- **Given** head `abc123` with `exam` = success and the automatic judge says `agree` at risk 2
- **When** babysit-pr merges
- **Then** `gh pr merge --match-head-commit abc123` runs

### Scenario: a status posted by anyone else doesn't count

- **Given** head `abc123` whose `exam` status from the box's GitHub App is pending, and a newer
  `exam` = success posted with the operator's login
- **When** babysit-pr waits for the exam or runs `merge`
- **Then** the wait goes on, and the merge is refused with "exam not passed"

### Scenario: a pass is not merge approval

- **Given** head `abc123` with `exam` = success, and the risk gate verdict is `human`
- **When** babysit-pr reaches the merge decision
- **Then** it hands the PR off for owner approval exactly as it would without exams

### Scenario: the owner's skip label bypasses the exam

- **Given** `.pr-exam.json` maps `exam:skip` to `skip`, and the repo owner added `exam:skip`
- **When** babysit-pr reaches the merge decision
- **Then** no exam is generated, `exam` = success with the description "bypassed", and the merge
  goes on

### Scenario: an agent's skip label is ignored

- **Given** the `exam:skip` label was added by a bot or any account other than the repo owner
- **When** babysit-pr reaches the merge decision
- **Then** the label is ignored and an exam is requested as usual

### Scenario: the owner's defer label lets the merge through and leaves debt

- **Given** the repo owner added `exam:defer`, which is mapped to `defer`
- **When** babysit-pr reaches the merge decision
- **Then** `exam` = success with the description "deferred", the merge goes on, and the host
  records an open exam for the PR

### Scenario: an exam with 80% and every why question passed passes

- **Given** an exam with 5 questions: 3 multiple choice, 1 free-text *why* and 1 other free text
- **When** the operator gets the 3 multiple-choice questions right, a pass on the *why* and a
  partial on the other free text (4.5/5 = 90%)
- **Then** the exam passes

### Scenario: exactly 80% passes

- **Given** the same 5-question exam
- **When** the score is 4/5, with the *why* question passed
- **Then** the exam passes

### Scenario: under 80% fails

- **Given** the same 5-question exam
- **When** the score is 3.5/5 (70%), with the *why* question passed
- **Then** the exam fails

### Scenario: failing a why question fails the exam even above 80%

- **Given** a 5-question exam with one *why* question
- **When** the operator gets all four other questions right and a partial on the *why* (4.5/5 =
  90%)
- **Then** the exam fails

### Scenario: a failed attempt re-tests only what was missed

- **Given** an attempt that missed the concepts "lease ownership" and "head-SHA recheck"
- **When** the attempt is graded
- **Then** the result explains each miss and quotes its diff hunk, and the next attempt has
  exactly 2 new questions, one on each of those concepts

### Scenario: a trivial PR gets one question

- **Given** a PR that only fixes a typo in a README
- **When** an exam is generated
- **Then** it has exactly 1 question

### Scenario: an exam never goes over 8 questions

- **Given** a PR that introduces 12 distinct concepts
- **When** an exam is generated
- **Then** it has 8 questions, picked by the priority order

### Scenario: multiple-choice options don't give the answer away

- **Given** any generated multiple-choice question
- **Then** all its options have the same word count

### Scenario: questions avoid demonstrated concepts and revisit misconceptions

- **Given** learning records saying the operator demonstrated "merge lease" last week and holds a
  misconception about "base SHA recheck"
- **When** a PR touching both is examined
- **Then** no question targets "merge lease", and at least one targets "base SHA recheck"

### Scenario: a concept due for re-testing is asked as multiple choice

- **Given** a learning record saying the operator demonstrated "merge lease" exactly 30 days ago
- **When** a PR touching "merge lease" is examined and the exam asks about it
- **Then** that question is multiple choice, not free text

### Scenario: a concept demonstrated 29 days ago is skipped

- **Given** a learning record saying the operator demonstrated "merge lease" 29 days ago
- **When** a PR touching "merge lease" is examined
- **Then** no question targets "merge lease"

### Scenario: the exam sent to the page contains no answers

- **Given** a generated exam
- **Then** the questions the host serves include neither the answer key nor the rubrics

### Scenario: exams turned off for a repo

- **Given** `.pr-exam.json` is `{"enabled": false}`
- **When** babysit-pr reaches the merge decision
- **Then** no exam is requested, and the merge does not wait for an `exam` status

### Scenario: an invalid config stops the run

- **Given** a `.pr-exam.json` whose bypass mode is `"maybe"`
- **When** babysit-pr reads it
- **Then** it stops with an error naming the field and the allowed values `skip` and `defer`

### Scenario: a push while the exam is open replaces it

- **Given** an open exam for head `abc123`
- **When** head `def456` is pushed
- **Then** the host replaces the exam with one on `def456`, and `exam` on `def456` is pending

### Scenario: a push after a pass adds nothing new

- **Given** the operator passed the exam on `abc123`
- **When** `def456` is pushed and the delta grader finds no new concept
- **Then** `exam` = success is posted on `def456` without a new exam

### Scenario: a push after a pass adds a new concept

- **Given** the operator passed the exam on `abc123`
- **When** `def456` is pushed and the delta grader finds a new concept "retry budget"
- **Then** a follow-up exam asks only about "retry budget", and `exam` on `def456` is pending

### Scenario: setup on a public repo

- **Given** public repo `jesdi/agent-ops` whose main branch requires the checks `test` and `lint`
- **When** the operator runs `pr-exam setup jesdi/agent-ops`
- **Then** the required checks become `test`, `lint` and `exam`, with `exam` limited to the box's
  GitHub App

### Scenario: setup on a private repo of a free account

- **Given** private repo `jesdi/claude-config`, where GitHub refuses branch protection
- **When** the operator runs `pr-exam setup jesdi/claude-config`
- **Then** nothing changes, and the output says the gate is agent-enforced only

### Scenario: babysit-pr warns when the check isn't required

- **Given** public repo `jesdi/general-skills` with exams enabled and no `exam` required check
- **When** babysit-pr runs
- **Then** it prints the warning once, naming `pr-exam setup jesdi/general-skills`

## Out of scope

- Visual questions.
- Examinees other than the operator.
- Treating a pass as merge approval.
- Automatic branch protection.
- Hosting, grading, learning-record storage and sweeps, which are covered by agent-ops
  `pr-exam-host`.
