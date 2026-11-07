#!/usr/bin/env python3
"""Score ratchet for feature work: progress is pushed, a step back is undone.

    ratchet.py start --feature NAME --tests test_a.py[,test_b.py] -m MESSAGE
    ratchet.py step  --feature NAME -m MESSAGE
    ratchet.py status --feature NAME
    ratchet.py gate

`start` commits tests that fail, which proves they came first, and records that score. Each `step`
commits the work and scores it out of 100: 70 for the feature's tests, 30 for all the others.

    improved    higher score: pushed
    raised_bar  more feature tests and none of the passing ones lost: pushed, new baseline
    held        same score: kept locally, goes up with the next improvement
    reverted    lower score, or fewer feature tests: patch saved, back to the best commit

`gate` is for merging: it passes only when every test in the repository passes.
Exit codes: 0 fine, 2 cannot run, 3 reverted or gate failed, 4 push failed.
The state lives in the git folder, so it is never committed and survives a reset.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

FEATURE_POINTS = 70
OTHER_POINTS = 30
PROTECTED = ("main", "master")


class Stop(Exception):
    pass


def git(*args, check=True):
    p = subprocess.run(["git", *args], capture_output=True, text=True)
    if check and p.returncode:
        raise Stop(f"git {args[0]}: {p.stderr.strip() or p.stdout.strip()}")
    return p.stdout.strip()


def root():
    return Path(git("rev-parse", "--show-toplevel"))


def branch():
    name = git("rev-parse", "--abbrev-ref", "HEAD")
    if name in PROTECTED:
        raise Stop(f"refusing to work on {name}: use a feature branch")
    return name


def state_path(feature):
    return Path(git("rev-parse", "--path-format=absolute", "--git-path", f"ratchet/{feature}.json"))


def load_state(feature):
    p = state_path(feature)
    if not p.exists():
        raise Stop(f"no baseline for {feature}: run start first")
    return json.loads(p.read_text(encoding="utf-8"))


def save_state(feature, state):
    p = state_path(feature)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")


def run_tests(files):
    """Runs test files in a fresh process, away from the real Claude CLI."""
    if not files:
        return {"passed": 0, "total": 0, "failed": []}
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "result.json"
        path = os.pathsep.join(p for p in os.environ.get("PATH", "").split(os.pathsep)
                               if not shutil.which("claude", path=p))
        env = dict(os.environ, PATH=path, EXAM_FORGE_NO_PROBE="1", PYTHONDONTWRITEBYTECODE="1")
        subprocess.run([sys.executable, __file__, "_run", str(out), *files], cwd=root(), env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not out.exists():
            return {"passed": 0, "total": max(1, len(files)), "failed": ["test run crashed"]}
        return json.loads(out.read_text(encoding="utf-8"))


def all_test_files():
    return sorted(p.name for p in (root() / "tests").glob("test*.py"))


def measure(feature_files):
    feature = run_tests(feature_files)
    others = run_tests([f for f in all_test_files() if f not in feature_files])
    share = lambda r: r["passed"] / r["total"] if r["total"] else 1.0
    score = FEATURE_POINTS * (share(feature) if feature["total"] else 0) + OTHER_POINTS * share(others)
    return {"score": round(score, 2), "feature": feature, "others": others}


def commit(message):
    git("add", "-A", "--", ".", ":!.claude")
    if git("diff", "--cached", "--name-only"):
        git("commit", "-q", "-m", message)


def push():
    p = subprocess.run(["git", "push", "-q", "-u", "origin", "HEAD"], capture_output=True, text=True)
    return p.returncode == 0, p.stderr.strip()


def record(state, decision, m, head, **extra):
    entry = {"decision": decision, "score": m["score"], "commit": head,
             "feature_passed": m["feature"]["passed"], "feature_total": m["feature"]["total"],
             "others_passed": m["others"]["passed"], "others_total": m["others"]["total"], **extra}
    state["history"].append(entry)
    return entry


def report(m, **fields):
    return {**fields, "score": m["score"], "feature": f'{m["feature"]["passed"]}/{m["feature"]["total"]}',
            "others": f'{m["others"]["passed"]}/{m["others"]["total"]}',
            "failed": (m["feature"]["failed"] + m["others"]["failed"])[:30]}


def cmd_start(a):
    branch()
    files = [f.strip() for f in a.tests.split(",") if f.strip()]
    m = measure(files)
    if not m["feature"]["total"]:
        raise Stop("no feature tests found")
    if m["feature"]["passed"] == m["feature"]["total"]:
        raise Stop("the feature tests already pass: tests must come first and fail")
    commit(a.message)
    ok, why = push()
    if not ok:
        return report(m, decision="push_failed", error=why), 4
    head = git("rev-parse", "HEAD")
    state = {"feature": a.feature, "tests": files, "best_score": m["score"], "best_commit": head,
             "best_feature_total": m["feature"]["total"], "best_feature_passed": m["feature"]["passed"], "history": []}
    record(state, "baseline", m, head)
    save_state(a.feature, state)
    return report(m, decision="baseline", commit=head), 0


def decide(state, m):
    total, passed = m["feature"]["total"], m["feature"]["passed"]
    if total < state["best_feature_total"]:
        return "reverted", "fewer feature tests than the best commit"
    if total > state["best_feature_total"] and passed >= state["best_feature_passed"]:
        return "raised_bar", "more feature tests"
    if m["score"] > state["best_score"]:
        return "improved", "higher score"
    if m["score"] == state["best_score"]:
        return "held", "same score"
    return "reverted", f'score fell from {state["best_score"]} to {m["score"]}'


def cmd_step(a):
    branch()
    state = load_state(a.feature)
    commit(a.message)
    m = measure(state["tests"])
    head = git("rev-parse", "HEAD")
    decision, reason = decide(state, m)
    if decision == "reverted":
        patch = state_path(a.feature).with_name(f'{a.feature}-rejected-{len(state["history"])}.patch')
        patch.write_text(git("diff", state["best_commit"], head) + "\n", encoding="utf-8")
        git("reset", "-q", "--hard", state["best_commit"])
        record(state, decision, m, head, reason=reason, patch=str(patch))
        save_state(a.feature, state)
        return report(m, decision=decision, reason=reason, patch=str(patch), back_to=state["best_commit"]), 3
    if decision in ("improved", "raised_bar"):
        ok, why = push()
        if not ok:
            return report(m, decision="push_failed", error=why), 4
        state.update(best_score=m["score"], best_commit=head, best_feature_total=m["feature"]["total"],
                     best_feature_passed=m["feature"]["passed"])
    record(state, decision, m, head, reason=reason)
    save_state(a.feature, state)
    return report(m, decision=decision, reason=reason, commit=head), 0


def cmd_status(a):
    state = load_state(a.feature)
    return {k: state[k] for k in ("feature", "best_score", "best_commit", "history")}, 0


def cmd_gate(a):
    r = run_tests(all_test_files())
    ok = r["total"] > 0 and r["passed"] == r["total"]
    return {"passed": ok, "tests": f'{r["passed"]}/{r["total"]}', "skipped": r.get("skipped", 0),
            "failed": r["failed"][:30], "commit": git("rev-parse", "HEAD")}, 0 if ok else 3


def inner_run(out, files):
    suite = unittest.TestSuite()
    for name in files:
        suite.addTests(unittest.defaultTestLoader.discover("tests", pattern=name))
    with open(os.devnull, "w") as sink:
        result = unittest.TextTestRunner(stream=sink, verbosity=0).run(suite)
    bad = [str(t) for t, _ in result.failures + result.errors]
    broken = {getattr(t, "test_case", t).id() for t, _ in result.failures + result.errors}  # subtests share a test
    skipped = len(result.skipped)
    ran = max(result.testsRun - skipped, len(broken))
    Path(out).write_text(json.dumps({"passed": ran - len(broken), "total": ran, "skipped": skipped, "failed": bad}),
                         encoding="utf-8")


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "_run":
        sys.path.insert(0, str(Path("tests").resolve()))
        return inner_run(sys.argv[2], sys.argv[3:])
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("start"); p.add_argument("--feature", required=True); p.add_argument("--tests", required=True); p.add_argument("-m", "--message", required=True); p.set_defaults(fn=cmd_start)
    p = sub.add_parser("step"); p.add_argument("--feature", required=True); p.add_argument("-m", "--message", required=True); p.set_defaults(fn=cmd_step)
    p = sub.add_parser("status"); p.add_argument("--feature", required=True); p.set_defaults(fn=cmd_status)
    sub.add_parser("gate").set_defaults(fn=cmd_gate)
    a = ap.parse_args()
    try:
        out, code = a.fn(a)
    except Stop as e:
        out, code = {"error": str(e)}, 2
    print(json.dumps(out, ensure_ascii=False))
    sys.exit(code)


if __name__ == "__main__":
    main()
