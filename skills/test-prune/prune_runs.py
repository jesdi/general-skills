"""apply (interactive) and prune (unattended): apply the accepted deletions, run the safety check,
restore every culprit, then commit; unattended also branches, gates, pushes and opens the PR."""
from __future__ import annotations

from prune_repo import BadInput, Fail, emit, fenced, ratchet_lines
from prune_source import delete_tests, split_id

CAP = 10


def _targets(ids) -> dict:
    """{test file: {(classes, function)}} for the test ids."""
    out: dict = {}
    for test_id in ids:
        path, classes, name = split_id(test_id)
        out.setdefault(path, set()).add((classes, name))
    return out


class DeletionSet:
    """Writes a deletion set over the base commit's content of every file the whole batch touches."""

    def __init__(self, repo, commit: str, ids):
        self.repo, self.commit, self.files = repo, commit, sorted(_targets(ids))

    def write(self, ids) -> None:
        targets = _targets(ids)
        for path in self.files:
            source = self.repo.show(self.commit, f"backend/{path}")
            self.repo.write(f"backend/{path}", delete_tests(source, targets[path]) if path in targets else source)


def families(ids) -> list:
    """Test ids grouped by test function, in batch order: a parametrize family is deleted, capped,
    bisected and restored as one unit, because deleting one case deletes the function."""
    groups: dict = {}
    for test_id in ids:
        groups.setdefault(test_id.split("[")[0], []).append(test_id)
    return list(groups.values())


def capped(deletions: list) -> tuple[list, list]:
    """Every whole family that still fits within CAP ids, in batch order; the rest is left for next week."""
    taken, count = set(), 0
    for family in families(d["test_id"] for d in deletions):
        if count + len(family) > CAP:
            continue
        taken.update(family)
        count += len(family)
    return [d for d in deletions if d["test_id"] in taken], [d for d in deletions if d["test_id"] not in taken]


def _culprit(family: list, failing: dict, passing: dict) -> list:
    name = family[0] if len(family) == 1 else family[0].split("[")[0]
    return [{"test_id": name, "test_ids": family, "module": m, "survivors_with": failing["modules"][m][0],
             "baseline": failing["modules"][m][1], "survivors_after": passing["modules"].get(m, [None])[0]}
            for m in failing["failed"]]


def _flat(units) -> list:
    return [test_id for unit in units for test_id in unit]


def safety_check(repo, base: dict, ids: list) -> tuple[list, list, dict]:
    """Restore every deletion whose absence lets a module exceed its baseline; returns (kept ids, restored,
    ratchet of the kept set) with the kept set written to the tree. Survivors only grow as tests go, so
    the smallest failing prefix of families ends with a culprit; repeat until the whole kept set passes."""
    deletion_set = DeletionSet(repo, base["commit"], ids)
    results = {(): base["ratchet"]}

    def check(units: list) -> dict:
        key = tuple(_flat(units))
        if key not in results:
            deletion_set.write(key)
            results[key] = repo.cold_ratchet()
        return results[key]

    kept, restored, lo = families(ids), [], 0
    while not check(kept)["ok"]:
        hi = len(kept)
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if check(kept[:mid])["ok"]:
                lo = mid
            else:
                hi = mid
        restored += _culprit(kept[hi - 1], check(kept[:hi]), check(kept[:hi - 1]))
        del kept[hi - 1]
        lo = hi - 1
    deletion_set.write(_flat(kept))
    return _flat(kept), restored, check(kept)


def _commit(repo, kept: list, by_id: dict) -> str:
    subject = f"test: delete {len(kept)} redundant test{'' if len(kept) == 1 else 's'}"
    body = "Each one is contained by another test and the mutation ratchet passes without it:\n" + "\n".join(
        f"- {t} (contained by {by_id[t]['into']})" for t in kept)
    repo.git("add", "--", *(f"backend/{path}" for path in sorted(_targets(kept))))
    repo.git("commit", "-q", "-m", subject, "-m", body)
    return subject


def restored_lines(restored: list) -> list:
    return [f"- `{r['test_id']}`{_cases(r)}: {r['module']} had {r['survivors_with']} survivors with it deleted, baseline "
            f"{r['baseline']}; {r['survivors_after']} after restoring it" for r in restored]


def _cases(restored: dict) -> str:
    return f" ({len(restored['test_ids'])} parametrized cases)" if len(restored["test_ids"]) > 1 else ""


def suggestions_section(out: dict) -> list:
    items = [i for i in out["strengthen"] if i["kind"] == "candidate"]
    if not items:
        return []
    return ["", "## Strengthen suggestions", "",
            "The reviewer accepted these \"strengthen with this assertion\" verdicts. test-prune does not apply "
            "them; a human adds each assertion.", ""] + [
        f"- `{i['id']}`: add `{i['assertion']}`. {i['reason']}" for i in items]


def equivalents_section(equivalents: list) -> list:
    if not equivalents:
        return []
    return ["", "## Equivalent proposals", "",
            "The reviewer accepted these as equivalent mutants. test-prune never declares an equivalent; "
            "a human adds them to `backend/mutation_baseline.json` after checking each one.", ""] + [
        f"- `{e['group']}`: {', '.join(f'`{m}`' for m in e['mutant_ids'])}. {e['reason']}" for e in equivalents]


