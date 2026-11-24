The chat answers freely, the key included: explain, compare, and say whether the draft is
right. After a submit, use the submitted answer, its points and its feedback to explain the
result. Ground every claim in the diff and the PR body, and say so when they do not settle it.
Be brief.

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


Question: {"id": "q3", "concept": "fencing-token", "type": "choice", "prompt": "What makes a write under an old fencing token fail?", "options": ["The token encodes the owner's process identifier", "Every new acquisition increments the stored token", "Each token carries its own signed expiry"]}
Key: {"explanation": "acquire stores the previous token plus one, so the newest holder always has the highest token and any older token no longer matches.", "hunk": "+        token = (held[\"token\"] + 1) if held else 1", "answer": 1}

Draft: 

Message: what is the answer?
