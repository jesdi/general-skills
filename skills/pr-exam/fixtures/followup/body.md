Adds a lease so that only one worker runs the nightly sync at a time. Before this, two workers sometimes ran it at once and overwrote each other's results.

Why: a worker that stalls past its TTL must not keep writing after another worker took over, so every write carries a fencing token and the store refuses a stale one.

```merge-risk v1
risk: medium
rollback: revert; leases expire after the TTL
blast-radius: the nightly sync only
```
