# Changelog

## [0.3.0] - 2026-11-06

### Added
- New job form in the Studio: run `/exam-forge:exam-forge`, then choose PDFs and options, start the job, answer decisions and open the book from the browser
- Sources in the form: browse this computer for files and folders, or drop them onto the form (a folder adds every PDF inside it; dropped files are copied to `~/Exam Forge/`)
- `forge.py intake` and `forge.py ask`, which connect a waiting Claude Code session to the form
- `studio_job_form` setting; set it to `false` to keep answering in the chat
- Team and cost in the form: name a target in USD or tokens and get the best team for it, or choose each role's model and effort yourself, with a live estimate
- Difficulty sliders in the form and in Settings; the three always add up to 100%
- `book_contents` setting: ask, always or never add the contents page
- `forge.py doctor` and a start screen that check Python, the PDF page tool and the browser, and install what is missing after a click. pymupdf goes into a private venv
- `forge.py models`: finds out once whether this account can use Opus, with one short request. The chief editor starts on Opus if so, on Sonnet if not
- `forge.py cost --record` makes later estimates fit this user, and `cost --budget-check` asks in the Studio when spending passes 120% of the target

### Changed
- With the form on, the skill no longer asks setup questions in the chat
- A Studio left running by another version is no longer reused
- The Studio opens in a window of its own when a Chromium browser is installed (`studio_window`), and a new `/exam-forge` reuses a window that is already open
- On a Mac the Studio is a small Exam Forge app in `~/Applications`, built on the spot with tools macOS already has, with its own pixel-art icon in the Dock. It can be kept in the Dock and starts the Studio when clicked. Printing still uses a browser
- Files can be dropped anywhere in the Studio window, including over an open dialog. They used to vanish without a message outside the form
- Brave opens the Studio window. Printing still needs Chrome, Chromium or Edge, because headless Brave did not write the PDF in testing
- A browser that hangs while printing now leaves the book as HTML instead of stopping with an error

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
