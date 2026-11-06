# Exam Forge

Turn lecture notes into a printable question book, where **every question comes from your source** and **passes an independent review** before it gets in. Exam Forge is a [Claude Code](https://claude.com/claude-code) plugin, and you can watch its editorial team work in a small pixel-art office.

![Exam Forge Studio](docs/studio.png)

## Why

AI can write exam questions quickly. The hard part is trusting them. A model can add a fact that was never in your notes, write two correct options, or give the wrong answer key. Exam Forge is built around two rules:

1. **Every question is tied to its source.** Each one carries the page number and a verbatim quote that supports the answer.
2. **Unreviewed questions cannot enter the book.** A separate reviewer opens the PDF pages itself and checks each question. Whatever fails is rewritten. The tool enforces this, so it cannot be skipped.

## What you get

- **Formats:** multiple choice (4 or 5 options) · open-ended · mixed (open + fill-in-the-blank + true/false) · full mix
- **Difficulty:** easy 25%, medium 40%, hard 35% (adjustable)
- **Answers where you want them:** at the end of the book, under each question, or at the end of each section
- **Several PDFs:** one book per PDF, or a single merged book with a cover for each part, named after the file or with a logical title
- **Any language:** questions follow the language of your PDF. Book labels come in English and Turkish.
- **A live Studio:** a local animated office with the team at their desks, progress, reasons for rejections and estimated cost

## Install

You need Claude Code, Python 3.9+, and Chrome, Chromium or Edge to print the PDF. Without a browser, the book comes out as HTML that you can print from any browser.

In Claude Code:

```
/plugin install exam-forge --marketplace on-retryx/exam-forge
```

Or in two steps:

```
/plugin marketplace add on-retryx/exam-forge
/plugin install exam-forge@retryx
```

## Use

Drop PDFs into the chat or give their paths, then ask in plain words:

> Make a question book from these two PDFs. The teacher stressed chapter 2.

Or call it directly:

```
/exam-forge:exam-forge ~/Courses/week3.pdf ~/Courses/week4.pdf
```

Exam Forge asks for the format, where answers should go and, for several PDFs, whether to merge them. The book lands next to your PDF as `<name>-questions.pdf`.

Try it with [`examples/water-cycle.pdf`](examples/water-cycle.pdf). [`examples/water-cycle-questions.pdf`](examples/water-cycle-questions.pdf) is a real output: one merged book from that note and its Turkish twin, 20 questions, with 7 of them rewritten after review.

## How it works

```
PDFs ─► Plan ─► Editors (parallel, one per chunk)
                   │
                   ▼
           Mechanical check (forge.py)
                   │
                   ▼
           Screener (Haiku, no PDF) ──── fail ──► Rewrite ─┐
                   │ pass                                  │
                   ▼                                       │
           Reviewer (opens the pages) ── fail ──► Rewrite ─┤
                   │ pass                                  │
                   ▼                                       │
           Chief editor (balanced preset) ─ fail ► Rewrite ─┘
                   │ pass
                   ▼
           Merge ─► PDF book
```

| Role | Default model | Job |
|---|---|---|
| Orchestrator | your session | Splits the work, runs the team, manages the queue |
| Editor | Sonnet | Reads only its pages and writes the questions |
| Screener | Haiku | Catches defects visible in the text, before any costly review |
| Reviewer | Opus | Opens the pages and proves each question against the source. Only this step can verify a question. |
| Chief editor | Opus, `balanced` only | Re-checks hard and rewritten questions plus a sample of the rest |
| `forge.py` | no model | Plan, mechanical checks, approvals, merging, printing. Python standard library only. |

## Cost and quality

The main cost is reopening PDF pages, not writing. Two savings are always on and do not touch quality: the Haiku screener catches text-level defects before the expensive review, and correction rounds reopen only the pages of the rejected questions.

Beyond that, you choose a team preset:

| Preset | Reviewer | Chief editor | Use it when |
|---|---|---|---|
| `quality` (default) | Opus | none | The book matters most |
| `balanced` | Sonnet | Opus on a sample | You want lower cost with a final check |
| `economy` | Sonnet | none | Your plan limit is tight |

Haiku never writes questions and never verifies them. If it misses something, the reviewer still sees every question.

**What we measured.** `evals/run.py` gives each team the same 18 questions on a 4-page note, with 10 planted defects (outside knowledge, two correct answers, wrong keys, a fake quote, a wrong difficulty, a giveaway option, references to the source). Two trials per preset:

| Preset | Defects caught | Screener alone | Cost per trial |
|---|---|---|---|
| `quality` | 10 of 10 | 7 to 8 | about $0.41 |
| `balanced` | 10 of 10 | 7 to 9 | about $0.43 |
| `economy` | 10 of 10 | 6 to 7 | about $0.28 |

All three also failed 3 control questions that we had labeled too hard, so those were our mistakes, not false alarms. The screener wrongly flagged one clean question once. This is a small test on one short note, so treat it as an observation, not proof. On it, the Sonnet reviewer matched Opus, and `balanced` saved nothing. `quality` stays the default until a larger test says otherwise.

**A full job costs more than a review.** A merged book from two 4-page notes (20 questions, 7 rewritten) came to $9.93 at API prices. The team was $4.14 (reviewer $2.71, editors $1.39, screener $0.04); the rest was the Opus orchestrator, inflated by long API delays during that run. Short notes run out of distinct facts, which also adds rewrite rounds.

Long PDFs can use a real share of a Claude Pro session. `/exam-forge:exam-forge` reports the team's cost at the end. Run the comparison yourself with `python3 evals/run.py`, which spends real tokens.

## Studio

The Studio opens in your browser when a job starts. It is a pixel-art office drawn entirely in code, including its own font, with no image files, downloads or external services, and it runs only on your computer.

- The team grows with the job: hopping pixel bots in different shapes for each editor and reviewer, a screener, and a crowned chief editor in a glass office, all at desks with their own PCs
- Bots carry pages between desks, PASS and FAIL stamps appear, and speech bubbles show what each bot is doing (`studio_speech_bubbles` turns them off)
- Click the chief editor's computer, or any bot, to open the team console: every agent, its current task, model and recent activity. Move through agents with the arrow keys, or show one in the office.
- Live progress, reasons for rejection, estimated cost, past jobs and every setting
- English and Turkish, day and night themes. With no job running, it plays a demo.

It never shows question text. Open it any time with `/exam-forge:studio`. To stop it opening by itself: `studio_auto_open` = `false`.

## Settings

Change settings in the Studio, in plain words with `/exam-forge:config`, or from the helper:

```
/exam-forge:config set answer_placement inline
/exam-forge:config set quality_preset balanced
```

They are stored in `~/.config/exam-forge/settings.json`.

| Key | Default | Meaning |
|---|---|---|
| `answer_placement` | `book_end` | `book_end`, `inline` or `section_end` |
| `merge_books` | `ask` | `ask`, `always` or `never` |
| `section_titles` | `logical` | Part covers: `logical` title or `filename` |
| `book_language` | `auto` | Book labels: `auto`, `en` or `tr` |
| `question_language` | `auto` | `auto` follows the PDF; or a language name |
| `output_dir` | empty | Empty means next to the first PDF |
| `field` | empty | Exam style, such as medicine or law |
| `mcq_options` | 5 | Options per question: 4 or 5 |
| `difficulty_mix` | 0.25 / 0.40 / 0.35 | Easy, medium, hard; sums to 1 |
| `short_pdf_pages` | 20 | Below this, use fixed question counts |
| `short_pdf_question_options` | 10, 15 | Counts the editor can pick on short PDFs |
| `questions_per_page` | 1.0 | For longer PDFs |
| `thin_page_floor` | 0.6 | Lowest share of the target on thin pages |
| `quality_preset` | `quality` | `quality`, `balanced` or `economy` |
| `screener_enabled` | true | Haiku first pass |
| `chief_sample_rate` | 0.25 | Share of easy and medium questions the chief editor checks |
| `models.*` | empty | Override a role: `editor`, `screener`, `reviewer`, `chief_editor` |
| `topic_change_round` / `max_rounds` | 4 / 6 | When a stuck question changes topic, and when you are asked |
| `split_threshold` / `chunk_pages` | 25 / 20 | PDFs longer than this split into chunks of this size |
| `studio_auto_open` / `studio_port` | true / 8765 | Studio behavior |
| `studio_speech_bubbles` | true | Bots show their current task in the Studio |
| `browser` | empty | Browser path for printing (or `EXAM_FORGE_BROWSER`) |
| `pricing` | see file | USD per million tokens, used only for estimates |

## Privacy and copyright

- Exam Forge reads only the files you give it. It does not search the web or use other sources.
- Your questions stay yours. The run folder sits next to your book, and nothing is copied elsewhere. Your PDFs are processed by Claude as usual in Claude Code.
- Use only material you have the right to use. Questions include short quotes from the source.
- The review catches most errors, but it is still AI. Use the book to study. Have a person check it before using it as an official exam.

## Development

```
python3 -m unittest discover -s tests
claude plugin validate .
claude --plugin-dir .
```

## License

[MIT](LICENSE) · RetryX

---

## Türkçe özet

**Exam Forge**, ders notu PDF'lerinden yazdırılabilir soru kitabı üreten bir Claude Code eklentisidir. Her soru kaynak sayfa ve birebir alıntı taşır. Ayrı bir reviewer sayfaları kendisi açıp denetler, doğrulanmamış soru kitaba giremez. Sorular PDF'in dilinde yazılır; Türkçe kaynak Türkçe soru verir. Kitap etiketleri ve Studio arayüzü Türkçe de kullanılabilir. Cevaplar kitabın sonuna, sorunun altına ya da bölüm sonuna konabilir. Birden çok PDF tek kitapta birleştirilebilir. Tüm ayarlar Studio'dan ya da `/exam-forge:config` ile değişir. Kurulum: `/plugin install exam-forge --marketplace on-retryx/exam-forge`.
