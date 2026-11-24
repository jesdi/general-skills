The answer is **"Every new acquisition increments the stored token."**

- **Why that's right:** `acquire` stores `held["token"] + 1`, so each new holder gets a higher token than the last. `write` then checks `held["token"] != token` and raises `LeaseLost`. An older leader's token no longer matches the stored one, so its late write is refused. This is the behaviour the PR body describes: "the store refuses a stale one."
- **Why not the owner's process identifier:** the token is a plain integer counter. `write` never looks at the owner at all.
- **Why not a signed expiry:** the expiry is stored in the lease record, not in the token, and nothing is signed. `write` doesn't check expiry either. It only compares tokens.

One caveat from the diff: `write` checks for an exact match, not "is this the highest token." It works here only because the stored token only ever goes up.