def _deletions_report(kept: list, by_id: dict, restored: list, left: list, ratchet: dict) -> list:
    lines = [f"## Deleted tests ({len(kept)})", ""]
    lines += [f"- `{t}`, contained by `{by_id[t]['into']}`: {by_id[t]['reason']}" for t in kept]
    if restored:
        lines += ["", f"## Restored by the safety check ({len({t for r in restored for t in r['test_ids']})})", ""]
        lines += restored_lines(restored)
    if left:
        lines += ["", f"## Left for next week ({len(left)})", "",
                  f"Accepted, over the cap of {CAP} deletions per run (a parametrized test counts each case):", ""]
        lines += [f"- `{d['test_id']}`, contained by `{d['into']}`" for d in left]
    lines += ["", "## Mutation ratchet (cold run on this branch)", ""]
    lines += [f"- {line}" for line in ratchet_lines(ratchet)]
    return lines


def require_at_base(repo, base: dict, command: str) -> None:
    repo.require_clean()
    if repo.branch() != base["branch"] or repo.head() != base["commit"]:
        raise BadInput(f"{command} runs on {base['branch']} at the collected commit {base['commit']}; "
                       "check it out, or collect again after moving it")


def _apply_and_commit(repo, base: dict, deletions: list) -> tuple[list, list, dict]:
    """Safety check, make test and the commit; any failure puts every touched test file back."""
    by_id = {d["test_id"]: d for d in deletions}
    ids = [d["test_id"] for d in deletions]
    try:
        kept, restored, ratchet = safety_check(repo, base, ids)
        if kept:
            repo.must_make("test")
            _commit(repo, kept, by_id)
    except BaseException:
        DeletionSet(repo, base["commit"], ids).write([])
        raise
    return kept, restored, ratchet


def apply(ctx) -> int:
    repo, base = ctx.repo, ctx.base("interactive")
    require_at_base(repo, base, "apply")
    deletions = ctx.state.load("batch.json")["deletions"]
    if not deletions:
        emit("no accepted deletions to apply")
        return 0
    kept, restored, ratchet = _apply_and_commit(repo, base, deletions)
    head = (f"Committed on {base['branch']}: {repo.git('log', '-1', '--format=%s').strip()}" if kept
            else "Every deletion was restored: nothing committed")
    by_id = {d["test_id"]: d for d in deletions}
    emit("\n".join([head, ""] + _deletions_report(kept, by_id, restored, [], ratchet)))
    return 0


def _nothing_to_prune(ctx, base: dict, out: dict) -> str:
    if not ctx.state.load("fixer-input.json")["candidates"]:
        reason = "no protected candidates" if base["protected"] else "no candidates"
    else:
        reason = f"no accepted deletions ({len(out['rejected'])} rejected, {len(out['skipped'])} skipped)"
    return "\n".join([f"test-prune: {reason}; no branch, no PR"] + suggestions_section(out)
                     + equivalents_section(out["equivalents"]))


def prune(ctx) -> int:
    repo, base = ctx.repo, ctx.base("unattended")
    out = ctx.state.load("batch.json")
    if not out["deletions"]:
        emit(_nothing_to_prune(ctx, base, out))
        return 0
    branch = f"chore/test-prune-{ctx.date}"
    require_at_base(repo, base, "prune")
    repo.require_new_branch(branch)
    repo.git("checkout", "-q", "--detach", base["commit"])
    try:
        return _prune_detached(ctx, base, out, branch)
    finally:
        repo.leave_on(base["branch"], base["commit"])


def _prune_detached(ctx, base: dict, out: dict, branch: str) -> int:
    repo = ctx.repo
    by_id = {d["test_id"]: d for d in out["deletions"]}
    take, left = capped(out["deletions"])
    if not take:
        emit(f"test-prune: no accepted deletion fits the cap of {CAP} test ids; no branch, no PR")
        return 0
    kept, restored, ratchet = safety_check(repo, base, [d["test_id"] for d in take])
    if not kept:
        emit("\n".join(["test-prune: the safety check restored every accepted deletion; no branch, no PR", ""]
                       + restored_lines(restored)))
        return 0
    report = _deletions_report(kept, by_id, restored, left, ratchet)
    try:
        return _publish(ctx, base, out, branch, kept, report)
    except Fail as exc:  # the job summary keeps what the safety check found
        raise Fail("\n".join([f"{exc}", ""] + report)) from None


def _publish(ctx, base: dict, out: dict, branch: str, kept: list, report: list) -> int:
    """make test, the commit, the branch, make prune-gate, then the push and the PR."""
    repo = ctx.repo
    repo.must_make("test")
    subject = _commit(repo, kept, {d["test_id"]: d for d in out["deletions"]})
    repo.git("branch", branch)
    gate = repo.make("prune-gate", f"BASE={base['commit']}", f"HEAD={branch}")
    gate_output = (gate.stdout + gate.stderr).strip()
    if gate.returncode:
        emit("\n".join([f"test-prune: make prune-gate failed on {branch}; nothing pushed, no PR", "", fenced(gate_output), ""]
                       + report))
        return 1
    body = "\n".join([f"Weekly test prune from `{base['branch']}` at {base['commit'][:12]}. "
                      "Delete-only; merge it to accept the batch.", ""] + report
                     + ["", "## Prune gate", "", "```", gate_output, "```"]
                     + suggestions_section(out) + equivalents_section(out["equivalents"])) + "\n"
    repo.push(branch)
    url = repo.open_pr(base["branch"], branch, subject, ctx.state.save_text("prune-pr.md", body))
    emit(f"Opened {url}\n\n{body}")
    return 0
