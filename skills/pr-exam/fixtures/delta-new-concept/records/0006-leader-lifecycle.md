---
concept: leader-lifecycle
state: demonstrated
date: 2026-11-18
source: jesdi/general-skills#240
status: active
---
# leader-lifecycle

run_leader renews the lease after every unit of work, stops with False when renew raises LeaseLost, and releases the lease in a finally block so it is freed whatever happens.
