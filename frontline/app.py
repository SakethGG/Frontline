"""FastAPI app: JSON API + the single-page UI."""

import asyncio
import logging
import re
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, notify, researchers
from .summarizer import summarizer
from .sweep import auto_sweep_loop, manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    asyncio.create_task(summarizer.probe())
    task = asyncio.create_task(auto_sweep_loop())
    yield
    task.cancel()


app = FastAPI(title="Frontline", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(config.STATIC_DIR / "index.html")


@app.get("/api/status")
def status():
    return {
        "claude": summarizer.enabled,
        "claude_reason": summarizer.disabled_reason,
        "model": config.MODEL,
        "channels": notify.channels(),
        "auto_sweep_hours": config.AUTO_SWEEP_HOURS,
        "sweep": manager.state,
    }


# ---------------------------------------------------------------- topics

class TopicIn(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    queries: list[str] = Field(min_length=1)
    subtopics: list[str] = []
    arxiv_categories: list[str] = []
    lookback_days: int = Field(7, ge=1, le=90)
    enabled: bool = True


def _clean_topic(t: TopicIn) -> dict:
    d = t.model_dump()
    for k in ("queries", "subtopics", "arxiv_categories"):
        d[k] = [s.strip() for s in d[k] if s.strip()]
    if not d["queries"]:
        raise HTTPException(422, "At least one search phrase is required")
    d["slug"] = re.sub(r"[^a-z0-9]+", "-", d["name"].lower()).strip("-") or "topic"
    return d


@app.get("/api/topics")
def topics(min_relevance: int = 0):
    return db.list_topics(min_relevance)


def _unique_slug(slug: str) -> str:
    existing = {x["slug"] for x in db.list_topics()}
    base, n = slug, 2
    while slug in existing:
        slug, n = f"{base}-{n}", n + 1
    return slug


@app.post("/api/topics")
def create_topic(t: TopicIn):
    d = _clean_topic(t)
    d["slug"] = _unique_slug(d["slug"])
    return db.create_topic(d)


@app.put("/api/topics/{topic_id}")
def update_topic(topic_id: int, t: TopicIn):
    existing = db.get_topic(topic_id)
    if not existing:
        raise HTTPException(404)
    if existing["kind"] == "researcher":
        raise HTTPException(409, "Edit followed researchers via /api/researchers")
    d = _clean_topic(t)
    d.pop("slug")
    return db.update_topic(topic_id, d)


@app.delete("/api/topics/{topic_id}")
def delete_topic(topic_id: int):
    db.delete_topic(topic_id)
    return {"ok": True}


# ---------------------------------------------------------------- followed researchers
# A followed researcher is a topic with kind="researcher"; it shows up in /api/topics,
# is swept like any topic, and is unfollowed with DELETE /api/topics/{id}.

@app.get("/api/researchers/search")
async def search_researchers(name: str):
    if len(name.strip()) < 3:
        raise HTTPException(422, "Type at least 3 characters")
    try:
        candidates = await researchers.search(name.strip())
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Author search failed: {e}")
    followed = {(t.get("author_ids") or {}).get("openalex"): t["id"]
                for t in db.list_topics() if t["kind"] == "researcher"}
    return [{**c, "followed_topic_id": followed.get(c["openalex_id"])} for c in candidates]


class FollowIn(BaseModel):
    openalex_id: str
    lookback_days: int = Field(config.RESEARCHER_LOOKBACK_DAYS, ge=7, le=730)


@app.post("/api/researchers")
async def follow_researcher(body: FollowIn):
    for t in db.list_topics():
        if (t.get("author_ids") or {}).get("openalex") == body.openalex_id:
            raise HTTPException(409, f"Already following {t['name']}")
    try:
        profile = await researchers.resolve(body.openalex_id)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Could not load that author: {e}")
    categories = await researchers.learn_arxiv_categories(profile)
    d = researchers.topic_fields(profile, categories)
    d["lookback_days"] = body.lookback_days
    d["slug"] = _unique_slug("researcher-" + (re.sub(r"[^a-z0-9]+", "-", profile["name"].lower()).strip("-") or "x"))
    topic = db.create_topic(d)
    # Backfill their lab's recent papers right away; if a sweep is already running,
    # the next sweep picks them up.
    swept = manager.start([topic["id"]])
    await asyncio.sleep(0)
    return {"topic": topic, "sweep_started": swept}


class ResearcherPatch(BaseModel):
    enabled: bool | None = None
    lookback_days: int | None = Field(None, ge=7, le=730)


@app.patch("/api/researchers/{topic_id}")
def update_researcher(topic_id: int, body: ResearcherPatch):
    t = db.get_topic(topic_id)
    if not t or t["kind"] != "researcher":
        raise HTTPException(404)
    return db.update_topic(topic_id, body.model_dump(exclude_none=True))


# ---------------------------------------------------------------- sweeps

class SweepIn(BaseModel):
    topic_ids: list[int] | None = None


@app.post("/api/sweep")
async def start_sweep(body: SweepIn | None = None):
    started = manager.start(body.topic_ids if body else None)
    if not started:
        raise HTTPException(409, "A sweep is already running")
    await asyncio.sleep(0)  # let the task initialise its state
    return manager.state


@app.get("/api/sweep/status")
def sweep_status():
    return manager.state


@app.get("/api/sweeps")
def sweeps():
    return db.list_sweeps()


# ---------------------------------------------------------------- papers

@app.get("/api/papers")
def papers(
    topic_id: int | None = None,
    subtopic: str | None = None,
    q: str | None = None,
    starred: bool = False,
    unread: bool = False,
    min_relevance: int = 0,
    sweep_id: int | None = None,
    sort: str = "relevance",
    limit: int = 60,
    offset: int = 0,
):
    items, total = db.query_papers(topic_id, subtopic, q, starred, unread, min_relevance,
                                   sweep_id, sort, min(limit, 200), offset)
    return {"items": items, "total": total}


@app.get("/api/counts")
def counts(topic_id: int | None = None, min_relevance: int = 0):
    return db.counts(topic_id, min_relevance)


class PaperPatch(BaseModel):
    starred: bool | None = None
    is_read: bool | None = None


@app.patch("/api/papers/{paper_id}")
def patch_paper(paper_id: int, body: PaperPatch):
    p = db.update_paper(paper_id, **body.model_dump(exclude_none=True))
    if not p:
        raise HTTPException(404)
    return p


class MarkRead(BaseModel):
    topic_id: int | None = None


@app.post("/api/papers/mark-all-read")
def mark_all_read(body: MarkRead):
    db.mark_all_read(body.topic_id)
    return {"ok": True}


def _paper_and_topic(paper_id: int) -> tuple[dict, dict]:
    p = db.get_paper(paper_id)
    if not p:
        raise HTTPException(404)
    return p, db.get_topic(p["topic_id"]) or {"name": "", "description": ""}


@app.post("/api/papers/{paper_id}/deep-dive")
async def deep_dive(paper_id: int, refresh: bool = False):
    p, topic = _paper_and_topic(paper_id)
    if p["deep_dive"] and not refresh:
        return {"markdown": p["deep_dive"]}
    md = await summarizer.deep_dive(topic, p)
    if summarizer.enabled:
        db.update_paper(paper_id, deep_dive=md)
    return {"markdown": md}


class AskIn(BaseModel):
    question: str = Field(min_length=1)
    history: list[dict] = []


@app.post("/api/papers/{paper_id}/ask")
async def ask(paper_id: int, body: AskIn):
    p, _ = _paper_and_topic(paper_id)
    return {"answer": await summarizer.ask(p, body.question, body.history)}


@app.get("/api/export.md", response_class=PlainTextResponse)
def export_markdown(sweep_id: int | None = None, starred: bool = False, topic_id: int | None = None):
    items, _ = db.query_papers(topic_id=topic_id, starred=starred, sweep_id=sweep_id, limit=200)
    sweep = db.get_sweep(sweep_id) if sweep_id else None
    out = ["# Frontline research digest", ""]
    if sweep and sweep["digest"]:
        out += [sweep["digest"], ""]
    for p in items:
        s = p["summary"]
        out += [
            f"## {p['title']}",
            f"*{', '.join(p['authors'][:6])}{' et al.' if len(p['authors']) > 6 else ''}* · "
            f"{p['published']} · relevance {p['relevance']}/10 · [paper]({p['url']})"
            + (f" · [code]({p['code_url']})" if p["code_url"] else ""),
            "",
            f"**TL;DR** {s.get('tldr', '')}",
            "",
            *[f"- {k}" for k in s.get("key_points", [])],
            "",
        ]
    return PlainTextResponse("\n".join(out), media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=frontline-digest.md"})
