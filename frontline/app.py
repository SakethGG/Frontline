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

from . import backup, conferences, config, db, library, notify, planner, researchers, sources
from .summarizer import API_ERRORS, explain, summarizer
from .sweep import auto_sweep_loop, manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("frontline.app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    planner.purge_bin()  # completed items older than 30 days are deleted for good
    asyncio.create_task(_refresh_researcher_areas())
    asyncio.create_task(summarizer.probe())
    asyncio.create_task(conferences.refresh_if_stale())
    if (base := library.root()) and base.is_dir():
        library.manager.start()  # pick up PDFs added to the folder while the app was closed
    task = asyncio.create_task(auto_sweep_loop())
    yield
    task.cancel()


app = FastAPI(title="Frontline", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
app.include_router(library.router)
app.include_router(conferences.router)
app.include_router(planner.router)
app.include_router(backup.router)


@app.get("/api/calendar.ics")
def calendar():
    """Tracked deadlines and dated tasks, for importing into Google Calendar / Outlook / Apple Calendar."""
    return conferences.ics_response()


@app.middleware("http")
async def revalidate_ui(request, call_next):
    """Make the window re-check UI files on every load so edits show up without clearing its cache."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/")
def index():
    return FileResponse(config.STATIC_DIR / "index.html")


@app.get("/api/status")
def status():
    return {
        "ai": summarizer.enabled,
        "ai_reason": summarizer.disabled_reason,
        "provider": summarizer.name,
        "model": summarizer.model,
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

async def _refresh_researcher_areas() -> None:
    """One-time upgrade: followed researchers whose labels came from OpenAlex's coarse topic
    list get labels from their own papers' keywords (shown on their profile; their subtopic
    chips are left alone so existing paper tags keep working)."""
    stale = [t for t in db.list_topics() if t["kind"] == "researcher"
             and (t.get("profile") or {}).get("areas_from") in (None, "topics")
             and not (t.get("profile") or {}).get("scholar_id")]  # Scholar-linked: their own listed interests
    if not stale:
        return
    try:
        fresh = await researchers.refresh_areas([t["profile"] for t in stale])
    except httpx.HTTPError as e:
        log.info("Could not refresh researcher labels: %s", e)
        return
    for t in stale:
        areas = fresh.get(t["profile"].get("openalex_id"))
        if areas:
            db.update_topic(t["id"], {"profile": {**t["profile"], "areas": areas, "areas_from": "keywords"}})


@app.get("/api/researchers/search")
async def search_researchers(name: str, org: str = ""):
    if len(name.strip()) < 3:
        raise HTTPException(422, "Type at least 3 characters")
    try:
        if "scholar.google." in name:  # a pasted Google Scholar profile link
            candidates = await researchers.search_scholar(name.strip())
        else:
            candidates = await researchers.search(name.strip(), org.strip())
    except ValueError as e:
        raise HTTPException(422, str(e))
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Author search failed: {e}")
    return [{**c, "followed_topic_id": _followed_topic_id(c.get("scholar_id", ""), c["openalex_id"])}
            for c in candidates]


def _followed_topic_id(scholar_id: str, openalex_id: str) -> int | None:
    for t in db.list_topics():
        ids = t.get("author_ids") or {}
        if t["kind"] == "researcher" and (
                (scholar_id and ids.get("scholar") == scholar_id) or (openalex_id and ids.get("openalex") == openalex_id)):
            return t["id"]
    return None


class FollowIn(BaseModel):
    openalex_id: str = ""
    scholar_id: str = Field("", pattern=r"^([\w-]{12})?$")
    lookback_days: int = Field(config.RESEARCHER_LOOKBACK_DAYS, ge=7, le=730)


@app.post("/api/researchers")
async def follow_researcher(body: FollowIn):
    if not (body.scholar_id or body.openalex_id):
        raise HTTPException(422, "Give a Google Scholar or OpenAlex author ID")
    try:
        if body.scholar_id:  # their Scholar profile has the final say
            profile, scholar_titles = await researchers.scholar_profile(body.scholar_id)
        else:
            profile, scholar_titles = await researchers.resolve(body.openalex_id), None
    except (ValueError, sources.ScholarUnavailable) as e:
        raise HTTPException(422, str(e))
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Could not load that author: {e}")
    if (tid := _followed_topic_id(profile.get("scholar_id", ""), profile["openalex_id"])) is not None:
        raise HTTPException(409, f"Already following {db.get_topic(tid)['name']}")
    categories = await researchers.learn_arxiv_categories(profile, scholar_titles)
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


class ScholarLinkIn(BaseModel):
    url: str


@app.post("/api/researchers/{topic_id}/scholar")
async def link_scholar(topic_id: int, body: ScholarLinkIn):
    """Attach a Google Scholar profile to someone already followed: their photo shows up, and
    from the next sweep their Scholar page has the final say on which papers are theirs."""
    t = db.get_topic(topic_id)
    if not t or t["kind"] != "researcher":
        raise HTTPException(404)
    sid = researchers.scholar_id(body.url)
    if not sid:
        raise HTTPException(422, "That isn't a Google Scholar profile link. It should look like "
                                 "https://scholar.google.com/citations?user=XXXXXXXXXXXX")
    other = _followed_topic_id(sid, "")
    if other is not None and other != topic_id:
        raise HTTPException(409, f"That Scholar profile is already linked to {db.get_topic(other)['name']}")
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as c:
            gs = await sources.scholar_page(c, sid)
    except sources.ScholarUnavailable as e:
        raise HTTPException(502, str(e))
    profile = {**(t.get("profile") or {}), "scholar_id": sid, "photo_url": gs["photo"],
               "cited_by_count": gs["citations"] or (t.get("profile") or {}).get("cited_by_count", 0),
               "h_index": gs["h_index"] or (t.get("profile") or {}).get("h_index", 0)}
    if gs["affiliation"]:
        profile["institution"] = gs["affiliation"]
    if gs["interests"]:
        profile.update(areas=gs["interests"], areas_from="scholar")
    return db.update_topic(topic_id, {"author_ids": {**(t.get("author_ids") or {}), "scholar": sid},
                                      "profile": profile})


PHOTO_DIR = config.DB_PATH.parent / "photos"


@app.get("/api/scholar/{sid}/photo")
async def scholar_photo(sid: str):
    """A Scholar profile photo, downloaded once and kept in data/photos (404 if they have none)."""
    if not re.fullmatch(r"[\w-]{12}", sid):
        raise HTTPException(404)
    path = PHOTO_DIR / f"{sid}.jpg"
    missing = PHOTO_DIR / f"{sid}.none"  # remembers "no photo" so Scholar isn't asked every time
    if not path.exists():
        if missing.exists():
            raise HTTPException(404)
        url = next((((t.get("profile") or {}).get("photo_url")) for t in db.list_topics()
                    if (t.get("author_ids") or {}).get("scholar") == sid), None)
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=30) as c:
                url = url or (await sources.scholar_page(c, sid))["photo"]
                r = await c.get(url, headers={"User-Agent": sources.BROWSER_UA}) if url else None
        except (httpx.HTTPError, sources.ScholarUnavailable):
            raise HTTPException(404)  # try again next time
        PHOTO_DIR.mkdir(parents=True, exist_ok=True)
        if r is None or r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
            missing.touch()
            raise HTTPException(404)
        path.write_bytes(r.content)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=604800"})


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
    try:
        md = await summarizer.deep_dive(topic, p)
    except API_ERRORS as e:
        raise HTTPException(502, explain(e))
    if summarizer.enabled:
        db.update_paper(paper_id, deep_dive=md)
    return {"markdown": md}


class AskIn(BaseModel):
    question: str = Field(min_length=1)
    history: list[dict] = []


@app.post("/api/papers/{paper_id}/ask")
async def ask(paper_id: int, body: AskIn):
    p, _ = _paper_and_topic(paper_id)
    try:
        return {"answer": await summarizer.ask(p, body.question, body.history)}
    except API_ERRORS as e:
        raise HTTPException(502, explain(e))


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
