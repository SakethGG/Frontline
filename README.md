# Frontline

A one-button research radar. Press **Sweep now** and Frontline searches **arXiv, Semantic Scholar and OpenAlex** for papers newly published in your fields. Claude reads each paper and writes a medium-sized briefing card with a TL;DR, key points, subtopic tags and a 0–10 relevance score. Frontline also writes a short digest of the whole sweep and sends a notification.


## Features

- **One-click sweep** across three free scholarly APIs. Results from different sources are merged by arXiv ID, DOI or title. Papers you've already seen are skipped, so each sweep shows only new work.
- **Researchers you follow.** Click **+ Researcher**, type a professor's name, and pick the right person from the candidates (institution, h-index and research areas tell namesakes apart). Their feed shows every paper they co-author, which in practice is their lab's output. It draws on OpenAlex (by author ID, plus same-name papers at their institution, since author databases split one person into several profiles) and on arXiv (by name, within the categories they publish in) for the freshest preprints. Following someone backfills the last 6 months immediately, and later sweeps pick up new papers. Claude scores each paper against the lab's research areas, so a paper misattributed to a namesake scores low and gets hidden.
- **Topics with subtopics.** Ships with *Legged Robots* (co-design, control, RL, sim-to-real, perception, hardware, loco-manipulation). You can add any field from the UI. Each topic has search phrases, optional arXiv categories and a look-back window.
- **AI briefing cards**: TL;DR, 3–5 key points, problem / method / results / why it matters / limitations, paper type, and a relevance score with a one-line reason. Use the min-relevance slider to hide noise.
- **Sweep digest**: headline must-reads, themes and "worth watching" across everything found.
- **Deep dive**: Claude reads the **full PDF** and writes a dense breakdown covering method, setup, the actual numbers, limitations, ideas worth stealing and a read/skip verdict.
- **Ask the paper**: a chat about any paper, grounded in its PDF. The PDF is prompt-cached, so follow-up questions cost less.
- **Notifications**:
  - Browser notification when a sweep finishes.
  - Optional phone/desktop push via [ntfy.sh](https://ntfy.sh) (no account needed).
  - Optional email digest.
- **Scheduled sweeps** (`FRONTLINE_AUTO_SWEEP_HOURS`), so new papers arrive without pressing the button.
- **Reading workflow**: star papers, track unread, filter by topic, subtopic or sweep, full-text search, one-click BibTeX, code-link detection, and Markdown export of any view.
- Works **without an API key** too. Cards then fall back to abstract extracts and keyword-based scores.

## Quick start (Windows)

```powershell
cd D:\Projects\frontline
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env      # then put your ANTHROPIC_API_KEY in .env
.\run.ps1                   # opens http://localhost:8000
```

macOS/Linux: `source .venv/bin/activate && uvicorn frontline.app:app --port 8000`.

## Configuration (`.env`)

| Variable | What it does |
|---|---|
| `ANTHROPIC_API_KEY` | Enables Claude summaries, deep dives, Q&A and digests |
| `FRONTLINE_MODEL` | Default `claude-opus-5-5` |
| `FRONTLINE_SUMMARY_EFFORT` | Effort for card summaries (default `low`, which keeps sweeps fast and cheap) |
| `FRONTLINE_DEEP_DIVE_EFFORT` | Effort for deep dives (default `medium`) |
| `FRONTLINE_MAX_PAPERS_PER_TOPIC` | Cap on new papers summarized per topic per sweep (default 40) |
| `FRONTLINE_RESEARCHER_LOOKBACK_DAYS` | How far back a followed researcher's feed goes (default 180) |
| `FRONTLINE_MAX_PAPERS_PER_RESEARCHER` | Cap on new papers summarized per researcher per sweep (default 60) |
| `FRONTLINE_AUTO_SWEEP_HOURS` | e.g. `12` runs a sweep twice a day while the server is up |
| `NTFY_TOPIC` | Push notifications. Install the ntfy app, subscribe to a hard-to-guess topic name, and put that name here |
| `SMTP_*`, `DIGEST_TO` | Email digest (for Gmail, use an app password with `smtp.gmail.com:587`) |
| `SEMANTIC_SCHOLAR_API_KEY`, `OPENALEX_MAILTO` | Optional. Raise API rate limits |

**Cost note:** each new paper costs one short Claude call at low effort. Deep dives and Q&A send the full PDF, and they only run when you click them.

## How it works

```
Sweep ─► for each topic:
           arXiv + Semantic Scholar + OpenAlex (in parallel, last N days)
           ─► merge duplicates ─► drop off-topic keyword misses ─► skip already-seen
           ─► Claude card per paper (structured JSON, concurrent)
           ─► SQLite
       ─► Claude digest across the sweep ─► ntfy / email / browser notification
```

| File | Role |
|---|---|
| `frontline/sources.py` | Paper fetchers (topic keyword search and per-author) and dedupe |
| `frontline/researchers.py` | Researcher name search, profiles, arXiv category learning |
| `frontline/summarizer.py` | Claude prompts (cards, deep dive, Q&A, digest) plus the no-key fallback |
| `frontline/sweep.py` | Orchestration, progress state, scheduler |
| `frontline/notify.py` | ntfy and email |
| `frontline/db.py` | SQLite storage |
| `frontline/app.py` | FastAPI routes |
| `static/` | The single-page UI (no build step) |

## Navigating

Topic pills → **New / All / Starred** tabs → optional subtopic chip → click any card to open the **reader panel**. The panel holds the summary, the deep dive and Q&A, and its ‹ › buttons step through papers without closing it. Opening a paper marks it read, so **New** works as an inbox you can clear.

Keyboard: `j`/`k` next/previous · `Enter` open · `Esc` close · `s` star · `u` read/unread · `d` deep dive · `1` `2` `3` switch tabs · `/` search · `Shift+S` sweep · `?` shows all shortcuts.
