"""Conferences & workshops: a watchlist of venues with their deadlines.

Venues come from Hugging Face's community-maintained AI deadlines catalog (one YAML file per
series, covering ML, CV, NLP, robotics and more) or are added by hand (handy for workshops).
Tracked catalog venues are kept up to date, and a series' next edition is added automatically.
"""

import asyncio
import io
import json
import logging
import re
import tarfile
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import yaml
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import config, db

log = logging.getLogger("frontline.conferences")

router = APIRouter(prefix="/api/conferences")

CATALOG_FILE = config.DB_PATH.parent / "conference_catalog.json"
CATALOG_MAX_AGE = timedelta(hours=24)
AOE = timezone(timedelta(hours=-12), "AoE")
TZ_ALIASES = {
    "PST": "America/Los_Angeles", "PDT": "America/Los_Angeles", "PT": "America/Los_Angeles",
    "EST": "America/New_York", "EDT": "America/New_York", "ET": "America/New_York",
    "CST": "America/Chicago", "CET": "Europe/Paris", "CEST": "Europe/Paris",
    "BST": "Europe/London", "JST": "Asia/Tokyo", "KST": "Asia/Seoul", "IST": "Asia/Kolkata",
}
_catalog: dict = {"fetched_at": None, "items": []}


# ---------------------------------------------------------------- parsing

def parse_tz(name: str | None):
    n = (name or "").strip()
    if not n or n.lower() == "aoe":
        return AOE
    m = re.fullmatch(r"(?:UTC|GMT)\s*(?:([+-])\s*(\d{1,2})(?::?(\d{2}))?)?", n, re.I)
    if m:
        if not m.group(1):
            return timezone.utc
        mins = int(m.group(2)) * 60 + int(m.group(3) or 0)
        return timezone(timedelta(minutes=mins if m.group(1) == "+" else -mins))
    try:
        return ZoneInfo(TZ_ALIASES.get(n.upper(), n))
    except Exception:  # unknown name, or no tz database on this machine (install tzdata)
        return timezone.utc


def parse_deadline(value, tz_name: str | None) -> str | None:
    """A catalog/user date ('2026-05-29 11:59:00', '2026-05-29') in its timezone -> UTC ISO."""
    if value is None or value == "":
        return None
    s = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            break
        except ValueError:
            continue
    else:
        return None
    if len(s) == 10:  # a bare date means the end of that day
        dt = dt.replace(hour=23, minute=59, second=59)
    return dt.replace(tzinfo=parse_tz(tz_name)).astimezone(timezone.utc).isoformat()


def _label(kind: str) -> str:
    return kind.replace("_", " ").replace("-", " ").strip().capitalize() or "Deadline"


def normalize(e: dict) -> dict | None:
    title = str(e.get("title") or "").strip()
    if not title:
        return None
    year = e.get("year")
    tz_default = e.get("timezone")
    deadlines = []
    for d in e.get("deadlines") or []:
        tz = d.get("timezone") or tz_default
        at = parse_deadline(d.get("date"), tz)
        if at:
            kind = str(d.get("type") or "deadline")
            deadlines.append({"type": kind, "label": str(d.get("label") or _label(kind)), "at": at, "tz": str(tz or "AoE").strip()})
    for field, kind, label in (("abstract_deadline", "abstract", "Abstract submission"),
                               ("deadline", "paper", "Paper submission")):
        at = parse_deadline(e.get(field), tz_default)
        if at and not any(d["at"] == at for d in deadlines):
            deadlines.append({"type": kind, "label": label, "at": at, "tz": str(tz_default or "AoE").strip()})
    deadlines.sort(key=lambda d: d["at"])
    loc = ", ".join(str(x) for x in (e.get("city"), e.get("country")) if x)
    return {
        "source_key": str(e.get("id") or f"{title}{year}").lower(),
        "kind": "workshop" if "workshop" in title.lower() else "conference",
        "name": title, "full_name": str(e.get("full_name") or ""),
        "year": int(year) if str(year or "").isdigit() else None,
        "url": str(e.get("link") or ""), "location": loc or str(e.get("venue") or ""),
        "start_date": str(e.get("start") or ""), "end_date": str(e.get("end") or ""),
        "date_text": str(e.get("date") or ""),
        "tags": list(dict.fromkeys(re.sub(r"[\s_]+", "-", str(t).strip().lower()) for t in e.get("tags") or [])),
        "rankings": str(e.get("rankings") or ""),
        "notes": str(e.get("note") or ""),
        "deadlines": deadlines,
    }


