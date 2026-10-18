"""collect, review-input and batch: from the candidate report to the accepted batch; edits nothing."""
from __future__ import annotations

import json
from pathlib import PurePosixPath

from prune_repo import BadInput, Fail, ratchet_lines
from prune_source import class_refusals, describe, families, undeletable

CAP = 10  # deleted test ids per unattended run; a parametrized test counts each case

CANDIDATE_VERDICTS = {"keep", "delete", "strengthen"}
GROUP_VERDICTS = {"strengthen", "equivalent"}


class Sources:
    """Test files as of one commit, keyed by their backend-relative path."""

    def __init__(self, repo, commit: str):
        self.repo, self.commit, self.files = repo, commit, {}

    def text(self, path: str) -> str | None:
        if path not in self.files:
            self.files[path] = self.repo.show_test(self.commit, path)
        return self.files[path]

    def describe(self, test_id: str) -> dict:
        text = self.text(test_id.split("::")[0])
        found = describe(text, test_id) if text is not None else None
        return found or {"source": None, "assertions": []}

    def test(self, test_id: str) -> dict:
        return dict(test_id=test_id, **self.describe(test_id))

    def class_refusals(self, test_ids: list) -> dict:
        by_file: dict = {}
        for test_id in test_ids:
            by_file.setdefault(test_id.split("::")[0], []).append(test_id)
        refused: dict = {}
        for path, ids in by_file.items():
            refused.update(class_refusals(self.text(path), ids))
        return refused

    def undeletable(self, test_id: str) -> str | None:
        path = PurePosixPath(test_id.split("::")[0])
        if path.parts[0] != "tests" or path.name == "conftest.py":
            return f"{path} is not a deletable test module"
        text = self.text(str(path))
        return f"{path} not found at the base commit" if text is None else undeletable(text, test_id)


def _fixer_input(repo, commit: str, report: dict) -> dict:
    sources = Sources(repo, commit)
    return {
        "candidates": [dict(c, **sources.describe(c["test_id"]),
                            containers=[sources.test(t) for t in c.get("contained_by", [])])
                       for c in report["candidates"]],
        "weak_spots": [{"group": f"{PurePosixPath(w['module']).stem}.{w['function']}", **w}
                       for w in report["weak_spots"]],
    }


def collect(ctx) -> int:
    repo, args = ctx.repo, ctx.args
    repo.require_clean()
    branch = repo.branch()
    protected = args.unattended or args.protected_only
    ctx.state.reset()
    if args.unattended:
        repo.must_make("test")
    ratchet = repo.cold_ratchet()
    if not ratchet["ok"]:
        raise Fail("the base fails the mutation ratchet, fix it before pruning:\n" + "\n".join(ratchet_lines(ratchet)))
    mutants = repo.mutant_codes()
    repo.must_make("test-candidates", *(["ARGS=--protected-only"] if protected else []))
    report = json.loads((repo.backend / ".mutation" / "test-candidates.json").read_text())
    commit = repo.head()
    ctx.state.save("base.json", {
        "mode": "unattended" if args.unattended else "interactive", "protected": protected, "commit": commit,
        "branch": branch, "baseline": json.loads((repo.backend / "mutation_baseline.json").read_text()),
        "mutants": mutants, "tests": repo.collected_tests(), "ratchet": ratchet,
    })
    fixer = _fixer_input(repo, commit, report)
    path = ctx.state.save("fixer-input.json", fixer)
    kind = "protected candidates" if protected else "candidates"
    count = f"{len(fixer['candidates'])} {kind}" if fixer["candidates"] else f"no {kind}"
    ctx.state.emit(f"{count}, {len(fixer['weak_spots'])} weak-spot groups → {path}")
    return 0


def _index(entries: list, key: str, expected: list, label: str, errors: list) -> dict:
    """{key value: entry}; records missing, unknown and duplicate entries in errors."""
    found: dict = {}
    for entry in entries:
        value = entry.get(key)
        if value in found:
            errors.append(f"{label} {value}: more than one entry")
        elif value not in expected:
            errors.append(f"{label} {value}: unknown, not in the input")
        found[value] = entry
    errors += [f"{label} {value}: no entry" for value in expected if value not in found]
    return found


