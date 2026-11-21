---
concept: single-sync-leader
state: demonstrated
date: 2026-11-18
source: jesdi/general-skills#240
status: active
---
# single-sync-leader

Before the lease, two workers sometimes ran the nightly sync at the same time and overwrote each other's results; the lease makes one worker the single writer.
