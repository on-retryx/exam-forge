# Changelog

## [0.2.0] - 2026-11-06

Renamed from soru-kitabi to Exam Forge and rebuilt for a global audience.

### Added
- Exam Forge Studio: a local pixel-art office drawn in code, with its own pixel font, hopping bots at desks with PCs, task speech bubbles, a team console on the chief editor's computer, live progress, past jobs and a settings panel, in English and Turkish
- Haiku screener that catches text-level defects before the page-reading review
- Chief editor audit and team presets: `quality`, `balanced`, `economy`
- Answer placement: end of book, under each question, or end of each section
- Merging several PDFs into one book, with part covers named by file or by a logical title
- `/exam-forge:config`, `/exam-forge:studio` and the `forge.py config` command
- Cost report from subagent transcripts (`forge.py cost`)
- Seeded-defect evaluation (`evals/`)
- Questions in the language of the source; book labels in English and Turkish

### Changed
- Everything in the repository is now in English, including commands and the JSON schema
- Correction rounds reopen only the pages of the rejected questions
- Settings moved to `~/.config/exam-forge/settings.json`

### Removed
- `ekonomik_mod`, replaced by the `economy` preset
- Compatibility with v0.1.0 working files

## [0.1.0] - 2026-11-06

First public release as soru-kitabi.
