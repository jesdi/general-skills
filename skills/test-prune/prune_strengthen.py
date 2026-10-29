"""strengthen-start, strengthen-verify and strengthen-finish: prove each strengthen item kills the
mutant ids it names (surviving on the base, killed on the head), keep what the reviewer accepts,
lower the baseline, and (unattended) open the strengthen PR from the base commit."""
from __future__ import annotations

import json
import re

from prune_repo import KILLED, SURVIVING, BadInput, Fail, Repo, ratchet_lines
from prune_review import check_reviews, equivalents_section, suggestions_section
from prune_source import test_sources

TRAILER = "Test-Prune-Item"
# Read from the whole message, not git's trailer block: git only takes trailers from the
# last paragraph, and a Co-Authored-By paragraph appended after ours would hide it.
TRAILER_LINE = re.compile(rf"^{TRAILER}:[ \t]*(.*?)[ \t]*$", re.MULTILINE)
BASELINE = "backend/mutation_baseline.json"


def _branch(ctx) -> str:
    return f"chore/test-strengthen-{ctx.date}"


def _work_item(item: dict) -> dict:
    candidate = item.get("candidate") or {}
    return {"id": item["id"], "kind": item["kind"], "mutant_ids": item.get("mutant_ids", []),
            "module": item.get("module"), "function": item.get("function"),
            "test": item.get("test") or item.get("test_id"), "assertion": item.get("assertion"),
            "source": candidate.get("source"), "reason": item.get("fixer_reason"), "review": item["reason"]}


def strengthen_start(ctx) -> int:
    repo, base = ctx.repo, ctx.base()
    out = ctx.state.load("batch.json")
    items = [i for i in out["strengthen"] if i["kind"] == "weak_spot"]
    if not items:
        ctx.state.emit("\n".join(["nothing to strengthen"] + suggestions_section(out)
                                  + equivalents_section(out["equivalents"], shown=5)))
        ctx.finished = True
        return 0
    if base["mode"] == "unattended":
        repo.detach_at(base, _branch(ctx), "strengthen-start")
    else:
        repo.require_clean()
    start = repo.head()
    work = [_work_item(i) for i in items]
    ctx.state.save("strengthen.json", {"start": start, "verified": False, "items": work,
                                       "status": {i["id"]: "open" for i in work}, "closed": {}})
    path = ctx.state.save("strengthen-input.json", {"start": start, "items": work})
    ctx.state.emit(f"{len(work)} strengthen items → {path}\nWrite each item's tests test-first on top of {start[:12]}, touching "
         f"only backend/tests/, and commit each item with `prune.py strengthen-commit --item <item id>` (one item "
         f"per call; an item may take several). Never run `git commit` yourself.")
    return 0


def _history(repo, start: str) -> list:
    """[(sha, trailer values, paths)] for every commit since start, oldest first."""
    out = []
    for sha in repo.git("rev-list", "--reverse", f"{start}..HEAD").split():
        values = TRAILER_LINE.findall(repo.git("log", "-1", "--format=%B", sha))
        paths = repo.git("diff-tree", "--no-commit-id", "--name-only", "-r", sha).split()
        out.append((sha, [v for v in values if v], paths))
    return out


def commit_problems(sha: str, items: list, paths: list, known: set) -> list:
    problems = [f"{sha[:12]}: touches {p}, outside backend/tests/" for p in paths if not p.startswith("backend/tests/")]
    if len(items) != 1 or items[0] not in known:
        problems.insert(0, f"{sha[:12]}: needs exactly one `{TRAILER}` trailer naming a strengthen item, has {items}")
    return problems


def reopened(history: list, closed: dict) -> list:
    """Commits naming an item after test-prune closed it (reverted or rejected at the recorded commit)."""
    position = {sha: i for i, (sha, _, _) in enumerate(history)}
    return [f"{sha[:12]}: {items[0]} was closed at {closed[items[0]][:12]}; new commits for it are refused"
            for i, (sha, items, _) in enumerate(history)
            if len(items) == 1 and items[0] in closed and i > position.get(closed[items[0]], -1)]


def _recovery(start: str) -> str:
    """How to redo rejected commits with checkout, edits and commits alone (no reset)."""
    return (f"Redo them one item per commit: `git checkout {start}`, then write each item's tests again and "
            f"commit each item with `prune.py strengthen-commit --item <item id>`.")


