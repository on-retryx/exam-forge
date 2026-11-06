---
name: studio
description: Opens Exam Forge Studio, the local animated office view that shows the editing team at work, live progress, past jobs and all settings, in English and Turkish. Use when the user asks to open the studio, watch progress or see the office, or calls /exam-forge:studio.
---

# Exam Forge Studio

Helper: `${CLAUDE_SKILL_DIR}/../exam-forge/bin/forge.py`. Call it with an absolute path. `PY` is the first of `python3`, `python`, `py -3` that prints a Python 3 version. If none does, ask before installing Python as `/exam-forge:exam-forge` does.

Start it in the background:
```bash
nohup $PY "$FORGE" studio > /tmp/exam-forge-studio.log 2>&1 &
```
Wait a moment, read the log and give the user the URL it prints. If a Studio is already running, the command reuses it and opens the page. It serves only on this computer, shuts down after 30 idle minutes, and works with no job running: it then shows a demo of the office plus past jobs and settings. The **New job** tab needs a waiting session; to start a job from it, use `/exam-forge:exam-forge`.
