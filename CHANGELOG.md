# Changelog

## [Unreleased]

### Added
- Marked text: highlighted or underlined text is important and gets questions first, and it never means cancelled. Only a line through the text excludes it. Every agent prompt now says so, and the settings `marked_text_priority` and `marked_text_share` (default 60% of questions at most) control the weight
- `forge.py marks` reads highlights, underlines and strikethroughs from annotations, from pen strokes saved as ink, and from marks drawn into the page. For a drawn line, its height against the baseline decides between underline and strikethrough. It needs pymupdf; without it the agents still read marks from the page images
- `forge.py marks-coverage` reports how many marks have a question and the share of questions from marked text, and fails when a question rests on struck text. `forge.py check --marks` makes the same failure check
- Questions can carry `source.emphasis` and `source.mark`
- Questions from figures: editors can use a graph, chart, diagram or labelled shape from the PDF, and the book prints the crop with the question. New command `forge.py figures`, question field `figure`, `source.kind` value `figure` and setting `figure_questions`. The reviewer opens the crop, and a missing or damaged file stops the print
- `tools/ratchet.py`: a score ratchet for feature work. Tests come first, a better score is pushed, a worse one is undone, and a gate checks every test around a merge
- Handwritten notes count as a source. Legible notes can carry questions, and the answer cites them as a handwritten note; unreadable ones are reported and never guessed at. A note such as "not on the exam" stays a scope instruction. The `handwritten_notes` setting turns this off
- `forge.py notes` lists FreeText, sticky and Ink annotations of a page range, with a PNG crop of the ink. Ink saved stroke by stroke comes back as one note
- `source.kind` and `source.note` on questions, checked by `check`. `check` counts handwriting questions, and `merge` keeps the count in the book JSON as `handwriting_questions`
- A new review failure reason, `handwriting`, for a misread or uncertain note

## [0.3.1] - 2026-11-06

### Added
- Windows: the Studio's file browser lists every drive, so `D:\` no longer has to be typed
- README: how to update the plugin, and that an update needs a Claude Code restart

### Changed
- The office draws about 30 frames a second instead of 60, and nothing while another tab is showing. In a headless Chrome the page went from 42% to about 25% of a core on the office tab, and from 22% to 6% on the job form

### Fixed
- `/exam-forge:config` finds Python the way the other skills do, instead of assuming `python3`
- The hook also tries `py -3`, so a Windows machine with only `py` keeps its agent events, and it can no longer block an agent start: a missing script made Python exit with 2, which Claude Code treats as a block
- Windows: output is always UTF-8, so Turkish text and symbols no longer crash a command or arrive garbled, and the hook reads non-ASCII paths correctly
- Windows: printing no longer fails when Chrome is slow to release its temporary profile, or when a backup from the same minute exists; a book left open in a PDF viewer gives a clear message
- Windows: settings and job files retry when another program has them open for a moment
- Windows: installing Chrome with `winget` accepts its agreements, because the install runs without a prompt
- Windows and Linux: the page count uses pymupdf when it is installed, which reads PDFs with a compressed page tree
- Linux: the page to print is written next to the book, because a snap Chromium cannot read the system temp folder
- The file browser shows a message for a drive with no disk or a folder it may not open, instead of dropping the request
- The Drives shortcut appears only on a Mac
- Studios no longer pile up after an update. The newest one notes its address in `studio.json`, and an older one closes itself once no window is open and no job is in progress, even while a session waits for the next job. Studios from 0.3.0 cannot do this; they close on their old 30-minute timer, or by hand
- A Studio's pings to other Studios no longer keep those alive
- A hidden, minimized or frozen window now counts as open, because the page holds a connection (`/api/presence`) instead of relying on timers that may stop. It also means `/exam-forge` no longer opens a second window next to a sleeping one. Only three pages hold one at a time; any further page counts through its polling, which a hidden page can slow down
- A Studio no longer closes on its idle timer while a job has a question open or wrote an event in the last 15 minutes, even with no window open. One agent that stays silent for longer can still see it close, so `forge.py ask` now starts a Studio if none is running
- On a Mac the idle clock stops while the computer sleeps, so a Studio does not close the moment it wakes
- Many open Studio pages no longer freeze each other. Each held a connection, and Chrome allows six per host, so fetches failed and a seventh tab stayed blank
- `/exam-forge:studio` no longer opens another window when a page is already open
- `render` into a read-only folder gives a clear error instead of a traceback, and a failed move of the old book names the real cause. "Close it in your PDF viewer" now appears only on Windows
- The page to print has a unique hidden name, so a `<stem>.print.html` of yours stays, `--html` can use that name, and two renders never share a file
- A backup of the previous book never overwrites an older one; a second backup from the same minute gets `-2`
- A Studio keeps checking whether it should stop when `runs.json` or `studio.json` is broken or unreadable, and such a `studio.json` no longer stops every Studio start and `intake open`
- The office finishes on return what happened while its tab was hidden, instead of replaying old actions late
- Requests between the helper and the Studio skip any proxy set in `http_proxy`
- Parallel requests no longer collide on a temporary file, a failed write leaves none behind, and a disk error in a request gets an error reply. Settings are written atomically, one at a time, so a read never sees half a file
- When all 30 Studio ports are taken, the Studio stops with a message instead of starting on a port nobody can find
- A job is refused as soon as the waiting session has died, and `intake wait` throws away what such a session left behind
- A page connection above file descriptor 1024 no longer raises an error
- Adding a folder from the Studio or with `intake open` leaves out and names a file that is not a readable PDF instead of refusing the whole folder, and a locked folder reports the real cause
- Difficulty sliders sit at the saved value when it is not a multiple of 5
- A page whose Studio has closed says so, in English and Turkish
- Printing keeps Chrome away from the macOS keychain, so a job cannot bring up a keychain prompt
- A second `intake wait` or `intake open` no longer throws away a job accepted for a wait that is still running. Exactly one wait takes a job or a cancel, and what a dead wait left behind still never runs
- `EXAM_FORGE_NO_PROBE` stops the model check, in the background and when asked directly. The tests set it, so they can never start the real Claude Code CLI, which could bring up a keychain prompt under a temporary home
- When the Studio cannot start, the command says why, such as every port being taken. The ports are checked together, so a full range of silent ports fails in about 2 seconds instead of 35, and a Studio that does not answer in time is stopped, so none appears after the failure was reported
- Every POST body must be a JSON object: anything else gets a 400 before it reaches a route, also when no session is waiting
- Difficulty sliders: arrow keys always move in their own direction, also from a saved value that is not a multiple of 5, and the first move puts all three on 5% steps
- `settings.json` keeps its file mode when saved, so a private file stays private
- A read-only config folder gives a clear message for `config set`, `config reset` and in the Studio, which now answers 400 instead of 500
- `config set` from parallel commands no longer loses a key: they take turns through `settings.lock`
- A long book name no longer fails because the page to print had a longer name than the book
- A read-only book folder names the folder instead of a hidden temporary file
- A damaged `waiter.json` with an impossibly large pid no longer drops `/api/intake`
- A folder at `studio.json` no longer stops every Studio start; the mark is optional
- A `settings.json` that is not a JSON object gives the same clear error as broken JSON, in every command and in `/api/settings`, `/api/doctor` and `/api/intake`, which keeps polling
- A damaged or unreadable `runs.json` no longer stops `plan`
- Parallel uploads of one name each keep their own file instead of colliding, and a file name too long for the disk gets a clear refusal
- `/api/sources` wants a list of non-empty paths, so `"/tmp"` is not read letter by letter and `""` no longer lists the Studio's own folder
- A folder holding only fake PDFs names them

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
