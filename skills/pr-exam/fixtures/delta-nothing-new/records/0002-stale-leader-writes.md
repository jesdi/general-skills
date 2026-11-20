---
concept: stale-leader-writes
state: demonstrated
date: 2026-11-18
source: jesdi/general-skills#240
status: active
---
# stale-leader-writes

After a pause (GC, network) both workers can believe they lead. Every write carries the fencing token from acquire, and write raises LeaseLost when the stored token differs, so the stale leader's late writes are refused.
