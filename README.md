# Frontline

A research radar and planner in one desktop app. Press **Sweep now** and Frontline searches **arXiv, Semantic Scholar and OpenAlex** for papers newly published in your fields. Claude reads each paper and writes a medium-sized briefing card with a TL;DR, key points, subtopic tags and a 0–10 relevance score. Frontline also writes a short digest of the whole sweep and sends a notification.

Around the radar sits the rest of your research life. A **paper library** lives in a folder you choose, such as one inside Google Drive. A **conference tracker** counts down your deadlines. The **ideas** space takes ideas through to proposals and roadmaps. **Projects** hold your to-do board and future tasks.

## Sections

| Section | What it's for |
|---|---|
| **Home** | Today at a glance: next deadlines, tasks due this week, the best new papers, your reading queue and recent ideas. Quick-add a task or an idea from here. |
| **Radar** | The paper feed described below. |
| **Library** | Your PDFs, organised by your interests (see below). |
| **Conferences** | Venues you're aiming at, with live deadline countdowns (see below). |
| **Ideas** | Your ideas, proposals and roadmaps (see below). |
| **Projects** | A to-do board and a backlog of future tasks per project (see below). |

### Library

You pick a folder, ideally inside **Google Drive for desktop**, Dropbox or OneDrive, so the library syncs across devices. Frontline keeps plain PDF files there:

- Sub-folders are **collections**.
- Files you drop into the folder from anywhere show up on the next scan. Scans run at startup and when you open the Library.
- Moving or renaming a file keeps its notes, because files are matched by content.

Every PDF is identified from its arXiv ID or DOI when it has one, so you get the exact title, authors and abstract. It is then tagged against **your interests** and scored for fit. By default your interests are your Radar topics and subtopics, and you can edit them. With an AI key, tagging is one short call per new paper; without one, it uses keyword matching.

- **Filtering:** filter by interest, collection or reading status (To read / Reading / Read), star papers, and sort by relevance to you.
- **Paper drawer:** take notes, edit tags, move a paper between collections, copy its BibTeX, and run **Deep dive** or **Ask** on your local PDF.
- **Adding papers:** drag-and-drop or **+ Add PDFs**. On any Radar paper, **⤓ Save to library** downloads its PDF into the folder and carries over its AI card.
- **Removing papers:** a removed PDF moves to a hidden `.frontline-trash` folder inside the library, so nothing is lost.

### Conferences & workshops

- **Catalog:** browse the community-maintained [huggingface/ai-deadlines](https://github.com/huggingface/ai-deadlines) catalog. It covers ML, vision, NLP, robotics (ICRA, IROS, RSS, CoRL) and more, with every deadline converted from AoE/PST/UTC into your local time.
- **Focus areas:** pick yours (e.g. robotics, machine-learning) to get open deadlines suggested.
- **Tracking:** tracked venues stay up to date. When the catalog announces a series' next edition, it is added for you.
- **Manual entries:** add workshops, journals or anything missing by hand. If you edit a catalog venue's dates, later updates leave it alone.
- **Calendar export:** **📅 Export calendar** downloads an `.ics` file with every deadline (reminders a week and a day before), the conference dates and your dated tasks. Import it into Google Calendar, Outlook or Apple Calendar.

### Ideas

- **Capture:** type an idea and press Enter. Proposals and roadmaps start from a template.
- **Develop:**
  - Write Markdown notes, which save as you type.
  - Add tags and a milestone checklist.
  - Link the papers an idea builds on, from your library or the radar.
  - Move ideas through **Spark → Exploring → Developing → Active → Parked** on a drag-and-drop board.
- **Assistant (AI key needed):**
  - **Pressure-test** gives strengths, weak spots, a novelty check against the linked papers, and next steps.
  - **Brainstorm** suggests six directions.
  - **Draft proposal** writes a full proposal outline.
  - **Plan milestones** adds a realistic roadmap.
- **🚀 Start a project:** turns an idea into a project. Its open milestones become tasks: the first goes on the To-do board, the rest go to Future.

### Projects

- **To-do board:** To do / In progress / Done, with drag and drop, due dates (overdue ones are flagged) and priorities.
- **Future tab:** for things you'll do later, so the board stays focused.
- **Target venue:** a project can point at a tracked conference, and its header counts down to the next deadline.
- **Inbox:** collects tasks with no project. **All tasks** shows everything.

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

## Quick start (Windows desktop app)

```powershell
cd D:\Projects\frontline
.\install-desktop.ps1       # one time: sets up .venv and adds Desktop + Start-menu icons
```

Put your `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY`) in `.env`, then double-click **Frontline** on your Desktop. It opens in its own window: there's no browser tab or terminal, and the backend runs inside the app. When a sweep finishes you get a native Windows notification. Only one window can be open at a time. Logs go to `data/frontline.log`.

If you'd rather use a browser, `.\run.ps1` still serves it at http://localhost:8000.

## Quick start (macOS desktop app)

You'll need Python 3.10+ (`brew install python`).

```zsh
cd ~/Projects/Frontline
./install-desktop.sh        # one time: sets up .venv and creates ~/Applications/Frontline.app
```

Put your `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY`) in `.env`, then open **Frontline** from Launchpad or Spotlight (`Cmd+Space`, type "Frontline"). To keep it in the Dock, right-click its Dock icon while it's running and choose **Options → Keep in Dock**. It opens in its own window and the backend runs inside the app. When a sweep finishes you get a macOS notification. The first time, macOS may ask you to allow notifications from **Script Editor**, which is what sends them. Only one window can be open at a time. Logs go to `data/frontline.log`.

