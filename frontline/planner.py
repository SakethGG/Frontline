"""Ideation center (ideas, proposals, roadmaps, blog posts), project planning (projects + tasks) and personal deadlines."""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import db, library
from .summarizer import API_ERRORS, Refused, explain, summarizer

router = APIRouter(prefix="/api")

IDEA_KINDS = "^(idea|project|proposal|roadmap|blog)$"
IDEA_STATUSES = "^(spark|exploring|developing|active|published|parked)$"  # blogs show these as writing stages
TASK_STATUSES = "^(todo|doing|done|future)$"
PROJECT_COLORS = ["#ff8b7b", "#fff27a", "#8fe3a4", "#7cc4ff", "#c9a7ff", "#ffb86b", "#5ee6d0", "#f78fb3"]  # as in projects.js


def _get(table: str, rid: int) -> dict:
    r = db.get_record(table, rid)
    if not r:
        raise HTTPException(404)
    return r


# ---------------------------------------------------------------- ideas

class PaperRef(BaseModel):
    source: str = Field(pattern="^(library|radar)$")
    id: int
    title: str = ""


class Milestone(BaseModel):
    title: str = Field(min_length=1)
    detail: str = ""
    weeks: int | None = None
    done: bool = False


class QuickNote(BaseModel):
    text: str = ""
    color: str = Field("", pattern=r"^(#[0-9a-fA-F]{6})?$")
    at: str = ""


class IdeaIn(BaseModel):
    title: str | None = Field(None, min_length=1)
    body: str | None = None
    kind: str | None = Field(None, pattern=IDEA_KINDS)
    status: str | None = Field(None, pattern=IDEA_STATUSES)
    tags: list[str] | None = None
    papers: list[PaperRef] | None = None
    milestones: list[Milestone] | None = None
    notes: list[QuickNote] | None = None
    meta: dict | None = None  # blog posts: {hook, target, url}
    pinned: bool | None = None


@router.get("/ideas")
def ideas():
    return db.list_records("ideas", order="pinned DESC, created_at ASC, id ASC")  # oldest first


@router.post("/ideas")
def create_idea(body: IdeaIn):
    d = body.model_dump(exclude_none=True)
    if not d.get("title"):
        raise HTTPException(422, "Give the idea a title")
    return db.insert_record("ideas", d)


@router.patch("/ideas/{iid}")
def update_idea(iid: int, body: IdeaIn):
    _get("ideas", iid)
    d = body.model_dump(exclude_none=True)
    if "tags" in d:
        d["tags"] = list(dict.fromkeys(t.strip() for t in d["tags"] if t.strip()))
    return db.update_record("ideas", iid, d)


@router.delete("/ideas/{iid}")
def delete_idea(iid: int):
    db.delete_record("ideas", iid)
    return {"ok": True}


def _linked_papers(idea: dict) -> list[dict]:
    out = []
    for ref in idea.get("papers") or []:
        if ref["source"] == "library" and (r := db.get_record("library", ref["id"])):
            out.append({"title": r["title"], "tldr": r["tldr"] or r["abstract"][:300]})
        elif ref["source"] == "radar" and (p := db.get_paper(ref["id"])):
            out.append({"title": p["title"], "tldr": (p["summary"] or {}).get("tldr", "")})
    return out


def _context() -> str:
    topics = [f"- {t['name']}: {t['description']}" for t in db.list_topics() if t["kind"] == "topic"]
    ints = library.interests()
    return "\n".join(["Research topics:", *topics] + ([f"Interests: {', '.join(ints)}"] if ints else []))


class AssistIn(BaseModel):
    mode: str = Field(pattern="^(critique|expand|proposal|roadmap|outline|titles|edit)$")  # the last three: blogs


@router.post("/ideas/{iid}/assist")
async def assist(iid: int, body: AssistIn):
    idea = _get("ideas", iid)
    papers = _linked_papers(idea)
    try:
        if body.mode == "roadmap":
            steps = await summarizer.idea_roadmap(idea, papers, _context())
            ms = (idea["milestones"] or []) + [{**s, "done": False} for s in steps]
            return {"idea": db.update_record("ideas", iid, {"milestones": ms}), "added": len(steps)}
        return {"markdown": await summarizer.idea_assist(body.mode, idea, papers, _context())}
    except ValueError as e:
        raise HTTPException(409, str(e))
    except Refused:
        raise HTTPException(422, f"{summarizer.name} declined this request")
    except API_ERRORS as e:
        raise HTTPException(502, explain(e))


@router.post("/ideas/{iid}/project")
def start_project(iid: int):
    """Promote an idea to a project; its roadmap milestones become tasks (the first one now, the rest later)."""
    idea = _get("ideas", iid)
    body = (idea["body"] or "").strip()
    first_para = body.split("\n\n", 1)[0][:600] if body else ""
    color = PROJECT_COLORS[len(db.list_records("projects")) % len(PROJECT_COLORS)]
    proj = db.insert_record("projects", {"name": idea["title"], "description": first_para, "idea_id": iid, "color": color})
    open_ms = [m for m in idea["milestones"] or [] if not m.get("done")]
    for i, m in enumerate(open_ms):
        db.insert_record("tasks", {"project_id": proj["id"], "title": m["title"], "notes": m.get("detail", ""),
                                   "status": "todo" if i == 0 else "future"})
    if idea["status"] in ("spark", "exploring", "developing"):
        db.update_record("ideas", iid, {"status": "active"})
    return proj


