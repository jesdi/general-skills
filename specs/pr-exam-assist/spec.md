# PR exam assist — Spec

Extends `specs/pr-exam`. Where this spec is silent, `specs/pr-exam` holds.

## Requirements

1. **Where the rules live.** `skills/pr-exam/SKILL.md` gains a "Tips" section and a "Chat"
   section, written with `writing-for-agents`.
   - A tip or chat turn runs with no tools. Everything it needs arrives in the prompt
     `pr_exam.py context` builds (requirement 2).
   - Each rule appears once. The prompt carries the section's own text; the rule is not copied
     anywhere else.
   - Every word in SKILL.md is read on every run, and the two new sections go into every tip
     and chat prompt. So every addition is as short as it can be: each new section is at most
     150 words.
2. **`pr_exam.py context`** prints one self-contained prompt for one turn. There are three
   kinds of turn:

   | Kind | The prompt contains |
   |---|---|
   | `tip` | the "Tips" rules; the diff and the PR body; the question (prompt, and options for a `choice`); its key entry; the current draft (possibly empty); the tip number; the earlier tips on that question |
   | `question` (a question's chat thread) | the "Chat" rules; the diff and the PR body; the question; its key entry; the current draft; the thread so far; the new message |
   | `general` (the PR/exam chat) | the "Chat" rules; the diff and the PR body; every question with its key entry; the thread so far; the new message. No draft. |

   After submit, a `question` turn gets the submitted answer in place of the draft. Once the
   attempt is graded, it also gets the question's points and, for a `text` question, the
   grader's feedback.

   The prompt starts with the parts that are the same on every turn of a kind (the rules, the
   diff and the PR body) so that repeated turns hit the prompt cache. The turn's own parts
   follow.

   It refuses, exiting non-zero and naming every error:
   - a prompt longer than 400,000 characters, naming its length and the limit;
   - a tip number other than 1, 2 or 3;
   - a question id the exam doesn't have;
   - a draft on a `general` turn.
3. **The tip ladder.** Each tip reacts to the current draft and works on an empty one:
   - Tip 1 says where to look: a file or a hunk of the diff.
   - Tip 2 names the idea the draft is missing.
   - Tip 3 is one step from the answer.

   No tip states the answer. On a `choice` question, no tip quotes, names or rules out an
   option.
4. **The chat answers freely.** It sees the key and may give the answer. A `question` turn
   reads the current draft; a `general` turn has none.
5. **Bypass in scoring.** `score` takes the set of bypassed question ids. A bypassed question:
   - needs no answer and no grade; any answer or grade it has is ignored;
   - counts in neither the points nor the maximum;
   - is not a missed concept, so the *why* rule ignores it and no follow-up asks it;
   - is reported as `bypassed` in the result.
6. **At most one bypass per attempt.** `score` refuses an attempt with two or more bypassed
   questions and names them all.
7. **Assisted in scoring.** `score` takes the set of assisted question ids.
   - An assisted question scores exactly as it would unassisted.
   - The result reports it as `assisted`.
   - A question that is both bypassed and assisted is reported as `bypassed` only.
8. **Pass rule with a bypass.** An attempt passes when two things hold:
   - the points are at least 80% of the maximum over the questions not bypassed;
   - no missed concept is of kind *why*.

   When every question is bypassed (only possible on a one-question exam), the attempt
   passes with 0 / 0. The bypass still writes its `misconception` record (requirement 9).
9. **Learning records.** `record` writes, per question of a scored attempt:

   | Question | Record |
   |---|---|
   | bypassed | `misconception` |
   | full point, assisted | `assisted` (new state) |
   | full point, not assisted | `demonstrated` (as today) |
   | 0 points | `misconception` (as today) |
   | half a point | none (as today) |

10. **`learned` reads `assisted`.** An active `assisted` record makes its concept `due` at any
    age, so generation asks it as multiple choice. A newer record supersedes it as usual.
11. **Recorded live runs.** `fixtures/` gains three recorded live turns, built with `context`
    and checked by the tests:
    - a tip on a `choice` question;
    - a tip 1 on a `text` question;
    - a `question` chat turn that asks for the answer.

## Scenarios

Unless a scenario says otherwise, the exam has 6 questions:
- `q1`, `q2`, `q3`: multiple choice, on the concepts `merge-lease`, `retry-budget` and
  `head-recheck`;
- `q4`: free text, on the *why* concept `lease-owner`;
- `q5`, `q6`: free text, on the concepts `fencing-token` and `stale-lease`.

### Scenario: a bypassed question leaves the score

- **Given** `q6` is bypassed and has no answer and no grade
- **When** the operator gets `q1`–`q3` right, and `q4` and `q5` are graded pass
- **Then** `score` reports 5 / 5, the attempt passes, and `q6` is reported as `bypassed`

### Scenario: exactly 80% of the questions not bypassed passes

- **Given** `q6` is bypassed
- **When** `q1` is wrong, `q2` and `q3` are right, and `q4` and `q5` are graded pass
- **Then** `score` reports 4 / 5 (80%) and the attempt passes

### Scenario: under 80% of the questions not bypassed fails

- **Given** `q6` is bypassed
- **When** `q1` is wrong, `q2` and `q3` are right, `q4` is graded pass and `q5` partial
- **Then** `score` reports 3.5 / 5 (70%), the attempt fails, and the missed concepts are
  `merge-lease` and `fencing-token`, not `stale-lease`

### Scenario: a bypassed why question doesn't fail the attempt

- **Given** `q4`, the *why* question, is bypassed
- **When** every other question scores its full point
- **Then** `score` reports 5 / 5, the attempt passes, and `lease-owner` is not a missed concept

### Scenario: two bypasses are refused

- **Given** `q2` and `q6` are both bypassed
- **When** `score` runs
- **Then** it exits non-zero naming `q2` and `q6`, and writes no result

### Scenario: an answer to a bypassed question is ignored

- **Given** `q2` is bypassed and its answer is the wrong option
- **When** every other question scores its full point
- **Then** `score` reports 5 / 5, and `q2` is reported as `bypassed` with no points

### Scenario: bypassing the only question passes

- **Given** a one-question exam on a typo fix, whose `q1` on the concept `readme-typo` is
  bypassed
- **When** `score` runs and then `record` runs on 2026-11-24
- **Then** `score` reports 0 / 0 and the attempt passes, and `readme-typo` gets an active
  `misconception` record

### Scenario: assisted doesn't change the score

- **Given** `q2` is assisted and answered right
- **When** the attempt is scored
- **Then** `q2` scores 1 point and is reported as `assisted`

### Scenario: a bypass outranks assisted

- **Given** `q6` is both bypassed and assisted
- **When** the attempt is scored
- **Then** `q6` is reported as `bypassed` and not as `assisted`

### Scenario: each outcome writes its record

- **Given** a scored attempt, on 2026-11-24, where:
  - `q1` is right and not assisted;
  - `q2` is right and assisted;
  - `q3` is wrong and assisted;
  - `q4` is bypassed;
  - `q5` is graded partial and assisted;
  - `q6` is graded pass and not assisted.
- **When** `record` runs
- **Then** the new active records are:
  - `merge-lease` and `stale-lease`: `demonstrated`;
  - `retry-budget`: `assisted`;
  - `head-recheck` and `lease-owner`: `misconception`;
  - `fencing-token`: none.

### Scenario: an assisted concept is due at once

- **Given** an active `assisted` record for `retry-budget` dated 2026-11-22
- **When** `learned` runs with today 2026-11-24
- **Then** `retry-budget` is `due`

### Scenario: a demonstrated concept of the same age is still skipped

- **Given** an active `demonstrated` record for `retry-budget` dated 2026-11-22
- **When** `learned` runs with today 2026-11-24
- **Then** `retry-budget` is `skip`

### Scenario: a clean pass after an assist upgrades the record

- **Given** an active `assisted` record for `retry-budget`
- **When** a later attempt answers a `retry-budget` question right without assistance, and
  `record` runs on 2026-11-24
- **Then** the `assisted` record is superseded, a `demonstrated` record dated 2026-11-24 is
  active, and `learned` reports `retry-budget` as `skip` on 2026-11-24

### Scenario: a tip prompt carries the draft, the tip number and the earlier tips

- **Given** an attempt where tip 1 on `q5` has already been given
- **When** `context` builds tip 2 for `q5`, with the draft "the token only grows"
- **Then** the prompt contains:
  - the "Tips" rules;
  - the diff and the PR body;
  - `q5`'s prompt, rubric, explanation and hunk;
  - the draft "the token only grows";
  - tip number 2;
  - the text of tip 1.

### Scenario: the same parts open every turn of a kind

- **When** `context` builds tip 1 and tip 2 for `q5`, with different drafts
- **Then** both prompts start with the same text (the "Tips" rules, the diff and the PR body),
  and only what follows differs

### Scenario: a PR too large for chat is refused

- **Given** a diff of 450,000 characters
- **When** `context` builds any turn on it
- **Then** it exits non-zero, naming the prompt's length and the 400,000-character limit

### Scenario: a fourth tip is refused

- **When** `context` builds tip 4 for `q5`
- **Then** it exits non-zero, naming the tip number and the allowed range 1–3

### Scenario: an unknown question is refused

- **When** `context` builds a `question` turn for `q9` on the 6-question exam
- **Then** it exits non-zero, naming `q9`

### Scenario: a question thread sees the draft and the key

- **Given** a thread on `q2` with one earlier exchange
- **When** `context` builds a `question` turn for `q2`, with the draft "option 1" and the
  message "is this right?"
- **Then** the prompt contains:
  - the "Chat" rules;
  - the diff and the PR body;
  - `q2`'s prompt and options;
  - `q2`'s key entry, including `answer`;
  - the draft;
  - the earlier exchange;
  - the new message.

### Scenario: after grading, a question thread sees the answer, the points and the feedback

- **Given** a graded attempt where `q5` was answered "the token is random", scored 0, and
  got the feedback "misses that the token only grows"
- **When** `context` builds a `question` turn for `q5` with the message "why was I wrong?"
- **Then** the prompt contains the submitted answer "the token is random", the points 0, and
  the feedback "misses that the token only grows"

### Scenario: while grading, a question thread sees the submitted answer only

- **Given** an attempt that was submitted with `q5` answered "the token is random" and is not
  graded yet
- **When** `context` builds a `question` turn for `q5`
- **Then** the prompt contains the submitted answer, and contains no points and no feedback

### Scenario: the general chat sees every question and no draft

- **When** `context` builds a `general` turn with the message "what is the riskiest change?"
- **Then** the prompt contains the "Chat" rules, the diff, the PR body, all 6 questions with
  their key entries, and the message, and it contains no draft

### Scenario: a draft on the general chat is refused

- **When** `context` builds a `general` turn with a draft
- **Then** it exits non-zero, naming the draft as not allowed on a `general` turn

### Scenario: a recorded tip on a choice question gives no option away

- **Given** the recorded tip turn on a `choice` question of the twelve-concept exam
- **Then** the tip contains the text of none of the question's options

### Scenario: a recorded first tip points into the diff

- **Given** the recorded tip 1 on a `text` question of the twelve-concept exam
- **Then** the tip names a file path that appears in the diff

### Scenario: a recorded chat turn answers freely

- **Given** the recorded `question` turn on a `choice` question, with the message "what is the
  answer?"
- **Then** the reply contains the text of the option at the key's `answer` index

## Out of scope

- The host's part (agent-ops `pr-exam-host-assist`):
  - endpoints, the exam-chat socket service, streaming and the page;
  - `threads.json`;
  - counting tips and enforcing the cap across reloads;
  - deciding which questions are assisted or bypassed;
  - hiding the threads from the grader;
  - showing the grader's feedback line.
- Live grading per question, and tips that cost points.
- Changes to generation, exam size, question priority and the type mix.
