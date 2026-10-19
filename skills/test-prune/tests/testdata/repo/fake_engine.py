"""Fake mutmut/pytest/gate engine behind the fixture repo's make targets.

Model: kill_map.json maps {module: {mutant id: [test ids that kill it]}}. A mutant is
killed iff one of its killers exists in the working tree (file::[Class::]func, parametrize
brackets ignored). Output formats copy the target repo's scripts (check_mutation.py,
test_candidates.py, check_prune_pr.py). Logs go to --logs, outside the repo.

Env knobs: FAKE_SNEAK_EQUIVALENT=<module>:<mutant id> makes mutation-baseline add an
equivalent id; FAKE_GATE_FAIL=1 makes prune-gate fail at point 1; FAKE_TEST_FAIL=1 makes
`make test` fail.
"""
import argparse
import ast
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

SURVIVING = {0, 5, 33}
KILLED = {1, 3}


def log(logs, name, entry):
    with open(Path(logs) / f"{name}.log", "a") as f:
        f.write(json.dumps(entry) + "\n")


def _param_ids(func):
    for dec in func.decorator_list:
        if not (isinstance(dec, ast.Call) and getattr(dec.func, "attr", "") == "parametrize"):
            continue
        for kw in dec.keywords:
            if kw.arg == "ids":
                return list(ast.literal_eval(kw.value))
        return [str(v) for v in ast.literal_eval(dec.args[1])]
    return None


def collect_source(rel_path, source):
    """Test ids pytest would collect from one file, in file order."""
    ids = []

    def add(prefix, func):
        if not func.name.startswith("test"):
            return
        params = _param_ids(func)
        base = f"{prefix}::{func.name}"
        ids.extend([f"{base}[{p}]" for p in params] if params else [base])

    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add(rel_path, node)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for inner in node.body:
                if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    add(f"{rel_path}::{node.name}", inner)
    return ids


def collect_tree(backend):
    ids = []
    for path in sorted((backend / "tests").rglob("test_*.py")):
        ids += collect_source(path.relative_to(backend).as_posix(), path.read_text())
    return ids


def collect_ref(root, ref):
    files = subprocess.run(["git", "ls-tree", "-r", "--name-only", ref, "backend/tests"], cwd=root,
                           check=True, capture_output=True, text=True).stdout.split()
    ids = []
    for name in sorted(files):
        if Path(name).name.startswith("test_") and name.endswith(".py"):
            source = subprocess.run(["git", "show", f"{ref}:{name}"], cwd=root, check=True,
                                    capture_output=True, text=True).stdout
            ids += collect_source(name[len("backend/"):], source)
    return ids


def fn_hash(module, function):
    return hashlib.md5(f"{module}:{function}".encode()).hexdigest()[:12]


def dotted(module):
    return module[:-3].replace("/", ".")


def mutation(root, logs):
    backend = root / "backend"
    mutants = backend / "mutants"
    state = "absent" if not mutants.exists() else ("empty" if not any(mutants.iterdir()) else "populated")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()
    log(logs, "mutation", {"mutants": state, "head": head, "KEEP_CACHE": os.environ.get("KEEP_CACHE")})
    present = {i.split("[")[0] for i in collect_tree(backend)}
    kill_map = json.loads((root / "kill_map.json").read_text())
    for module, by_mutant in kill_map.items():
        codes, hashes = {}, {}
        for mutant_id, killers in by_mutant.items():
            killed = any(k.split("[")[0] in present for k in killers)
            codes[f"{dotted(module)}.{mutant_id}"] = 1 if killed else 0
            function = mutant_id.partition("__mutmut_")[0]
            hashes[function] = fn_hash(module, function)
        meta = mutants / f"{module}.meta"
        meta.parent.mkdir(parents=True, exist_ok=True)
        meta.write_text(json.dumps({"exit_code_by_key": codes, "hash_by_function_name": hashes}, indent=2))
    print("done")
    return 0


def read_results(backend, module, declared):
    meta_path = backend / "mutants" / f"{module}.meta"
    if not meta_path.exists():
        return None
    meta = json.loads(meta_path.read_text())
    hashes = meta["hash_by_function_name"]
    codes = {k.rpartition(".")[2]: c for k, c in meta["exit_code_by_key"].items()}
    equivalent = {}
    for mutant_id, digest in declared.items():
        if mutant_id not in codes:
            print(f"  ! {module}: equivalent {mutant_id} no longer exists, dropped")
        elif hashes.get(mutant_id.partition("__mutmut_")[0]) != digest:
            print(f"  ! {module}: equivalent {mutant_id} is stale (function changed), counts as a survivor")
        else:
            equivalent[mutant_id] = digest
    killed = sum(1 for c in codes.values() if c in KILLED)
    survivors = sorted(m for m, c in codes.items() if c in SURVIVING and m not in equivalent)
    tested = len(codes) - len(equivalent)
    score = round(100 * killed / tested, 2) if tested else 0.0
    return {"killed": killed, "total": len(codes), "survivors": survivors, "equivalent": equivalent, "score": score}


def modules(root):
    return list(json.loads((root / "kill_map.json").read_text()))