def _require_descends(repo, start: str) -> None:
    if repo.run_git("merge-base", "--is-ancestor", start, "HEAD").returncode:
        raise BadInput(f"HEAD does not descend from the strengthen start {start}")


def _commits(repo, st: dict) -> list:
    """[(sha, item id)] since start; every commit must name a known, open item and touch only backend/tests/."""
    start = st["start"]
    _require_descends(repo, start)
    errors = ["merge commits since the strengthen start"] if repo.git(
        "rev-list", "--min-parents=2", f"{start}..HEAD").strip() else []
    history = _history(repo, start)
    known = {i["id"] for i in st["items"]}
    for sha, items, paths in history:
        errors += commit_problems(sha, items, paths, known)
    errors += reopened(history, st["closed"])
    if errors:
        raise BadInput("strengthen commits rejected:\n" + "\n".join(errors) + "\n" + _recovery(start))
    return [(sha, items[0]) for sha, items, _ in history]


def strengthen_commit(ctx) -> int:
    """Commit the working tree's changes under backend/tests/ as one commit for one open item; the
    trailer is written here, so a commit can never name two items."""
    repo, st, item_id = ctx.repo, ctx.state.load("strengthen.json"), ctx.args.item
    status = st["status"].get(item_id)
    if status not in ("open", "proven"):
        raise BadInput(f"{item_id} is not an open strengthen item (status: {status or 'unknown'})")
    _require_descends(repo, st["start"])
    changed = repo.changes()
    outside = [p for p in changed if not p.startswith("backend/tests/")]
    if outside:
        raise BadInput("changes outside backend/tests/, discard them first:\n" + "\n".join(outside))
    if not changed:
        raise BadInput(f"nothing to commit for {item_id}: write its tests under backend/tests/ first")
    repo.git("add", "-A", "--", "backend/tests/")
    repo.git("commit", "-q", "-m", f"test: strengthen {item_id}", "-m", f"{TRAILER}: {item_id}")
    ctx.state.emit(repo.head())
    return 0


def _revert(repo, item_id: str, commits: list, why: str) -> None:
    shas = [sha for sha, item in reversed(commits) if item == item_id]
    if not shas:
        return
    if repo.run_git("revert", "--no-commit", *shas).returncode:
        repo.run_git("revert", "--abort")
        raise Fail(f"reverting strengthen item {item_id} conflicts with a later item's commits")
    repo.git("commit", "-q", "--allow-empty", "-m", f"Revert test-prune item {item_id}", "-m", why,
             "-m", f"{TRAILER}: {item_id}")


def _close(repo, st: dict, item_id: str, commits: list, status: str, why: str) -> None:
    """Revert the item's commits and record where it closed: later commits for it are refused."""
    _revert(repo, item_id, commits, why)
    st["status"][item_id] = status
    st["closed"][item_id] = repo.head()


def item_proof(base_codes: dict, codes: dict, item: dict, has_commits: bool) -> dict:
    """Per mutant id the base and head exit codes; proven when the item names at least one id, every id
    survived on the base and is killed on the head, and the item has a commit."""
    module = item.get("module")
    mutants = {m: {"base": base_codes.get(module, {}).get(m), "head": codes.get(module, {}).get(m)}
               for m in item["mutant_ids"]}
    proven = has_commits and bool(mutants) and all(e["base"] in SURVIVING and e["head"] in KILLED for e in mutants.values())
    return {"status": "proven" if proven else "unproven", "module": module, "mutants": mutants}


def _judge(ctx, base: dict, st: dict, proof: dict, commits: list) -> list:
    """One cold run; records each open item's proof and returns the unproven ones."""
    ctx.repo.cold_mutation()
    codes = ctx.repo.mutant_codes()
    items = {i["id"]: i for i in st["items"]}
    failing = []
    for item_id in [i for i, s in st["status"].items() if s in ("open", "proven")]:
        proof[item_id] = item_proof(base["mutants"], codes, items[item_id], any(c[1] == item_id for c in commits))
        st["status"][item_id] = proof[item_id]["status"]
        if st["status"][item_id] == "unproven":
            failing.append(item_id)
    return failing


