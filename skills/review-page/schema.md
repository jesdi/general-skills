# Review page schema

Every message carries `v`. `v` is the integer `1`. It changes only when the console cannot read the old shape.

## Messages

Page to console, once, when its script listens:

```json
{"type": "ready", "v": 1}
```

Page to console, on every change and on every button:

```json
{"type": "answers", "v": 1, "answers": {"format": "a", "track": "standard"}, "submit": null}
```

`submit` is `null` for a draft, `"changes"` for "Send changes" or "Send answers", and `"approve"` for the second tap on "Approve". The console adds the request's `revision` when it posts the set to the box.

Console to page, after `ready` and whenever saved answers arrive:

```json
{"type": "restore", "v": 1, "answers": {"format": "b", "track": "standard"}}
```

The page accepts `restore` only from its parent window, and only when `v` is `1`.

## The `answers` object

`answers` is a flat JSON object. A key is a question id, a question id followed by `.note`, or `track`. A value is a string (one chosen option id, a note, or a track name), a list of strings (a multi-choice question), or a boolean. A question with no choice has no key. A note appears only when it is not empty.

A question id is the `data-q` value of the question block. It matches `[a-z0-9_-]{1,64}`.

## Answers file

The dispatcher writes `.agent/review-answers.json` (plan) or `.agent/questionnaire-answers.json` (spec) in the task worktree. Sessions read it. A session that takes a text answer writes it in the same shape.

```json
{
  "v": 1,
  "stage": "plan",
  "submitted": "changes",
  "submitted_at": "2026-10-12T10:12:03+00:00",
  "actor": "jesdi",
  "answers": {"debounce": "draft", "debounce.note": "flush on pagehide", "track": "standard"}
}
```

- `v`: the integer `1`.
- `stage`: `"plan"` for `.agent/review-answers.json`, `"spec"` for `.agent/questionnaire-answers.json`.
- `submitted`: `null` for a draft, `"changes"` or `"approve"` for a submission.
- `submitted_at`: the time of the intent that was applied, or `null` for a draft.
- `actor`: the operator's console login.
- `answers`: the object above.

The file holds no option texts.