def _parse_tarball(data: bytes) -> list[dict]:
    items = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for m in tar.getmembers():  # read in memory only; nothing is extracted to disk
            if not (m.isfile() and "/src/data/conferences/" in m.name and m.name.endswith((".yml", ".yaml"))):
                continue
            try:
                docs = yaml.safe_load(tar.extractfile(m).read()) or []
            except yaml.YAMLError as e:
                log.info("Skipping %s: %s", m.name, e)
                continue
            for e in docs if isinstance(docs, list) else [docs]:
                if isinstance(e, dict) and (n := normalize(e)):
                    items.append(n)
    return items


def _is_upcoming(c: dict, now: str) -> bool:
    today = now[:10]
    return any(d["at"] >= now for d in c["deadlines"]) or (c["end_date"] or c["start_date"] or "") >= today


def _is_recent(c: dict, now: str) -> bool:
    """Upcoming, or held within the last year (track it to get the series' next edition)."""
    year_ago = (datetime.fromisoformat(now) - timedelta(days=365)).isoformat()
    last = max([d["at"] for d in c["deadlines"]] + [c["end_date"] or c["start_date"] or ""])
    return _is_upcoming(c, now) or last >= year_ago[:10]


# ---------------------------------------------------------------- catalog

def _load_cached() -> None:
    if _catalog["fetched_at"] is None and CATALOG_FILE.exists():
        try:
            _catalog.update(json.loads(CATALOG_FILE.read_text("utf-8")))
        except (OSError, ValueError):
            pass


def _stale() -> bool:
    _load_cached()
    f = _catalog["fetched_at"]
    return not f or datetime.now(timezone.utc) - datetime.fromisoformat(f) > CATALOG_MAX_AGE


async def refresh_catalog() -> None:
    async with httpx.AsyncClient(follow_redirects=True, timeout=60) as c:
        r = await c.get(config.CONFERENCE_CATALOG_URL)
        r.raise_for_status()
    items = await asyncio.to_thread(_parse_tarball, r.content)
    if not items:
        raise ValueError("the catalog came back empty")
    _catalog.update(fetched_at=db.now_iso(), items=items)
    CATALOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_FILE.write_text(json.dumps(_catalog), "utf-8")
    _sync_tracked()


def _sync_tracked() -> None:
    """Update tracked catalog venues from the catalog, and add newer editions of their series."""
    by_key = {c["source_key"]: c for c in _catalog["items"]}
    tracked = db.list_records("conferences")
    keys = {t["source_key"] for t in tracked if t["source_key"]}
    ignored = set(db.get_setting("conf_ignored", []))
    for t in tracked:
        fresh = by_key.get(t["source_key"] or "")
        if fresh and not t["custom"]:
            db.update_record("conferences", t["id"], {k: v for k, v in fresh.items() if k != "notes" or not t["notes"]})
    series = {(t["name"].lower(), t["year"] or 0) for t in tracked if t["source_key"]}
    for c in _catalog["items"]:
        newer = any(name == c["name"].lower() and (c["year"] or 0) > year for name, year in series)
        if newer and c["source_key"] not in keys and c["source_key"] not in ignored:
            db.insert_record("conferences", c)
            keys.add(c["source_key"])
            log.info("Tracking the next edition: %s %s", c["name"], c["year"])


