---
name: test-weigh-in
description: Measure a Python/pytest suite and score it on five pillars (Protection against regressions, Resistance to refactoring, Fast feedback, Maintainability, Economy), writing a JSON snapshot and a static HTML dashboard. Read-only. Use when the user asks to weigh, measure or score their test suite.
---

# test-weigh-in

Run the engine through the wrapper:

```sh
sh <skill-dir>/run.sh <repo-path> [--pytest-args "<options>"]
```

`<skill-dir>` is the directory holding this file. `<repo-path>` is the Python repo whose suite to weigh. The engine prints the path of the JSON snapshot it wrote, and exits non-zero with a message on stderr when it cannot run.

Relay the engine's output to the user exactly as printed. Do not add interpretation, scores, advice or a summary of your own.
