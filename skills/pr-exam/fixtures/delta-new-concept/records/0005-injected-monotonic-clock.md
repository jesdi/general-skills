---
concept: injected-monotonic-clock
state: demonstrated
date: 2026-11-18
source: jesdi/general-skills#240
status: active
---
# injected-monotonic-clock

The clock defaults to time.monotonic, which never jumps with wall-clock changes, and injecting it lets tests advance time without sleeping.