def _prove(ctx, base: dict, st: dict) -> dict:
    """Cold runs until every remaining item is proven; unproven items are reverted between runs."""
    path = ctx.state.path / "strengthen-proof.json"
    proof = json.loads(path.read_text())["items"] if path.exists() else {}
    while True:
        commits = _commits(ctx.repo, st)
        failing = _judge(ctx, base, st, proof, commits)
        for item_id in failing:
            _close(ctx.repo, st, item_id, commits, "reverted",
                   "Its mutant ids are not all killed on the head while surviving on the base.")
            proof[item_id]["status"] = "reverted"
        ctx.state.save("strengthen.json", st)
        ctx.state.save("strengthen-proof.json", {"start": st["start"], "head": ctx.repo.head(), "items": proof})
        if not failing:
            return proof


def _proof_lines(proof: dict) -> list:
    lines = []
    for item_id, entry in proof.items():
        lines.append(f"- {item_id}: {entry['status']}")
        lines += [f"    {m}: base exit code {e['base']}, head exit code {e['head']}" for m, e in entry["mutants"].items()]
    return lines


def strengthen_verify(ctx) -> int:
    base = ctx.base()
    st = ctx.state.load("strengthen.json")
    ctx.repo.require_clean()
    proof = _prove(ctx, base, st)
    st["verified"] = True
    ctx.state.save("strengthen.json", st)
    ctx.state.emit("\n".join(["Strengthen proof (survives on the base, killed on the head, per mutant id):"] + _proof_lines(proof)))
    return 0


def changed_tests(before: str, after: str, path: str) -> list:
    """Test ids in the file at path that are new or changed from before to after."""
    old, rel = test_sources(before), path[len(Repo.test_path("")):]
    return [f"{rel}::{name}" for name, text in test_sources(after).items() if old.get(name) != text]


def _commit_tests(repo, sha: str) -> list:
    paths = repo.git("diff-tree", "--no-commit-id", "--name-only", "-r", sha).split()
    return [t for path in paths if path.endswith(".py")
            for t in changed_tests(repo.show(f"{sha}^", path) or "", repo.show(sha, path) or "", path)]


def _item_tests(repo, commits: list, item_id: str) -> list:
    changed: list = []
    for sha in (sha for sha, item in commits if item == item_id):
        changed += [t for t in _commit_tests(repo, sha) if t not in changed]
    return changed


def _guard_equivalents(repo, base: dict, new: dict) -> None:
    gained = {m: sorted(set(e.get("equivalent", {})) - set(base["baseline"].get(m, {}).get("equivalent", {})))
              for m, e in new.items()}
    gained = {m: ids for m, ids in gained.items() if ids}
    if gained:
        repo.git("checkout", "-q", "HEAD", "--", BASELINE)
        raise Fail("guard: the baseline update gained equivalent ids test-prune never declares: "
                   + "; ".join(f"{m}: {', '.join(ids)}" for m, ids in gained.items()))


def _test_lines(items: dict, accepted: list, tests: dict) -> list:
    lines = []
    for item_id in accepted:
        ids = items[item_id]["mutant_ids"]
        kills = f"now kills {', '.join(f'`{m}`' for m in ids)}" if ids else f"adds `{items[item_id]['assertion']}`"
        lines += [f"- `{t}` ({item_id}) {kills}" for t in tests[item_id]] or [f"- {item_id}: {kills}"]
    return lines


def _body(base: dict, st: dict, proof: dict, accepted: list, tests: dict, new: dict, ratchet: str, out: dict) -> str:
    lines = [f"Strengthened tests from `{base['branch']}` at {base['commit'][:12]}, independent of the prune PR. "
             "Every mutant id listed survived the cold mutation run on the base and is killed by the cold run on "
             "this branch.", "", "## Changed tests", ""]
    lines += _test_lines({i["id"]: i for i in st["items"]}, accepted, tests)
    lines += ["", "## Proof per mutant id", ""]
    lines += [f"- `{m}` ({proof[i]['module']}): survived the base run (exit code {e['base']}), killed on this "
              f"branch (exit code {e['head']})" for i in accepted for m, e in proof[i]["mutants"].items()]
    lines += ["", "## Baseline survivors", ""]
    lines += [f"- {m}: {base['baseline'].get(m, {}).get('survivors', 'new')} → {e['survivors']}" for m, e in new.items()]
    lines += ["", "## Mutation ratchet", "", "```", ratchet.strip(), "```"]
    return "\n".join(lines + suggestions_section(out) + equivalents_section(out["equivalents"])) + "\n"


