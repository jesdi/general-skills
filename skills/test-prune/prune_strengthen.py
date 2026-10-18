"""strengthen-start, strengthen-verify and strengthen-finish: prove each strengthen item kills the
mutant ids it names (surviving on the base, killed on the head), keep what the reviewer accepts,
lower the baseline, and (unattended) open the strengthen PR from the base commit."""
from __future__ import annotations

import json

from prune_repo import KILLED, SURVIVING, BadInput, Fail, emit, ratchet_lines
from prune_review import check_reviews
from prune_runs import equivalents_section
from prune_source import test_sources

TRAILER = "Test-Prune-Item"
BASELINE = "backend/mutation_baseline.json"


def _branch(ctx) -> str:
    return f"chore/test-strengthen-{ctx.date}"


def _work_item(item: dict) -> dict:
    candidate = item.get("candidate") or {}
    return {"id": item["id"], "kind": item["kind"], "mutant_ids": item.get("mutant_ids", []),
            "module": item.get("module"), "function": item.get("function"),
            "test": item.get("test") or item.get("test_id"), "assertion": item.get("assertion"),
            "source": candidate.get("source"), "reason": item.get("fixer_reason"), "review": item["reason"]}


def _detach_at_base(ctx, base: dict) -> None:
    ctx.repo.require_new_branch(_branch(ctx))
    if ctx.repo.ref(base["branch"]) != base["commit"]:
        raise BadInput(f"{base['branch']} moved since collect ({base['commit']}): collect again")
    ctx.repo.git("checkout", "-q", "--detach", base["commit"])


def strengthen_start(ctx) -> int:
    repo, base = ctx.repo, ctx.base()
    items = ctx.state.load("batch.json")["strengthen"]
    if not items:
        emit("nothing to strengthen")
        return 0
    repo.require_clean()
    if base["mode"] == "unattended":
        _detach_at_base(ctx, base)
    start = repo.head()
    work = [_work_item(i) for i in items]
    ctx.state.save("strengthen.json", {"start": start, "verified": False, "items": work,
                                       "status": {i["id"]: "open" for i in work}})
    path = ctx.state.save("strengthen-input.json", {"start": start, "items": work})
    emit(f"{len(work)} strengthen items → {path}\nCommit each item test-first on top of {start[:12]}: one or more "
         f"commits per item, each touching only backend/tests/ and ending with the trailer `{TRAILER}: <item id>`.")
    return 0


def _history(repo, start: str) -> list:
    """[(sha, trailer values, paths)] for every commit since start, oldest first."""
    out = []
    for sha in repo.git("rev-list", "--reverse", f"{start}..HEAD").split():
        values = repo.git("log", "-1", f"--format=%(trailers:key={TRAILER},valueonly)", sha).split("\n")
        paths = repo.git("diff-tree", "--no-commit-id", "--name-only", "-r", sha).split()
        out.append((sha, [v.strip() for v in values if v.strip()], paths))
    return out


def commit_problems(sha: str, items: list, paths: list, known: set) -> list:
    problems = [f"{sha[:12]}: touches {p}, outside backend/tests/" for p in paths if not p.startswith("backend/tests/")]
    if len(items) != 1 or items[0] not in known:
        problems.insert(0, f"{sha[:12]}: needs exactly one `{TRAILER}` trailer naming a strengthen item, has {items}")
    return problems


def _commits(repo, start: str, known: set) -> list:
    """[(sha, item id)] since start; every commit must name a known item and touch only backend/tests/."""
    if repo.run_git("merge-base", "--is-ancestor", start, "HEAD").returncode:
        raise BadInput(f"HEAD does not descend from the strengthen start {start}")
    errors = ["merge commits since the strengthen start"] if repo.git(
        "rev-list", "--min-parents=2", f"{start}..HEAD").strip() else []
    history = _history(repo, start)
    for sha, items, paths in history:
        errors += commit_problems(sha, items, paths, known)
    if errors:
        raise BadInput("strengthen commits rejected:\n" + "\n".join(errors))
    return [(sha, items[0]) for sha, items, _ in history]


