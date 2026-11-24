A tip helps a reader who is stuck on one question. You get the question, its key, the draft
and the earlier tips. Climb one rung per tip number:

1. Where to look: point at a file or hunk, nothing more.
2. The missing idea: name what the draft leaves out.
3. One step away: bring the reader next to the answer, never onto it.

React to the draft. Never state the answer, never quote the key. On a `choice` question never
quote, name or rule out an option. Do not repeat an earlier tip. Reply in two sentences at most.

Diff:
diff --git a/lease.py b/lease.py
new file mode 100644
--- /dev/null
+++ b/lease.py
@@ -0,0 +1,61 @@
+import time
+
+
+class LeaseLost(Exception):
+    """The caller no longer owns the lease it tried to write under."""
+
+
+class LeaseStore:
+    """Single-writer lease over a key-value store.
+
+    Two workers can both believe they lead after a pause (GC, network). A
+    fencing token lets the store reject the older leader's late writes.
+    """
+
+    def __init__(self, backend, ttl=30, clock=time.monotonic):
+        self.backend = backend
+        self.ttl = ttl
+        self.clock = clock
+
+    def acquire(self, name, owner):
+        now = self.clock()
+        held = self.backend.get(name)
+        if held and held["expires"] > now and held["owner"] != owner:
+            return None
+        token = (held["token"] + 1) if held else 1
+        self.backend.put(name, {"owner": owner, "token": token, "expires": now + self.ttl})
+        return token
+
+    def renew(self, name, owner, token):
+        held = self.backend.get(name)
+        if not held or held["owner"] != owner or held["token"] != token:
+            raise LeaseLost(name)
+        held["expires"] = self.clock() + self.ttl
+        self.backend.put(name, held)
+
+    def write(self, name, token, key, value):
+        held = self.backend.get(name)
+        if not held or held["token"] != token:
+            raise LeaseLost(name)
+        self.backend.put(key, value)
+
+    def release(self, name, owner, token):
+        held = self.backend.get(name)
+        if held and held["owner"] == owner and held["token"] == token:
+            held["expires"] = 0
+            self.backend.put(name, held)
+
+
+def run_leader(store, name, owner, work, interval=10):
+    token = store.acquire(name, owner)
+    if token is None:
+        return False
+    try:
+        while work(token):
+            store.renew(name, owner, token)
+            time.sleep(interval)
+    except LeaseLost:
+        return False
+    finally:
+        store.release(name, owner, token)
+    return True


PR body:
Adds a lease so that only one worker runs the nightly sync at a time. Before this, two workers sometimes ran it at once and overwrote each other's results.

Why: a worker that stalls past its TTL must not keep writing after another worker took over, so every write carries a fencing token and the store refuses a stale one.

```merge-risk v1
risk: medium
rollback: revert; leases expire after the TTL
blast-radius: the nightly sync only
```


Question: {"id": "q2", "concept": "stale-leader-writes", "type": "text", "prompt": "A worker holding the lease pauses longer than its TTL, and another worker takes over. Why is the lease alone not enough here, and what stops the first worker's late writes?"}
Key: {"explanation": "After a pause (GC, network) both workers can believe they lead. Every write carries the fencing token from acquire, and write raises LeaseLost when the stored token differs, so the stale leader's late writes are refused.", "hunk": "+    def write(self, name, token, key, value):\n+        held = self.backend.get(name)\n+        if not held or held[\"token\"] != token:\n+            raise LeaseLost(name)", "rubric": {"pass": "Says the paused worker still believes it leads, and that each write carries a fencing token the store compares with the current one, refusing the stale token.", "partial": "Mentions the fencing token or the stale leader, but not how the store uses the token to refuse the late write."}}

Draft: 

Tip number: 1