def _blank(value) -> bool:
    return not str(value or "").strip()


def candidate_problems(tid: str, entry: dict, tests: set) -> list:
    verdict, into = entry.get("verdict"), entry.get("into")
    checks = [
        (verdict not in CANDIDATE_VERDICTS, f"verdict {verdict!r} is not one of {sorted(CANDIDATE_VERDICTS)}"),
        (verdict == "delete" and (into == tid or into not in tests), f"delete into {into} is not another collected test id"),
        (verdict == "strengthen" and _blank(entry.get("assertion")), "strengthen names no assertion"),
        (verdict != "keep" and _blank(entry.get("reason")), "no reason"),
    ]
    return [f"candidate {tid}: {message}" for failed, message in checks if failed]


def group_problems(group: dict, entry: dict) -> list:
    verdict, ids = entry.get("verdict"), entry.get("mutant_ids", group["mutant_ids"])
    listed = isinstance(ids, list) and bool(ids)
    checks = [
        (verdict not in GROUP_VERDICTS, f"verdict {verdict!r} is not one of {sorted(GROUP_VERDICTS)}"),
        (not listed, "mutant_ids must be a non-empty list"),
        (_blank(entry.get("reason")), "no reason"),
    ] + [(True, f"{m} is not one of its surviving mutants") for m in (ids if listed else []) if m not in group["mutant_ids"]]
    return [f"weak spot {group['group']}: {message}" for failed, message in checks if failed]


def split_problems(name: str, entries: list) -> list:
    """A group takes one entry, or one strengthen and one equivalent entry with disjoint, listed ids."""
    if len(entries) == 1:
        return []
    verdicts = sorted(str(e.get("verdict")) for e in entries)
    if verdicts != ["equivalent", "strengthen"]:
        return [f"weak spot {name}: at most one strengthen and one equivalent entry, got {verdicts}"]
    if not all(isinstance(e.get("mutant_ids"), list) for e in entries):
        return [f"weak spot {name}: a split group lists mutant_ids in both entries"]
    shared = set(entries[0]["mutant_ids"]) & set(entries[1]["mutant_ids"])
    return [f"weak spot {name}: {', '.join(sorted(shared))} in both entries"] if shared else []


def _group_entries(entries: list, groups: dict, errors: list) -> dict:
    """{group: [entries]}; records unknown and missing groups and bad splits in errors."""
    found: dict = {}
    for entry in entries:
        found.setdefault(entry.get("group"), []).append(entry)
    errors += [f"weak spot {name}: unknown, not in the input" for name in found if name not in groups]
    errors += [f"weak spot {name}: no entry" for name in groups if name not in found]
    for name in [n for n in found if n in groups]:
        errors += _entries_problems(groups[name], found[name])
    return found


def _entries_problems(group: dict, entries: list) -> list:
    return split_problems(group["group"], entries) + [p for e in entries for p in group_problems(group, e)]


def _validated(fixer: dict, out: dict, tests: set) -> tuple[dict, dict]:
    errors: list = []
    cands = _index(out["candidates"], "test_id", [c["test_id"] for c in fixer["candidates"]], "candidate", errors)
    for tid, entry in cands.items():
        errors += candidate_problems(tid, entry, tests)
    spots = _group_entries(out["weak_spots"], {w["group"]: w for w in fixer["weak_spots"]}, errors)
    if errors:
        raise BadInput("invalid fixer proposals:\n" + "\n".join(errors))
    return cands, spots


def _group_proposals(w: dict, entries: list) -> list:
    """One proposal per entry; id = the group, or `<group>:<verdict>` when the group is split."""
    return [dict(e, id=w["group"] if len(entries) == 1 else f"{w['group']}:{e['verdict']}", kind=e["verdict"],
                 module=w["module"], function=w["function"], mutant_ids=e.get("mutant_ids", w["mutant_ids"]))
            for e in entries]


def _candidate_proposal(c: dict, entry: dict, sources: Sources) -> dict:
    proposal = dict(entry, id=c["test_id"], kind=entry["verdict"],
                    candidate={k: c[k] for k in ("test_id", "source", "assertions")})
    if entry["verdict"] == "delete":
        proposal["into"] = sources.test(entry["into"])
    return proposal