async def refresh_if_stale() -> None:
    """Startup: keep tracked deadlines current without the user pressing anything."""
    if _stale() and db.list_records("conferences", "source_key IS NOT NULL"):
        try:
            await refresh_catalog()
        except (httpx.HTTPError, ValueError, tarfile.TarError) as e:
            log.info("Conference catalog refresh failed: %s", e)


# ---------------------------------------------------------------- routes

@router.get("")
def tracked():
    return db.list_records("conferences", order="start_date, name")


@router.get("/catalog")
async def catalog(refresh: bool = False):
    if refresh or _stale():
        try:
            await refresh_catalog()
        except (httpx.HTTPError, ValueError, tarfile.TarError) as e:
            if not _catalog["items"]:
                raise HTTPException(502, f"Couldn't download the conference catalog: {e}")
            log.info("Catalog refresh failed, using cached copy: %s", e)
    now = db.now_iso()
    keys = {t["source_key"]: t["id"] for t in db.list_records("conferences", "source_key IS NOT NULL")}
    items = [{**c, "tracked_id": keys.get(c["source_key"]), "upcoming": _is_upcoming(c, now)}
             for c in _catalog["items"] if _is_recent(c, now)]
    # soonest open deadline first; then upcoming venues with no open deadline; then past ones
    items.sort(key=lambda c: (not c["upcoming"], next((d["at"] for d in c["deadlines"] if d["at"] >= now), "9999"),
                              c["start_date"] or "9999"))
    return {"fetched_at": _catalog["fetched_at"], "items": items,
            "tags": sorted({t for c in _catalog["items"] for t in c["tags"]})}


@router.get("/focus")
def get_focus():
    return db.get_setting("conf_focus", [])


@router.put("/focus")
def set_focus(body: list[str]):
    db.set_setting("conf_focus", list(dict.fromkeys(t.strip() for t in body if t.strip())))
    return get_focus()


class DeadlineIn(BaseModel):
    label: str = Field(min_length=1)
    type: str = "deadline"
    date: str = Field(min_length=10, description="YYYY-MM-DD or YYYY-MM-DD HH:MM")
    tz: str = "AoE"


class ConferenceIn(BaseModel):
    source_key: str | None = None   # track this catalog venue; the other fields are then ignored
    kind: str = Field("conference", pattern="^(conference|workshop|journal|other)$")
    name: str = ""
    full_name: str = ""
    year: int | None = None
    parent: str = ""
    url: str = ""
    location: str = ""
    start_date: str = ""
    end_date: str = ""
    tags: list[str] = []
    notes: str = ""
    deadlines: list[DeadlineIn] = []


def _manual(body: ConferenceIn) -> dict:
    if not body.name.strip():
        raise HTTPException(422, "Give it a name")
    deadlines = []
    for d in body.deadlines:
        at = parse_deadline(d.date.replace("T", " "), d.tz)
        if not at:
            raise HTTPException(422, f"Couldn't read the date for “{d.label}”")
        deadlines.append({"type": d.type, "label": d.label.strip(), "at": at, "tz": d.tz})
    d = body.model_dump(exclude={"source_key"})
    d.update(name=body.name.strip(), deadlines=sorted(deadlines, key=lambda x: x["at"]),
             tags=[t.strip() for t in body.tags if t.strip()])
    return d


@router.post("")
def track(body: ConferenceIn):
    if body.source_key:
        _load_cached()
        c = next((c for c in _catalog["items"] if c["source_key"] == body.source_key), None)
        if not c:
            raise HTTPException(404, "That venue isn't in the catalog")
        existing = db.list_records("conferences", "source_key = ?", (c["source_key"],))
        if existing:
            return existing[0]
        db.set_setting("conf_ignored", [k for k in db.get_setting("conf_ignored", []) if k != c["source_key"]])
        return db.insert_record("conferences", c)
    return db.insert_record("conferences", _manual(body))


