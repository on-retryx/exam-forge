---
name: config
description: Show or change Exam Forge settings, such as answer placement, merging books, cover titles, team preset or question counts. Use when the user asks to configure Exam Forge or calls /exam-forge:config.
argument-hint: "[list | get KEY | set KEY VALUE | reset [KEY]]"
---

# Exam Forge settings

Helper: `${CLAUDE_SKILL_DIR}/../exam-forge/bin/forge.py`. Call it with an absolute path.

- See everything: `python3 "$FORGE" config list`
- Read one: `python3 "$FORGE" config get KEY`
- Change one: `python3 "$FORGE" config set KEY VALUE`
- Back to default: `python3 "$FORGE" config reset KEY` (without KEY, resets all; confirm with the user first)

Turn plain requests into these commands. "Put answers under the questions" is `set answer_placement inline`; "use the cheap team" is `set quality_preset economy`. For allowed values run `config list --json` and read the `schema` section. Value formats: booleans `true`/`false`, `difficulty_mix` as `0.25,0.40,0.35`, `short_pdf_question_options` as `10,15`.

Settings are stored in `~/.config/exam-forge/settings.json` and apply to the next job. After a change, show the new value in one line. The same settings can be edited in the Studio: `/exam-forge:studio`.