def _finish_reviews(ctx, st: dict) -> dict:
    """Preconditions: verified, clean, HEAD still the proven head, a new branch name, valid reviews."""
    if not st["verified"]:
        raise BadInput("run strengthen-verify first")
    ctx.repo.require_clean()
    proven_head = ctx.state.load("strengthen-proof.json")["head"]
    if ctx.repo.head() != proven_head:
        raise BadInput(f"HEAD moved since strengthen-verify proved {proven_head[:12]}: run strengthen-verify again")
    if ctx.base()["mode"] == "unattended":
        ctx.repo.require_new_branch(_branch(ctx))
    return check_reviews(ctx.reviews(), [i for i, s in st["status"].items() if s == "proven"])


def strengthen_finish(ctx) -> int:
    repo, base = ctx.repo, ctx.base()
    st = ctx.state.load("strengthen.json")
    unattended = base["mode"] == "unattended"
    leave = unattended
    try:
        code = _finish(ctx, base, st, _finish_reviews(ctx, st), unattended)
    except BadInput:
        leave = False  # HEAD stays at the proven head: fix the input and rerun
        raise
    finally:
        if leave:
            repo.leave_on(base["branch"], base["commit"])
    ctx.finished = code == 0
    return code


def _keep_accepted(ctx, base: dict, st: dict, reviews: dict) -> tuple[dict, list]:
    rejected = [i for i, review in reviews.items() if review["verdict"] == "reject"]
    if rejected:  # a revert changes the head: prove the rest again, cold
        commits = _commits(ctx.repo, st)
        for item_id in rejected:
            _close(ctx.repo, st, item_id, commits, "rejected", f"Rejected by the reviewer: {reviews[item_id]['reason']}")
        proof = _prove(ctx, base, st)
    else:
        proof = ctx.state.load("strengthen-proof.json")["items"]
    return proof, [i for i, s in st["status"].items() if s == "proven"]


def _lower_baseline(repo, base: dict) -> tuple[str, dict]:
    """The ratchet, test and crap targets, then the baseline update behind the equivalent guard."""
    ratchet = repo.must_make("mutation-check")
    repo.must_make("test")
    repo.must_make("crap")
    repo.must_make("mutation-baseline")
    repo.own(BASELINE)  # only once the target passed: after a failure its bytes are not provably the run's
    new = json.loads((repo.root / BASELINE).read_text())
    _guard_equivalents(repo, base, new)
    if repo.git("status", "--porcelain", "--", BASELINE).strip():
        repo.git("add", "--", BASELINE)
        repo.git("commit", "-q", "-m", "test: lower the mutation baseline",
                 "-m", "The strengthened tests kill mutants that survived on the base.")
    return "\n".join(ratchet_lines({"output": ratchet})), new


def _finish(ctx, base: dict, st: dict, reviews: dict, unattended: bool) -> int:
    repo = ctx.repo
    proof, accepted = _keep_accepted(ctx, base, st, reviews)
    if not accepted:
        ctx.state.emit("test-prune: nothing proven and accepted to strengthen; no branch, no PR")
        return 0
    ratchet, new = _lower_baseline(repo, base)
    commits = [(sha, items[0]) for sha, items, _ in _history(repo, st["start"]) if items]
    tests = {i: _item_tests(repo, commits, i) for i in accepted}
    body = _body(base, st, proof, accepted, tests, new, ratchet, ctx.state.load("batch.json"))
    if not unattended:
        ctx.state.emit(f"Strengthen commits kept on {repo.branch()}:\n\n{body}")
        return 0
    branch = _branch(ctx)
    repo.git("branch", branch)
    repo.push(branch)
    title = f"test: strengthen {len(accepted)} weak spot{'' if len(accepted) == 1 else 's'}"
    path, body = ctx.state.save_body("strengthen-pr.md", body)
    url = repo.open_pr(base["branch"], branch, title, path)
    ctx.state.emit(f"Opened {url}\n\n{body}")
    return 0