@router.put("/{cid}")
def edit(cid: int, body: ConferenceIn):
    c = db.get_record("conferences", cid)
    if not c:
        raise HTTPException(404)
    d = _manual(body)
    # deadline/date edits on a catalog venue stick: refreshes then leave it alone
    d["custom"] = bool(c["source_key"]) and (
        [x["at"] for x in d["deadlines"]] != [x["at"] for x in c["deadlines"]]
        or d["start_date"] != c["start_date"] or d["end_date"] != c["end_date"] or c["custom"])
    return db.update_record("conferences", cid, d)


class NotesIn(BaseModel):
    notes: str


@router.patch("/{cid}")
def edit_notes(cid: int, body: NotesIn):
    if not db.get_record("conferences", cid):
        raise HTTPException(404)
    return db.update_record("conferences", cid, {"notes": body.notes})


@router.delete("/{cid}")
def untrack(cid: int):
    c = db.get_record("conferences", cid)
    if c and c["source_key"]:  # don't bring it back as a "next edition"
        db.set_setting("conf_ignored", list({*db.get_setting("conf_ignored", []), c["source_key"]}))
    db.delete_record("conferences", cid)
    return {"ok": True}


# ---------------------------------------------------------------- calendar export

def _ics_text(s: str) -> str:
    return re.sub(r"([,;\\])", r"\\\1", s).replace("\n", "\\n")


def _ics_dt(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def calendar_ics() -> str:
    """Tracked deadlines (with 1-week and 1-day reminders), conference dates, and dated tasks."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    ev = []
    for c in db.list_records("conferences"):
        title = f"{c['name']} {c['year'] or ''}".strip()
        for i, d in enumerate(c["deadlines"]):
            if d["at"] < since:
                continue
            summary = _ics_text("⏰ " + title + ": " + d["label"])
            desc = _ics_text(d["label"] + " (" + d["tz"] + "). " + c["url"])
            ev += ["BEGIN:VEVENT", f"UID:frontline-conf-{c['id']}-{i}@frontline", f"DTSTAMP:{stamp}",
                   f"DTSTART:{_ics_dt(d['at'])}", f"DTEND:{_ics_dt(d['at'])}",
                   f"SUMMARY:{summary}", f"DESCRIPTION:{desc}",
                   "BEGIN:VALARM", "ACTION:DISPLAY", "TRIGGER:-P7D", f"DESCRIPTION:{_ics_text(title)} in a week", "END:VALARM",
                   "BEGIN:VALARM", "ACTION:DISPLAY", "TRIGGER:-P1D", f"DESCRIPTION:{_ics_text(title)} tomorrow", "END:VALARM",
                   "END:VEVENT"]
        try:
            start, end = date.fromisoformat(c["start_date"]), date.fromisoformat(c["end_date"] or c["start_date"])
        except ValueError:
            continue
        ev += ["BEGIN:VEVENT", f"UID:frontline-conf-{c['id']}@frontline", f"DTSTAMP:{stamp}",
               f"DTSTART;VALUE=DATE:{start:%Y%m%d}", f"DTEND;VALUE=DATE:{end + timedelta(days=1):%Y%m%d}",
               f"SUMMARY:{_ics_text(title)}", f"LOCATION:{_ics_text(c['location'])}", "END:VEVENT"]
    projects = {p["id"]: p["name"] for p in db.list_records("projects")}
    for t in db.list_records("tasks", "due != '' AND status != 'done'"):
        try:
            day = date.fromisoformat(t["due"][:10])
        except ValueError:
            continue
        name = f"{projects.get(t['project_id'], '')}: {t['title']}" if t["project_id"] else t["title"]
        ev += ["BEGIN:VEVENT", f"UID:frontline-task-{t['id']}@frontline", f"DTSTAMP:{stamp}",
               f"DTSTART;VALUE=DATE:{day:%Y%m%d}", f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}",
               f"SUMMARY:{_ics_text('☐ ' + name)}", "END:VEVENT"]
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Frontline//Research planner//EN",
             "X-WR-CALNAME:Frontline", "CALSCALE:GREGORIAN", *ev, "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


def ics_response() -> Response:
    return Response(calendar_ics(), media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=frontline.ics"})
