---
name: studio
description: Opens Exam Forge Studio, the local animated office view that shows the editing team at work, live progress, past jobs and all settings, in English and Turkish. Use when the user asks to open the studio, watch progress or see the office, or calls /exam-forge:studio.
---

# Exam Forge Studio

Helper: `${CLAUDE_SKILL_DIR}/../exam-forge/bin/forge.py`. Call it with an absolute path.

Start it in the background:
```bash
nohup python3 "$FORGE" studio > /tmp/exam-forge-studio.log 2>&1 &
```
Wait a moment, read the log and give the user the URL it prints. If a Studio is already running, the command reuses it and opens the page. It serves only on this computer, shuts down after 30 idle minutes, and works with no job running: it then shows a demo of the office plus past jobs and settings.
