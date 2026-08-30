---
name: crap-gate
description: >-
  Score every function a branch changed with CRAP (cyclomatic complexity ×
  untested fraction), fail on existing functions > 15 and new functions > 9,
  and drive a fixer + independent reviewer loop until the gate passes. Use
  when implementation is done and before pushing, when the user says "run the
  crap gate", "check CRAP", "is this change tested enough", or when a push
  gate reports CRAP violations.
---

# crap-gate

(Procedure written in Task 8.)
