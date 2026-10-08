---
name: review-page
description: One interactive HTML page for a plan review or a questionnaire, built from a fixed template. Use when a pipeline session must deliver a plan review or a questionnaire as an interactive page the console shows.
---

# Review page

The session fills a fixed template. The console shows the page in an iframe and reads the operator's answers from it. Messages and the answers file are in [schema.md](schema.md).

## Modes

Set the mode on the body: `<body data-mode="plan">` or `<body data-mode="questionnaire">`.

- `plan`: tickets, open questions, corrections, a track, and a bar with the count of answered questions.
- `questionnaire`: questions only. The template hides the other sections.

The page has no buttons. The console shows "Send changes" and "Approve" (or "Send answers") outside the page, so a page can never submit or approve. Never add a button or a `submit` value to the page.

## Steps

1. Copy [template.html](template.html) to the path the prompt names: `.agent/review.html` (plan) or `.agent/questionnaire.html` (questionnaire).
2. Set `data-mode` on the body.
3. Replace the content between `<!-- slot: NAME -->` and `<!-- /slot -->` in each slot. Keep the `<section data-slot="...">` wrappers. Slots: `header` (eyebrow, `h1`, lede), `tickets`, `questions`, `corrections`, `track`. Do not nest a `<section>` in a slot.
4. Open the file and check that it renders.

## Slot shapes

Ticket, in `ol.tickets`:

```html
<li><span class="n">01</span><span>Title <span class="by">· blocked by none · seam: file.py</span></span></li>
```

Question, in `questions`. The `data-q` value, the radio `name` and the note id `note-ID` use the same id:

```html
<div class="q" data-q="format">
  <header><h3>Question text</h3><span class="chip waiting">needs you</span></header>
  <p class="why">Why it matters.</p>
  <div class="opts">
    <label class="opt"><input type="radio" name="format" value="a"><span><span class="t">Option A <span class="chip rec">recommended</span></span><br><span class="d">Trade-off.</span></span></label>
    <label class="opt"><input type="radio" name="format" value="b"><span><span class="t">Option B</span><br><span class="d">Trade-off.</span></span></label>
  </div>
  <textarea id="note-format" placeholder="Note (optional)"></textarea>
</div>
```

Correction, in `corrections`:

```html
<div class="corr"><p class="was">Old statement.</p><p>New statement.</p></div>
```

Track pills, in `track`. Check the proposed track:

```html
<div class="tracks" role="radiogroup" aria-label="track">
  <label><input type="radio" name="track" value="standard" checked>standard</label>
</div>
```

## Rules

- Never edit the `<script>` or the CSS. Only fill slots.
- Every question has an id that matches `[a-z0-9_-]{1,64}`. The id `track` is reserved for the track pills. Slot content never carries the id `state`, or the name `track` outside the track slot: the script owns them.
- Every question has exactly one recommended option, marked with `chip rec`. It is one recommended option, never zero and never two.
- Option values are short ids. Put no option text in a value.
- The page has no network, no storage and no external file: no script `src`, no `<link>`, no image or font URL.
- Keep `<meta name="agent-ops-review" content="1">`. The dispatcher looks for it.
- Re-write the file after a plan revision. Remove the questions and tickets that no longer apply.