The app is a small launcher that points at this folder. If you move the project or recreate `.venv`, run `./install-desktop.sh` again. To uninstall, delete `~/Applications/Frontline.app`. Your papers and settings stay in `data/`.

The app serves itself on port 8000 when it's free, and on a random port otherwise. Links in ntfy and email notifications only open the app when it's on 8000, so don't leave a separate `uvicorn` server running on that port.

Don't run the `.ps1` scripts on a Mac. They're for Windows, and zsh will say `command not found`.

## Quick start (macOS / Linux, in the browser)

If you'd rather use a browser, or you're on Linux (the desktop app supports Windows and macOS only), run the web server directly.

One-time setup:

```zsh
cd ~/Projects/Frontline
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # then put your ANTHROPIC_API_KEY (or OPENAI_API_KEY) in .env
```

To start Frontline:

```zsh
cd ~/Projects/Frontline
.venv/bin/python -m uvicorn frontline.app:app --port 8000
```

Then open http://localhost:8000 (on a Mac, `open http://localhost:8000`). Press `Ctrl+C` in the terminal to stop the server. Native desktop notifications aren't available in this mode. Browser, ntfy and email notifications all work.

## Configuration (`.env`)

| Variable | What it does |
|---|---|
| `ANTHROPIC_API_KEY` | Enables Claude summaries, deep dives, Q&A and digests |
| `FRONTLINE_MODEL` | Claude model (default `claude-opus-5-5`) |
| `OPENAI_API_KEY` | Use OpenAI instead of Claude for the same features |
| `OPENAI_MODEL` | OpenAI model (default `gpt-5-mini`) |
| `FRONTLINE_LLM` | `claude`, `openai` or `auto` (default). `auto` uses OpenAI only when `OPENAI_API_KEY` is set and `ANTHROPIC_API_KEY` isn't |
| `FRONTLINE_SUMMARY_EFFORT` | Effort for card summaries (default `low`, which keeps sweeps fast and cheap) |
| `FRONTLINE_DEEP_DIVE_EFFORT` | Effort for deep dives (default `medium`) |
| `FRONTLINE_MAX_PAPERS_PER_TOPIC` | Cap on new papers summarized per topic per sweep (default 40) |
| `FRONTLINE_CONCURRENCY` | How many papers are summarized at once (default 4) |
| `FRONTLINE_RESEARCHER_LOOKBACK_DAYS` | How far back a followed researcher's feed goes (default 180) |
| `FRONTLINE_MAX_PAPERS_PER_RESEARCHER` | Cap on new papers summarized per researcher per sweep (default 60) |
| `FRONTLINE_AUTO_SWEEP_HOURS` | e.g. `12` runs a sweep twice a day while the server is up |
| `NTFY_TOPIC` | Push notifications. Install the ntfy app, subscribe to a hard-to-guess topic name, and put that name here |
| `NTFY_SERVER` | Your own ntfy server, if you don't use `https://ntfy.sh` |
| `SMTP_*`, `DIGEST_TO` | Email digest (for Gmail, use an app password with `smtp.gmail.com:587`) |
| `FRONTLINE_LIBRARY_DIR` | Default library folder (you can also pick it in the app, which wins). E.g. `G:\My Drive\Research Papers` on Windows, or `~/Library/CloudStorage/GoogleDrive-you@gmail.com/My Drive/Research Papers` on a Mac |
| `SEMANTIC_SCHOLAR_API_KEY`, `OPENALEX_MAILTO` | Optional. Raise API rate limits |
| `PUBLIC_URL` | Base URL used for links in notifications (default `http://localhost:8000`). The desktop app sets it for you |
| `FRONTLINE_DB` | Where the SQLite database lives (default `data/frontline.db`) |

**Cost note:** each new paper costs one short AI call (at low effort with Claude). This covers Radar papers and new PDFs in your library. If your library folder already holds hundreds of PDFs, the first scan tags each one once. Deep dives, Q&A and the idea assistant only run when you click them.

**Google Drive tip:** with Drive for desktop set to *stream* files, Frontline reads each new PDF once to index it, and Drive then downloads it. To keep a large library available offline, mark the folder *Available offline*.

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
| `frontline/notify.py` | Desktop notifications (Windows/macOS), ntfy and email |
| `frontline/library.py` | Paper library: folder scanning, PDF metadata, interest tagging, file actions |
| `frontline/conferences.py` | Conference catalog, tracked venues, time zones, calendar export |
| `frontline/planner.py` | Ideas, idea assistant, projects and tasks |
| `frontline/db.py` | SQLite storage |
| `frontline/app.py` | FastAPI routes |
| `frontline/desktop.py` | Desktop window (pywebview), Windows shortcuts and macOS `.app` installer |
| `static/` | The single-page UI (no build step): `app.js` (shared helpers + Radar), `home.js`, `library.js`, `conferences.js`, `ideas.js`, `projects.js` |

## Navigating

Topic pills → **New / All / Starred** tabs → optional subtopic chip → click any card to open the **reader panel**. The panel holds the summary, the deep dive and Q&A, and its ‹ › buttons step through papers without closing it. Opening a paper marks it read, so **New** works as an inbox you can clear.

Keyboard: `h` `r` `l` `c` `i` `p` jump to Home / Radar / Library / Conferences / Ideas / Projects · `n` new idea / task · `/` search the current section · `?` shows all shortcuts. On the Radar: `j`/`k` next/previous · `Enter` open · `Esc` close · `s` star · `u` read/unread · `d` deep dive · `1` `2` `3` switch tabs · `Shift+S` sweep.