def review_input(ctx) -> int:
    base = ctx.base()
    fixer = ctx.state.load("fixer-input.json")
    cands, spots = _validated(fixer, ctx.proposals(), set(base["tests"]))
    sources = Sources(ctx.repo, base["commit"])
    proposals = [_candidate_proposal(c, cands[c["test_id"]], sources)
                 for c in fixer["candidates"] if cands[c["test_id"]]["verdict"] != "keep"]
    proposals += [p for w in fixer["weak_spots"] for p in _group_proposals(w, spots[w["group"]])]
    path = ctx.state.save("review-input.json", {"proposals": proposals})
    ctx.state.emit(f"{len(proposals)} proposals for the reviewer → {path}")
    return 0


def _skip_reason(tid: str, into: str, kept: list, sources: Sources) -> str | None:
    for d in kept:
        if d["test_id"] == into:
            return f"its containing test {into} is deleted earlier in this batch"
    return sources.undeletable(tid)


def _whole_parametrize(kept: list, tests: list) -> tuple[list, list]:
    """A parametrized case goes only with all its siblings: the gate refuses a parametrize-list edit."""
    chosen = {d["test_id"] for d in kept}
    deletions, skipped = [], []
    for d in kept:
        stem = d["test_id"].split("[")[0]
        missing = [t for t in tests if t.split("[")[0] == stem and t not in chosen]
        if "[" in d["test_id"] and missing:
            skipped.append({"test_id": d["test_id"], "reason": "parametrized case: deleting it would edit the "
                            f"parametrize list, and its siblings {', '.join(missing)} are not all accepted"})
        else:
            deletions.append(d)
    return deletions, skipped


def _settle(accepted: list, tests: list, sources: Sources) -> tuple[list, list]:
    """Accepted deletions → (deletions, skipped): a test whose container goes, or the gate would refuse
    (alone or with the rest of the batch), or a parametrized case whose siblings stay, is skipped."""
    kept, skipped = [], []
    for proposal, review in accepted:
        tid, into = proposal["test_id"], proposal["into"]["test_id"]
        reason = _skip_reason(tid, into, kept, sources)
        if reason:
            skipped.append({"test_id": tid, "reason": reason})
        else:
            kept.append({"test_id": tid, "into": into, "reason": review["reason"]})
    deletions, partial = _whole_parametrize(kept, tests)
    refused = sources.class_refusals([d["test_id"] for d in deletions])
    return ([d for d in deletions if d["test_id"] not in refused],
            skipped + partial + [{"test_id": t, "reason": r} for t, r in refused.items()])


def cap(deletions: list) -> tuple[list, list, list]:
    """(take, left, over): every whole family that still fits within CAP ids, in batch order; the families
    that do not fit this run, left for next week; and a family of more than CAP cases, which never fits,
    as skipped entries."""
    take, left, over, count = set(), set(), [], 0
    for family in families(d["test_id"] for d in deletions):
        if len(family) > CAP:
            over += [{"test_id": t, "reason": f"{len(family)} cases exceed the cap of {CAP}"} for t in family]
        elif count + len(family) > CAP:
            left.update(family)
        else:
            take.update(family)
            count += len(family)
    return [d for d in deletions if d["test_id"] in take], [d for d in deletions if d["test_id"] in left], over


def _strengthen_item(proposal: dict, review: dict) -> dict:
    item = {k: v for k, v in proposal.items() if k not in ("into", "verdict")}
    return dict(item, kind="weak_spot" if "group" in proposal else "candidate",
                fixer_reason=proposal.get("reason"), reason=review["reason"])


def mutant_summary(ids: list, shown: int = 5) -> str:
    """`(<n> mutants: <first ids> +N more)`: batch.json keeps every id, the printout stays readable."""
    more = f" +{len(ids) - shown} more" if len(ids) > shown else ""
    return f"({len(ids)} mutant{'' if len(ids) == 1 else 's'}: {', '.join(ids[:shown])}{more})"


def suggestions_section(out: dict) -> list:
    """Accepted "strengthen with this assertion" verdicts on candidates: listed for a human, never applied."""
    items = [i for i in out["strengthen"] if i["kind"] == "candidate"]
    if not items:
        return []
    return ["", "## Strengthen suggestions", "",
            "The reviewer accepted these \"strengthen with this assertion\" verdicts. test-prune does not apply "
            "them; a human adds each assertion.", ""] + [
        f"- `{i['id']}`: add `{i['assertion']}`. {i['reason']}" for i in items]


