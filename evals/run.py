#!/usr/bin/env python3
"""Compare team presets on questions with known defects.

Each trial runs the real agents on evals/seeded-defects.json with no correction loop, and
records which questions any layer failed. This costs real tokens.

Usage: python3 evals/run.py [--presets quality,balanced,economy] [--trials 2] [--results DIR]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORGE = ROOT / "skills" / "exam-forge" / "bin" / "forge.py"
PDF = ROOT / "examples" / "water-cycle.pdf"
SEED = Path(__file__).with_name("seeded-defects.json")

PROMPT = """You are running an evaluation. Follow these steps exactly and do nothing else. Do not fix any question.
FORGE={forge}
Chunk file: {chunk}
PDF: {pdf} (4 pages; the Read tool cannot use `pages` here, so read it whole)
Format mcq, 5 options. Team: {team}

Every agent prompt starts with this line: EXAM_FORGE: run={run}|pdf=lesson|chunk=1|round=1

1. If the team has a screener: run the agent exam-forge:screener (model {screener}) on the chunk file, output {run}/lesson/screen-1-round1.json. Then run: python3 "$FORGE" screen <chunk> <that file> (a non-zero exit is expected).
2. Run the agent exam-forge:reviewer (model {reviewer}) on ALL 18 questions, whatever the screener said, output {run}/lesson/review-1-round1.json. Then run: python3 "$FORGE" approve <chunk> <that file> (a non-zero exit is expected).
3. If the team has a chief editor: run python3 "$FORGE" audit-sample <chunk> and take its ids, leaving out ids the reviewer failed. Run the agent exam-forge:chief-editor (model {chief}) on those ids, output {run}/lesson/audit-1-round1.json.
Pass FORGE to every agent. Wait for each agent to finish before the next step. Then stop and reply DONE.
"""


def results_of(path):
    if not path.is_file():
        return {}
    return {r["id"]: r for r in json.loads(path.read_text(encoding="utf-8")).get("results", [])}


def trial(preset, n, base):
    run = base / f"{preset}-{n}" / "run"
    if run.parent.exists():
        shutil.rmtree(run.parent)
    (run / "lesson").mkdir(parents=True)
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    defects = seed.pop("_defects")
    chunk = run / "lesson" / "chunk-1.json"
    chunk.write_text(json.dumps(seed, ensure_ascii=False, indent=1), encoding="utf-8")
    settings = run.parent / "settings.json"
    settings.write_text(json.dumps({"quality_preset": preset}))
    env = {**os.environ, "EXAM_FORGE_SETTINGS": str(settings), "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "0"}
    plan = subprocess.run([sys.executable, FORGE, "plan", PDF, "--run", run], env=env, capture_output=True, text=True, check=True)
    team = json.loads(plan.stdout)["team"]
    prompt = PROMPT.format(forge=FORGE, chunk=chunk, pdf=PDF, run=run, team=json.dumps(team),
                           screener=team["screener"], reviewer=team["reviewer"], chief=team["chief_editor"] or "none")
    t0 = time.time()
    out = subprocess.run(["claude", "-p", prompt, "--plugin-dir", str(ROOT), "--model", "sonnet",
                          "--permission-mode", "acceptEdits", "--output-format", "json",
                          "--allowedTools", "Bash,Read,Write,Agent,SendMessage"],
                         env=env, capture_output=True, text=True, cwd=run.parent, timeout=1500)
    try:
        cost = json.loads(out.stdout).get("total_cost_usd")
    except ValueError:
        cost = None
    layers = {name: results_of(run / "lesson" / f"{name}-1-round1.json") for name in ("screen", "review", "audit")}
    caught = {}
    for name, res in layers.items():
        for qid, r in res.items():
            if r.get("status") == "FAIL":
                caught.setdefault(qid, []).append(name)
    clean = [q["id"] for q in seed["questions"] if q["id"] not in defects]
    return {
        "preset": preset, "trial": n, "team": team, "cost_usd": cost, "seconds": round(time.time() - t0),
        "reviewed": len(layers["review"]),
        "defects": len(defects), "caught": sorted(q for q in defects if q in caught),
        "missed": {q: defects[q] for q in defects if q not in caught},
        "reviewer_caught": sum(1 for q in defects if "review" in caught.get(q, [])),
        "screener_caught": sum(1 for q in defects if "screen" in caught.get(q, [])),
        "chief_caught": sum(1 for q in defects if "audit" in caught.get(q, [])),
        "false_alarms": {q: caught[q] for q in clean if q in caught},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--presets", default="quality,balanced,economy")
    ap.add_argument("--trials", type=int, default=2)
    ap.add_argument("--results", default=str(Path(tempfile.gettempdir()) / "exam-forge-evals"),
                    help="must be outside this repository: agents cannot write inside a plugin folder")
    a = ap.parse_args()
    base = Path(a.results).expanduser()
    base.mkdir(parents=True, exist_ok=True)
    rows = []
    for preset in a.presets.split(","):
        for n in range(1, a.trials + 1):
            r = trial(preset, n, base)
            rows.append(r)
            print(json.dumps(r), flush=True)
            (base / "summary.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