# ---------------------------------------------------------------- projects

class ProjectIn(BaseModel):
    name: str | None = Field(None, min_length=1)
    description: str | None = None
    color: str | None = Field(None, pattern=r"^(#[0-9a-fA-F]{6})?$")
    status: str | None = Field(None, pattern="^(active|paused|done)$")
    conference_id: int | None = None
    idea_id: int | None = None
    notes: list[QuickNote] | None = None
    clear_conference: bool = False


@router.get("/projects")
def projects():
    counts: dict[int, dict] = {}
    for t in db.list_records("tasks"):
        c = counts.setdefault(t["project_id"] or 0, {"todo": 0, "doing": 0, "done": 0, "future": 0})
        c[t["status"]] = c.get(t["status"], 0) + 1
    order = "CASE status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 ELSE 2 END, created_at"
    return [{**p, "counts": counts.get(p["id"], {})} for p in db.list_records("projects", order=order)]


@router.post("/projects")
def create_project(body: ProjectIn):
    d = body.model_dump(exclude_none=True, exclude={"clear_conference"})
    if not d.get("name"):
        raise HTTPException(422, "Give the project a name")
    return db.insert_record("projects", d)


@router.patch("/projects/{pid}")
def update_project(pid: int, body: ProjectIn):
    _get("projects", pid)
    d = body.model_dump(exclude_none=True, exclude={"clear_conference"})
    if body.clear_conference:
        d["conference_id"] = None
    return db.update_record("projects", pid, d)


@router.delete("/projects/{pid}")
def delete_project(pid: int):
    db.delete_record("projects", pid)  # its tasks go with it (ON DELETE CASCADE)
    return {"ok": True}


# ---------------------------------------------------------------- tasks

class TaskIn(BaseModel):
    title: str | None = Field(None, min_length=1)
    notes: str | None = None
    status: str | None = Field(None, pattern=TASK_STATUSES)
    priority: int | None = Field(None, ge=0, le=2)
    due: str | None = Field(None, pattern=r"^(\d{4}-\d{2}-\d{2})?$")
    project_id: int | None = None
    clear_project: bool = False


@router.get("/tasks")
def tasks():
    return db.list_records("tasks", order="priority DESC, CASE WHEN due = '' THEN 1 ELSE 0 END, due, id")


@router.post("/tasks")
def create_task(body: TaskIn):
    d = body.model_dump(exclude_none=True, exclude={"clear_project"})
    if not d.get("title"):
        raise HTTPException(422, "Give the task a title")
    if d.get("project_id"):
        _get("projects", d["project_id"])
    if d.get("status") == "done":
        d["done_at"] = db.now_iso()
    return db.insert_record("tasks", d)


@router.patch("/tasks/{tid}")
def update_task(tid: int, body: TaskIn):
    t = _get("tasks", tid)
    d = body.model_dump(exclude_none=True, exclude={"clear_project"})
    if body.clear_project:
        d["project_id"] = None
    if "status" in d and d["status"] != t["status"]:
        d["done_at"] = db.now_iso() if d["status"] == "done" else None
    return db.update_record("tasks", tid, d)


@router.delete("/tasks/{tid}")
def delete_task(tid: int):
    db.delete_record("tasks", tid)
    return {"ok": True}


# ---------------------------------------------------------------- personal deadlines

class DeadlineIn(BaseModel):
    title: str | None = Field(None, min_length=1)
    at: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")  # ISO instant, from the browser
    all_day: bool | None = None
    notes: str | None = None
    done: bool | None = None
    project_id: int | None = None
    clear_project: bool = False


def _utc(d: dict) -> dict:
    if "at" in d:
        try:
            d["at"] = datetime.fromisoformat(d["at"].replace("Z", "+00:00")).astimezone(timezone.utc).isoformat(timespec="seconds")
        except ValueError:
            raise HTTPException(422, "That date isn't valid")
    return d


@router.get("/deadlines")
def deadlines():
    return db.list_records("deadlines", order="at")


@router.post("/deadlines")
def create_deadline(body: DeadlineIn):
    d = body.model_dump(exclude_none=True, exclude={"clear_project"})
    if not d.get("title") or not d.get("at"):
        raise HTTPException(422, "Give the deadline a name and a date")
    if d.get("project_id"):
        _get("projects", d["project_id"])
    return db.insert_record("deadlines", _utc(d))


@router.patch("/deadlines/{did}")
def update_deadline(did: int, body: DeadlineIn):
    _get("deadlines", did)
    d = body.model_dump(exclude_none=True, exclude={"clear_project"})
    if body.clear_project:
        d["project_id"] = None
    return db.update_record("deadlines", did, _utc(d))


@router.delete("/deadlines/{did}")
def delete_deadline(did: int):
    db.delete_record("deadlines", did)
    return {"ok": True}