def equivalents_section(equivalents: list, shown: int | None = None) -> list:
    """Accepted equivalent proposals, for a human to check and declare; shown: ids listed per group
    before the rest is counted (None: every id, as the PR bodies need)."""
    if not equivalents:
        return []
    return ["", "## Equivalent proposals", "",
            "The reviewer accepted these as equivalent mutants. test-prune never declares an equivalent; "
            "a human adds them to `backend/mutation_baseline.json` after checking each one.", ""] + [
        f"- `{e['group']}`: {_ids(e['mutant_ids'], shown)}. {e['reason']}" for e in equivalents]


def _ids(ids: list, shown: int | None) -> str:
    listed = ", ".join(f"`{m}`" for m in ids[:shown])
    return listed + (f" +{len(ids) - shown} more" if shown is not None and len(ids) > shown else "")


def _render_batch(out: dict, mode: str) -> str:
    lines = [f"Accepted batch ({mode})" + (": nothing is edited until a human approves and runs `apply`"
                                           if mode == "interactive" else "")]
    lines.append(f"Deletions ({len(out['deletions'])}):")
    lines += [f"  - {d['test_id']} → contained by {d['into']}: {d['reason']}" for d in out["deletions"]]
    spots = [i for i in out["strengthen"] if i["kind"] == "weak_spot"]
    lines.append(f"Strengthen, weak spots for the strengthen agent ({len(spots)}):")
    lines += [f"  - {s['id']} {mutant_summary(s['mutant_ids'])}: {s['reason']}" for s in spots]
    if out["left"]:
        lines.append(f"Left for next week ({len(out['left'])}, over the cap of {CAP} per run):")
        lines += [f"  - {d['test_id']} → contained by {d['into']}" for d in out["left"]]
    lines.append(f"Skipped ({len(out['skipped'])}):")
    lines += [f"  - {s['test_id']}: {s['reason']}" for s in out["skipped"]]
    lines.append(f"Rejected ({len(out['rejected'])}):")
    lines += [f"  - {r['id']} [{r['kind']}]: {r['reason']}" for r in out["rejected"]]
    return "\n".join(lines + suggestions_section(out) + equivalents_section(out["equivalents"], shown=5))


def check_reviews(reviews: list, ids: list) -> dict:
    errors: list = []
    found = _index(reviews, "id", ids, "proposal", errors)
    for pid, review in found.items():
        if review.get("verdict") not in ("accept", "reject"):
            errors.append(f"proposal {pid}: verdict {review.get('verdict')!r} is not accept or reject")
        if not str(review.get("reason") or "").strip():
            errors.append(f"proposal {pid}: no reason")
    if errors:
        raise BadInput("invalid reviews:\n" + "\n".join(errors))
    return found


def batch(ctx) -> int:
    base = ctx.base()
    proposals = ctx.state.load("review-input.json")["proposals"]
    reviews = check_reviews(ctx.reviews(), [p["id"] for p in proposals])
    out: dict = {"deletions": [], "left": [], "strengthen": [], "equivalents": [], "rejected": [], "skipped": []}
    accepted = []
    for p in proposals:
        review = reviews[p["id"]]
        if review["verdict"] == "reject":
            out["rejected"].append({"id": p["id"], "kind": p["kind"], "reason": review["reason"]})
        elif p["kind"] == "delete":
            accepted.append((p, review))
        elif p["kind"] == "equivalent":
            out["equivalents"].append({"group": p["group"], "mutant_ids": p["mutant_ids"], "reason": p["reason"],
                                       "review": review["reason"]})
        else:
            out["strengthen"].append(_strengthen_item(p, review))
    out["deletions"], out["skipped"] = _settle(accepted, base["tests"], Sources(ctx.repo, base["commit"]))
    if base["mode"] == "unattended":
        out["deletions"], out["left"], over = cap(out["deletions"])
        out["skipped"] += over
    path = ctx.state.save("batch.json", out)
    ctx.state.emit(f"{_render_batch(out, base['mode'])}\n→ {path}")
    return 0
