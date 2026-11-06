#!/usr/bin/env python3
"""PreToolUse hook: record agent starts for the Studio. Must stay silent and never block."""
import datetime as dt
import json
import re
import sys
from pathlib import Path


def main():
    try:
        d = json.load(sys.stdin)
        tool_input = d.get("tool_input") or {}
        prompt = str(tool_input.get("prompt", ""))
        m = re.search(r"EXAM_FORGE: ([^\n]+)", prompt)
        if not m:
            return
        fields = dict(x.split("=", 1) for x in m.group(1).strip().split("|") if "=" in x)
        run = Path(fields.get("run", ""))
        if not (run / "plan.json").is_file():
            return
        rec = {
            "t": dt.datetime.now().isoformat(timespec="seconds"), "type": "agent_start",
            "role": str(tool_input.get("subagent_type", "")).split(":")[-1],
            "pdf": fields.get("pdf"), "chunk": int(fields.get("chunk") or 0),
            "round": int(fields.get("round") or 1),
            "model": tool_input.get("model") or "",
            "tool_use_id": d.get("tool_use_id"), "transcript": d.get("transcript_path"),
        }
        with open(run / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


main()