def mutation_check(root):
    backend = root / "backend"
    baseline = json.loads((backend / "mutation_baseline.json").read_text())
    ok = True
    for module in modules(root):
        entry = baseline.get(module)
        result = read_results(backend, module, (entry or {}).get("equivalent", {}))
        if result is None:
            print(f"  ✗ {module}: no mutmut results, run 'make mutation'")
            ok = False
            continue
        count = len(result["survivors"])
        if entry is None:
            print(f"  • {module}: no baseline, run the update command ({count} survivors, score {result['score']:.2f})")
            continue
        allowed = entry["survivors"]
        if count > allowed:
            print(f"  ✗ {module}: {count} survivors > baseline {allowed} (score {result['score']:.2f})")
            for mutant_id in result["survivors"]:
                print(f"      {mutant_id}")
            ok = False
            continue
        print(f"  ✓ {module}: {count} survivors, baseline {allowed}, score {result['score']:.2f}")
        if count < allowed:
            print("    ↑ fewer survivors than the baseline, run 'make mutation-baseline' to lock it in")
    print("Mutation ratchet PASSED." if ok else "Mutation ratchet FAILED.")
    return 0 if ok else 1


def mutation_baseline(root, logs):
    backend = root / "backend"
    path = backend / "mutation_baseline.json"
    baseline = json.loads(path.read_text())
    sneak = os.environ.get("FAKE_SNEAK_EQUIVALENT")
    log(logs, "mutation-baseline", {"sneak": sneak})
    new = {}
    for module in modules(root):
        entry = baseline.get(module)
        declared = dict((entry or {}).get("equivalent", {}))
        if sneak and sneak.split(":")[0] == module:
            mutant_id = sneak.split(":")[1]
            declared[mutant_id] = fn_hash(module, mutant_id.partition("__mutmut_")[0])
        result = read_results(backend, module, declared)
        if result is None:
            print(f"  ! {module}: no mutmut results, baseline kept")
            if entry is not None:
                new[module] = entry
            continue
        new[module] = {"equivalent": dict(sorted(result["equivalent"].items())), "killed": result["killed"],
                       "score": result["score"], "survivors": len(result["survivors"]), "total": result["total"]}
        print(f"  {module}: {entry['survivors'] if entry else 'new'} → {len(result['survivors'])} survivors")
    path.write_text(json.dumps(new, indent=2, sort_keys=True) + "\n")
    return 0


def _function_name(mutant_id):
    mangled = mutant_id.partition("__mutmut_")[0]
    return mangled[2:] if mangled.startswith("x_") else ".".join(mangled.split("ǁ")[1:])


def test_candidates(root, logs, args):
    backend = root / "backend"
    log(logs, "test-candidates", {"args": args})
    out = backend / ".mutation"
    out.mkdir(exist_ok=True)
    ids = collect_tree(backend)
    out.joinpath("tests.txt").write_text("\n".join(ids) + f"\n\n{len(ids)} tests collected in 0.01s\n")
    provided = Path(logs) / "candidates.json"
    candidates = json.loads(provided.read_text()) if provided.exists() else []
    if "--protected-only" in args:
        candidates = [c for c in candidates if not c.get("unprotected")]
    candidates = [{k: v for k, v in c.items() if k != "unprotected"} for c in candidates]
    baseline = json.loads((backend / "mutation_baseline.json").read_text())
    groups = {}
    for module in modules(root):
        result = read_results(backend, module, baseline.get(module, {}).get("equivalent", {}))
        for mutant_id in result["survivors"] if result else []:
            groups.setdefault((module, _function_name(mutant_id)), []).append(mutant_id)
    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    report = {"candidates": candidates, "no_measured_coverage": [],
              "weak_spots": [{"module": m, "function": f, "mutant_ids": i} for (m, f), i in ordered]}
    out.joinpath("test-candidates.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"{len(candidates)} candidates, 0 without measured coverage, {len(ordered)} weak-spot functions"
          f" → {out / 'test-candidates.json'}")
    return 0


def prune_gate(root, logs, base, head):
    log(logs, "prune-gate", {"base": base, "head": head})
    if os.environ.get("FAKE_GATE_FAIL"):
        print("Prune gate FAILED at point 1:")
        print("  ✗ backend/tests/test_rebalancer.py: adds line 3: 'assert True'")
        return 1
    numstat = subprocess.run(["git", "diff", "--numstat", base, head], cwd=root, check=True,
                             capture_output=True, text=True).stdout
    problems = []
    for line in numstat.splitlines():
        added, _, path = line.split("\t")
        if not path.startswith("backend/tests/") or Path(path).name == "conftest.py":
            problems.append(f"{path}: outside the deletable test files")
        elif added != "0":
            problems.append(f"{path}: adds {added} lines")
    base_ids, head_ids = set(collect_ref(root, base)), set(collect_ref(root, head))
    problems += [f"{t}: added test" for t in sorted(head_ids - base_ids)]
    if problems:
        print("Prune gate FAILED at point 1:")
        for p in problems:
            print(f"  ✗ {p}")
        return 1
    deleted = sorted(base_ids - head_ids)
    print(f"Prune gate PASSED: {len(deleted)} deleted tests, all protected by the ratchet")
    for t in deleted:
        print(f"  ✓ {t}")
    return 0


def run_tests(root, logs):
    log(logs, "test", {})
    if os.environ.get("FAKE_TEST_FAIL"):
        print("1 failed")
        return 1
    for path in (root / "backend" / "tests").rglob("*.py"):
        ast.parse(path.read_text())
    print("passed")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--logs", required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("target")
    parser.add_argument("rest", nargs=argparse.REMAINDER)
    a = parser.parse_args()
    root = a.root
    if a.target == "mutation":
        return mutation(root, a.logs)
    if a.target == "mutation-check":
        return mutation_check(root)
    if a.target == "mutation-baseline":
        return mutation_baseline(root, a.logs)
    if a.target == "test-candidates":
        return test_candidates(root, a.logs, a.rest)
    if a.target == "prune-gate":
        return prune_gate(root, a.logs, *a.rest)
    if a.target == "test":
        return run_tests(root, a.logs)
    log(a.logs, a.target, {})
    return 0


if __name__ == "__main__":
    sys.exit(main())