def _revert(repo, item_id: str, commits: list, why: str) -> None:
    shas = [sha for sha, item in reversed(commits) if item == item_id]
    if not shas:
        return
    if repo.run_git("revert", "--no-commit", *shas).returncode:
        repo.run_git("revert", "--abort")
        raise Fail(f"reverting strengthen item {item_id} conflicts with a later item's commits")
    repo.git("commit", "-q", "--allow-empty", "-m", f"Revert test-prune item {item_id}", "-m", why,
             "-m", f"{TRAILER}: {item_id}")


def item_proof(base_codes: dict, codes: dict, item: dict, has_commits: bool) -> dict:
    """Per mutant id the base and head exit codes; proven when every id survived on the base and is
    killed on the head, and the item has a commit."""
    module = item.get("module")
    mutants = {m: {"base": base_codes.get(module, {}).get(m), "head": codes.get(module, {}).get(m)}
               for m in item["mutant_ids"]}
    proven = has_commits and all(e["base"] in SURVIVING and e["head"] in KILLED for e in mutants.values())
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
    known = {i["id"] for i in st["items"]}
    while True:
        commits = _commits(ctx.repo, st["start"], known)
        failing = _judge(ctx, base, st, proof, commits)
        for item_id in failing:
            _revert(ctx.repo, item_id, commits, "Its mutant ids are not all killed on the head while surviving on the base.")
            st["status"][item_id] = proof[item_id]["status"] = "reverted"
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
    emit("\n".join(["Strengthen proof (survives on the base, killed on the head, per mutant id):"] + _proof_lines(proof)))
    return 0


def changed_tests(before: str, after: str, path: str) -> list:
    """Test ids in the file at path that are new or changed from before to after."""
    old, rel = test_sources(before), path[len("backend/"):]
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


def _body(base: dict, st: dict, proof: dict, accepted: list, tests: dict, new: dict, ratchet: str, equivalents) -> str:
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
    return "\n".join(lines + equivalents_section(equivalents)) + "\n"


def strengthen_finish(ctx) -> int:
    repo, base = ctx.repo, ctx.base()
    st = ctx.state.load("strengthen.json")
    if not st["verified"]:
        raise BadInput("run strengthen-verify first")
    repo.require_clean()
    reviews = check_reviews(ctx.reviews(), [i for i, s in st["status"].items() if s == "proven"])
    unattended = base["mode"] == "unattended"
    if unattended:
        repo.require_new_branch(_branch(ctx))
    try:
        return _finish(ctx, base, st, reviews, unattended)
    finally:
        if unattended:
            repo.leave_on(base["branch"], base["commit"])


def _keep_accepted(ctx, base: dict, st: dict, reviews: dict) -> tuple[dict, list]:
    commits = _commits(ctx.repo, st["start"], {i["id"] for i in st["items"]})
    for item_id, review in reviews.items():
        if review["verdict"] == "reject":
            _revert(ctx.repo, item_id, commits, f"Rejected by the reviewer: {review['reason']}")
            st["status"][item_id] = "rejected"
    proof = _prove(ctx, base, st)
    return proof, [i for i, s in st["status"].items() if s == "proven"]


def _lower_baseline(repo, base: dict) -> tuple[str, dict]:
    """The ratchet, test and crap targets, then the baseline update behind the equivalent guard."""
    ratchet = repo.must_make("mutation-check")
    repo.must_make("test")
    repo.must_make("crap")
    repo.must_make("mutation-baseline")
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
        emit("test-prune: nothing proven and accepted to strengthen; no branch, no PR")
        return 0
    ratchet, new = _lower_baseline(repo, base)
    commits = [(sha, items[0]) for sha, items, _ in _history(repo, st["start"]) if items]
    tests = {i: _item_tests(repo, commits, i) for i in accepted}
    body = _body(base, st, proof, accepted, tests, new, ratchet, ctx.state.load("batch.json")["equivalents"])
    if not unattended:
        emit(f"Strengthen commits kept on {repo.branch()}:\n\n{body}")
        return 0
    branch = _branch(ctx)
    repo.git("branch", branch)
    repo.push(branch)
    title = f"test: strengthen {len(accepted)} weak spot{'' if len(accepted) == 1 else 's'}"
    url = repo.open_pr(base["branch"], branch, title, ctx.state.save_text("strengthen-pr.md", body))
    emit(f"Opened {url}\n\n{body}")
    return 0
