"""Paper library: the PDFs in a folder you choose (any local folder, or one inside Google Drive for
desktop, Dropbox or OneDrive if you want it synced), indexed and tagged against your interests.

The folder is the source of truth: sub-folders are collections, files you drop in show up on the
next scan, and moving or renaming a file keeps its notes (files are matched by content hash).
"""

import asyncio
import hashlib
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import config, db, sources
from .summarizer import API_ERRORS, explain, keyword_tags, summarizer

log = logging.getLogger("frontline.library")
logging.getLogger("pypdf").setLevel(logging.ERROR)  # malformed PDFs are common and noisy

router = APIRouter(prefix="/api/library")

TRASH = ".frontline-trash"   # removed papers go here, inside the library, so nothing is lost
STATUSES = ("unread", "reading", "read")
MAX_PDF = 80 * 1024 * 1024
ARXIV_FILE_RE = re.compile(r"(?<!\d)(\d{4}\.\d{4,5})(?:v\d+)?(?!\d)")
ARXIV_TEXT_RE = re.compile(r"arXiv:\s*(\d{4}\.\d{4,5})", re.I)
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+)", re.I)
BAD_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


# ---------------------------------------------------------------- settings

def root() -> Path | None:
    d = db.get_setting("library_dir") or config.LIBRARY_DIR
    return Path(d).expanduser() if d else None


def _require_root() -> Path:
    base = root()
    if not base or not base.is_dir():
        raise HTTPException(409, "Pick your library folder first")
    return base


def interests() -> list[str]:
    """What the library is sorted and tagged by. Defaults to your Radar topics and subtopics."""
    saved = db.get_setting("interests")
    if saved is not None:
        return saved
    out: list[str] = []
    for t in db.list_topics():
        if t["kind"] == "topic":
            for s in [t["name"], *t["subtopics"]]:
                if s not in out:
                    out.append(s)
    return out


def drive_suggestions() -> list[str]:
    """A 'Research Papers' folder in Documents, then in any cloud-synced folders on this machine."""
    home = Path.home()
    found: list[Path] = [home / "Documents" if (home / "Documents").is_dir() else home]
    cloud = home / "Library" / "CloudStorage"  # macOS: Google Drive, Dropbox, OneDrive, Box
    if cloud.is_dir():
        for p in sorted(cloud.iterdir()):
            if p.name.startswith(("GoogleDrive", "Dropbox", "OneDrive", "Box")):
                found.append(p / "My Drive" if (p / "My Drive").is_dir() else p)
    if sys.platform == "win32":
        found += [Path(f"{d}:/My Drive") for d in "GHIJKLM" if Path(f"{d}:/My Drive").is_dir()]
    for p in (home / "Google Drive" / "My Drive", home / "Google Drive", home / "My Drive",
              home / "Dropbox", home / "OneDrive"):
        if p.is_dir() and p not in found and p.parent not in found:
            found.append(p)
    return [str(p / "Research Papers") for p in found]


# ---------------------------------------------------------------- files & paths

def _abs(base: Path, rel: str) -> Path:
    p = (base / rel).resolve()
    if not p.is_relative_to(base.resolve()):
        raise HTTPException(400, "Path is outside the library folder")
    return p


def clean_collection(name: str) -> str:
    """'Parent / Child' -> 'Parent/Child'; rejects anything that could escape the folder."""
    parts = [BAD_FILENAME.sub("", p).strip().strip(".") for p in re.split(r"[/\\]+", name or "")]
    parts = [p for p in parts if p]
    if any(p.startswith(".") for p in parts):
        raise HTTPException(422, "Collection names can't start with a dot")
    return "/".join(parts)


def _unique(path: Path) -> Path:
    n, out = 2, path
    while out.exists():
        out, n = path.with_name(f"{path.stem} ({n}){path.suffix}"), n + 1
    return out


def _venue(v: str) -> str:
    """arXiv's comment field doubles as a venue; keep it only when it names one."""
    if not v or v == "arXiv" or re.search(r"\b(pages?|figures?|tables?|appendix|pp\.)\b", v, re.I):
        return ""
    return v


def _paper_filename(authors: list[str], year: str, title: str) -> str:
    first = sources.surname(authors[0]).title() if authors else ""
    stem = " ".join(x for x in (first, year) if x)
    stem = f"{stem} - {title}" if stem else title
    stem = BAD_FILENAME.sub("", stem).strip(" .")[:120] or "paper"
    return stem + ".pdf"


def _list_pdfs(base: Path) -> dict[str, int]:
    """rel_path -> size for every PDF under the folder (hidden folders and the trash are skipped)."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for f in filenames:
            if f.lower().endswith(".pdf") and not f.startswith((".", "~$")):
                p = Path(dirpath) / f
                try:
                    out[p.relative_to(base).as_posix()] = p.stat().st_size
                except OSError:
                    continue
    return out


def collections(base: Path) -> list[str]:
    out = []
    for dirpath, dirnames, _ in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        rel = Path(dirpath).relative_to(base).as_posix()
        if rel != ".":
            out.append(rel)
        if rel.count("/") >= 2:
            dirnames[:] = []
    return out


def _hash(path: Path) -> str:
    """Size + first MiB: cheap, and enough to recognise a file after a move or rename."""
    h = hashlib.sha1(str(path.stat().st_size).encode())
    with open(path, "rb") as f:
        h.update(f.read(1 << 20))
    return h.hexdigest()


def _good_title(t: str) -> bool:
    t = t.strip()
    return (8 <= len(t) <= 300 and " " in t
            and not re.search(r"\.(pdf|dvi|docx?|tex)$|^untitled|^microsoft word|^arxiv:", t, re.I))


def read_pdf(path: Path) -> dict:
    """Best-effort metadata from the PDF itself: title, authors, year, first-pages text, IDs."""
    from pypdf import PdfReader

    out = {"title": "", "authors": [], "year": "", "excerpt": "", "arxiv_id": "", "doi": ""}
    try:
        reader = PdfReader(str(path))
        meta = reader.metadata or {}
        title = str(meta.get("/Title") or "")
        if _good_title(title):
            out["title"] = re.sub(r"\s+", " ", title).strip()
        author = str(meta.get("/Author") or "")
        if 2 < len(author) < 400:
            out["authors"] = [a.strip() for a in re.split(r";|,| and ", author) if len(a.strip()) > 2][:30]
        m = re.match(r"D:(\d{4})", str(meta.get("/CreationDate") or ""))
        if m:
            out["year"] = m.group(1)
        text = "\n".join((p.extract_text() or "") for p in reader.pages[:2])
        out["excerpt"] = re.sub(r"[ \t]+", " ", text).strip()[:8000]
    except Exception as e:  # pypdf raises many types on damaged files
        log.info("Could not read %s: %s", path.name, e)
    m = ARXIV_FILE_RE.search(path.name) or ARXIV_TEXT_RE.search(out["excerpt"][:3000])
    if m:
        out["arxiv_id"] = m.group(1)
    m = DOI_RE.search(out["excerpt"][:4000])
    if m:
        out["doi"] = m.group(1).rstrip(".,;)]")
    if not out["title"]:
        lines = [l.strip() for l in out["excerpt"].splitlines()[:15]]
        guess = next((l for l in lines if 4 <= len(l.split()) <= 25 and not re.search(r"arxiv|@|\d{4}\.\d{4}", l, re.I)), "")
        out["title"] = guess or re.sub(r"[_\-]+", " ", path.stem).strip()
    return out


# ---------------------------------------------------------------- indexing

class LibraryManager:
    """Background scan of the library folder: new files are read, looked up and tagged."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.state: dict = {"running": False}
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._lock.locked()

    def start(self, retag: bool = False) -> bool:
        if self.running:
            return False
        self._task = asyncio.create_task(self.run(retag))
        return True

    async def run(self, retag: bool = False) -> None:
        async with self._lock:
            self.state = {"running": True, "phase": "scanning", "done": 0, "total": 0, "added": 0, "message": "Scanning folder…"}
            try:
                await self._scan(retag)
                self.state["message"] = (f"Added {self.state['added']} paper{'s' if self.state['added'] != 1 else ''}"
                                         if self.state["added"] else "Library is up to date")
            except Exception as e:
                log.exception("library scan failed")
                self.state["message"] = f"Scan failed: {e}"
                self.state["error"] = str(e)
            finally:
                self.state.update(running=False, phase="done", finished_at=db.now_iso())

    async def _scan(self, retag: bool) -> None:
        base = root()
        if not base or not base.is_dir():
            raise RuntimeError("the library folder doesn't exist")
        files = await asyncio.to_thread(_list_pdfs, base)
        rows = db.list_records("library")
        by_path = {r["rel_path"]: r for r in rows}
        gone = {r["id"]: r for r in rows if r["rel_path"] not in files}
        new = [rel for rel in files if rel not in by_path]
        for r in rows:
            if r["rel_path"] in files and r["missing"]:
                db.update_record("library", r["id"], {"missing": False})

        # a file whose content matches a vanished row was moved or renamed: keep its notes
        by_hash = {r["file_hash"]: r for r in gone.values() if r["file_hash"]}
        fresh = []
        for rel in new:
            try:
                h = await asyncio.to_thread(_hash, base / rel)
            except OSError:
                continue
            if h in by_hash and by_hash[h]["id"] in gone:
                moved = gone.pop(by_hash[h]["id"])
                db.update_record("library", moved["id"], {"rel_path": rel, "missing": False})
            else:
                fresh.append((rel, h))
        for r in gone.values():
            untouched = (not r["notes"] and not r["starred"] and r["status"] == "unread"
                         and r["tagged_by"] != "manual")
            if untouched:
                db.delete_record("library", r["id"])
            elif not r["missing"]:
                db.update_record("library", r["id"], {"missing": True})

        todo = [r for r in db.list_records("library") if r["tagged_by"] == "" or (retag and r["tagged_by"] != "manual")]
        self.state.update(phase="indexing", total=len(fresh) + len(todo),
                          message=f"Reading {len(fresh)} new PDF{'s' if len(fresh) != 1 else ''}…" if fresh else "Tagging…")
        sem = asyncio.Semaphore(max(1, config.CONCURRENCY))
        async with httpx.AsyncClient(headers={"User-Agent": sources.USER_AGENT}, follow_redirects=True) as client:
            async def add(rel: str, h: str) -> None:
                async with sem:
                    row = await self.index_file(base, rel, h, client)
                    if row:
                        self.state["added"] += 1
                        await self.tag(row)
                    self.state["done"] += 1

            async def retag_one(row: dict) -> None:
                async with sem:
                    await self.tag(db.get_record("library", row["id"]) or row)
                    self.state["done"] += 1

            await asyncio.gather(*(add(rel, h) for rel, h in fresh), *(retag_one(r) for r in todo))

    async def index_file(self, base: Path, rel: str, h: str, client: httpx.AsyncClient,
                         extra: dict | None = None) -> dict | None:
        """Insert one PDF (returns None if another scan got there first)."""
        info = await asyncio.to_thread(read_pdf, base / rel)
        found = await sources.lookup(client, info["arxiv_id"], info["doi"]) if (info["arxiv_id"] or info["doi"]) else None
        rec = {
            "rel_path": rel, "file_hash": h, "file_size": (base / rel).stat().st_size,
            "title": info["title"], "authors": info["authors"], "year": info["year"],
            "excerpt": info["excerpt"], "arxiv_id": info["arxiv_id"], "doi": info["doi"],
        }
        if found:
            rec.update(title=found["title"] or rec["title"], authors=found["authors"] or rec["authors"],
                       year=(found["published"] or "")[:4] or rec["year"], abstract=found["abstract"],
                       venue=_venue(found["venue"]), url=found["url"],
                       arxiv_id=found["arxiv_id"] or rec["arxiv_id"], doi=found["doi"] or rec["doi"])
        rec.update(extra or {})
        try:
            return db.insert_record("library", rec)
        except sqlite3.IntegrityError:
            return None

    async def tag(self, row: dict) -> dict:
        ints = interests()
        card = await summarizer.library_card(ints, row)
        if card:
            looked_up = bool(row["abstract"])  # arXiv/OpenAlex metadata beats what the AI read off the page
            upd = {"tldr": card["tldr"], "tags": card["tags"], "relevance": card["relevance"], "tagged_by": "ai"}
            if not looked_up:
                upd.update(title=card["title"] or row["title"], authors=card["authors"] or row["authors"],
                           year=card["year"] or row["year"])
        else:
            text = f"{row['title']} {row['abstract']} {row['excerpt'][:3000]}"
            tags = keyword_tags(ints, text)
            upd = {"tags": tags, "relevance": min(10, 2 * len(tags)), "tagged_by": "keywords"}
            if not row["tldr"] and row["abstract"]:
                upd["tldr"] = re.split(r"(?<=[.!?])\s", row["abstract"], maxsplit=1)[0]
        return db.update_record("library", row["id"], upd)


manager = LibraryManager()


def _public(r: dict) -> dict:
    r = dict(r)
    r.pop("excerpt", None)
    r["has_deep_dive"] = bool(r.pop("deep_dive", ""))
    r["collection"] = r["rel_path"].rsplit("/", 1)[0] if "/" in r["rel_path"] else ""
    r["filename"] = r["rel_path"].rsplit("/", 1)[-1]
    return r


def _get(pid: int) -> dict:
    r = db.get_record("library", pid)
    if not r:
        raise HTTPException(404)
    return r


# ---------------------------------------------------------------- routes

@router.get("")
def list_library():
    return [_public(r) for r in db.list_records("library", order="added_at DESC")]


@router.get("/meta")
def meta():
    base = root()
    ok = bool(base and base.is_dir())
    return {
        "root": str(base) if base else "", "root_exists": ok,
        "interests": interests(), "interests_custom": db.get_setting("interests") is not None,
        "collections": collections(base) if ok else [],
        "suggestions": [] if ok else drive_suggestions(),
        "scan": manager.state, "ai": summarizer.enabled,
    }


class LibrarySettings(BaseModel):
    library_dir: str | None = None
    create: bool = False
    interests: list[str] | None = None
    reset_interests: bool = False  # back to the Radar topics + subtopics


@router.put("/settings")
async def save_settings(body: LibrarySettings):
    if body.library_dir is not None:
        p = Path(body.library_dir.strip().strip('"')).expanduser()
        if not p.is_absolute():
            raise HTTPException(422, "Give the full path of the folder")
        if body.create:
            try:
                p.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise HTTPException(422, f"Couldn't create that folder: {e}")
        if not p.is_dir():
            raise HTTPException(422, "That folder doesn't exist")
        db.set_setting("library_dir", str(p))
        manager.start()
    if body.interests is not None:
        clean = list(dict.fromkeys(i.strip() for i in body.interests if i.strip()))
        db.set_setting("interests", clean)
    if body.reset_interests:
        db.set_setting("interests", None)
    return meta()


class ScanIn(BaseModel):
    retag: bool = False


@router.post("/scan")
async def scan(body: ScanIn | None = None):
    _require_root()
    manager.start(retag=bool(body and body.retag))
    await asyncio.sleep(0)
    return manager.state


@router.get("/scan")
def scan_status():
    return manager.state


@router.post("/collections")
def create_collection(body: dict):
    base = _require_root()
    name = clean_collection(str(body.get("name", "")))
    if not name:
        raise HTTPException(422, "Give the collection a name")
    _abs(base, name).mkdir(parents=True, exist_ok=True)
    return {"collections": collections(base)}


@router.post("/upload")
async def upload(request: Request, name: str, collection: str = ""):
    """Raw PDF bytes in the body (no multipart needed); saved into the library folder."""
    base = _require_root()
    data = await request.body()
    if data[:4] != b"%PDF":
        raise HTTPException(422, f"{name} isn't a PDF")
    if len(data) > MAX_PDF:
        raise HTTPException(413, "That PDF is over 80 MB")
    folder = _abs(base, clean_collection(collection))
    folder.mkdir(parents=True, exist_ok=True)
    fname = BAD_FILENAME.sub("", Path(name).name).strip(" .") or "paper.pdf"
    dest = _unique(folder / (fname if fname.lower().endswith(".pdf") else fname + ".pdf"))
    await asyncio.to_thread(dest.write_bytes, data)
    rel = dest.relative_to(base.resolve()).as_posix()
    async with httpx.AsyncClient(headers={"User-Agent": sources.USER_AGENT}, follow_redirects=True) as client:
        row = await manager.index_file(base.resolve(), rel, await asyncio.to_thread(_hash, dest), client)
    if not row:  # a scan indexed it first
        row = db.list_records("library", "rel_path = ?", (rel,))[0]
    return _public(await manager.tag(row))


class SaveIn(BaseModel):
    collection: str = ""


@router.get("/radar-ids")
def radar_ids():
    """Radar paper IDs already saved to the library (so the Radar can show "In library")."""
    return [r["radar_paper_id"] for r in db.list_records("library", "radar_paper_id IS NOT NULL")]


@router.post("/from-paper/{paper_id}")
async def save_radar_paper(paper_id: int, body: SaveIn | None = None):
    """Download a Radar paper's PDF into the library, carrying over its AI card."""
    p = db.get_paper(paper_id)
    if not p:
        raise HTTPException(404)
    existing = db.list_records(
        "library", "radar_paper_id = ? OR (arxiv_id != '' AND arxiv_id = ?) OR (doi != '' AND doi = ?)",
        (paper_id, p["arxiv_id"], p["doi"]))
    if existing:
        return {**_public(existing[0]), "already": True}
    base = _require_root().resolve()
    pdf_url = p["pdf_url"] or (f"https://arxiv.org/pdf/{p['arxiv_id']}" if p["arxiv_id"] else "")
    if not pdf_url:
        raise HTTPException(422, "There's no open PDF for this paper. Download it from the paper page and drop it into your library folder.")
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=90) as c:
            r = await c.get(pdf_url, headers={"User-Agent": sources.BROWSER_UA})
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Couldn't download the PDF: {e}")
    if r.status_code != 200 or r.content[:4] != b"%PDF":
        raise HTTPException(502, "The PDF link didn't return a PDF (it may be paywalled). Download it from the paper page instead.")
    year = (p["published"] or "")[:4]
    folder = _abs(base, clean_collection((body.collection if body else "") or ""))
    folder.mkdir(parents=True, exist_ok=True)
    dest = _unique(folder / _paper_filename(p["authors"], year, p["title"]))
    await asyncio.to_thread(dest.write_bytes, r.content)
    topic = db.get_topic(p["topic_id"]) or {}
    ints = interests()
    s = p["summary"] or {}
    tags = [t for t in [topic.get("name", ""), *s.get("subtopics", [])] if t in ints]
    rec = {
        "rel_path": dest.relative_to(base).as_posix(), "file_hash": await asyncio.to_thread(_hash, dest),
        "file_size": len(r.content), "title": p["title"], "authors": p["authors"], "year": year,
        "abstract": p["abstract"], "venue": _venue(p["venue"]), "url": p["url"],
        "arxiv_id": p["arxiv_id"], "doi": p["doi"], "tldr": s.get("tldr", ""), "tags": tags,
        "relevance": p["relevance"], "tagged_by": "radar", "radar_paper_id": paper_id,
        "deep_dive": p["deep_dive"],
    }
    try:
        row = db.insert_record("library", rec)
    except sqlite3.IntegrityError:  # a scan picked the file up first
        row = db.list_records("library", "rel_path = ?", (rec["rel_path"],))[0]
        row = db.update_record("library", row["id"], rec)
    return _public(row)


class LibraryPatch(BaseModel):
    title: str | None = Field(None, min_length=1)
    authors: list[str] | None = None
    year: str | None = None
    tags: list[str] | None = None
    status: str | None = Field(None, pattern="^(unread|reading|read)$")
    starred: bool | None = None
    notes: str | None = None
    collection: str | None = None


@router.patch("/{pid}")
def patch_paper(pid: int, body: LibraryPatch):
    r = _get(pid)
    upd = body.model_dump(exclude_none=True)
    if "tags" in upd:
        upd["tags"] = list(dict.fromkeys(t.strip() for t in upd["tags"] if t.strip()))
        upd["tagged_by"] = "manual"
    if "collection" in upd:
        base = _require_root().resolve()
        src = _abs(base, r["rel_path"])
        folder = _abs(base, clean_collection(upd.pop("collection")))
        if src.parent != folder:
            if not src.is_file():
                raise HTTPException(409, "The PDF isn't in the library folder any more")
            folder.mkdir(parents=True, exist_ok=True)
            dest = _unique(folder / src.name)
            shutil.move(src, dest)
            upd["rel_path"] = dest.relative_to(base).as_posix()
    return _public(db.update_record("library", pid, upd))


@router.delete("/{pid}")
def remove(pid: int):
    """Drop a paper from the library; its PDF moves to the hidden trash folder inside the library."""
    r = _get(pid)
    base = root()
    if base and base.is_dir():
        src = _abs(base, r["rel_path"])
        if src.is_file():
            trash = base / TRASH
            trash.mkdir(exist_ok=True)
            shutil.move(src, _unique(trash / src.name))
    db.delete_record("library", pid)
    return {"ok": True, "trash": str(base / TRASH) if base else ""}


@router.get("/{pid}/file")
def pdf_file(pid: int):
    r = _get(pid)
    path = _abs(_require_root(), r["rel_path"])
    if not path.is_file():
        raise HTTPException(404, "The PDF isn't in the library folder any more")
    return FileResponse(path, media_type="application/pdf", filename=path.name, content_disposition_type="inline")


THUMBS = config.DB_PATH.parent / "thumbs"   # each paper's first figure, cached by file content
THUMB_WIDTH = 480
FIGURE_PAGES = 6                             # figures past the first few pages rarely stand for the paper


def _first_figure(src: Path, dest: Path) -> bool:
    """Save the first real image embedded in the PDF (skipping logos, icons and rules); False if none."""
    from PIL import Image
    from pypdf import PdfReader

    reader = PdfReader(str(src))
    for page in reader.pages[:FIGURE_PAGES]:
        try:
            images = list(page.images)
        except Exception:  # broken image dictionaries are common; try the next page
            continue
        for f in images:
            try:
                img = f.image
            except Exception:
                continue
            w, h = img.size
            if w < 150 or h < 100 or not 0.25 <= w / h <= 4:
                continue
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
                bg = Image.new("RGB", img.size, "white")
                bg.paste(img, mask=img.getchannel("A"))
                img = bg
            img = img.convert("RGB")
            img.thumbnail((THUMB_WIDTH, THUMB_WIDTH * 2))
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(f"{dest.stem}.{threading.get_ident()}.tmp")
            img.save(tmp, "JPEG", quality=85)
            tmp.replace(dest)
            return True
    return False


@router.get("/{pid}/thumb")
async def thumb(pid: int):
    r = _get(pid)
    src = _abs(_require_root(), r["rel_path"])
    if not src.is_file():
        raise HTTPException(404, "The PDF isn't in the library folder any more")
    key = r["file_hash"] or hashlib.sha1(f"{r['rel_path']}:{src.stat().st_size}".encode()).hexdigest()
    dest, none = THUMBS / f"{key}.fig.jpg", THUMBS / f"{key}.none"
    if not dest.is_file():
        found = False
        if not none.is_file():
            try:
                found = await asyncio.to_thread(_first_figure, src, dest)
            except Exception as e:  # unreadable or encrypted PDFs just get the plain tile
                log.info("No figure for %s: %s", r["rel_path"], e)
            if not found:  # remember, so the PDF isn't re-read on every visit
                THUMBS.mkdir(parents=True, exist_ok=True)
                none.touch()
        if not found:
            raise HTTPException(404, "No figure in this PDF")
    return FileResponse(dest, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


class RevealIn(BaseModel):
    folder: bool = False


@router.post("/{pid}/reveal")
def reveal(pid: int, body: RevealIn, request: Request):
    """Open the PDF in the system's PDF app, or show it in Finder / Explorer."""
    if request.client and request.client.host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(403, "Only available on this computer")
    r = _get(pid)
    path = _abs(_require_root(), r["rel_path"])
    if not path.exists():
        raise HTTPException(404, "The PDF isn't in the library folder any more")
    if sys.platform == "win32":
        if body.folder:
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            os.startfile(path)  # noqa: S606 — the user's own file, opened on their own machine
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)] if body.folder else ["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent if body.folder else path)])
    return {"ok": True}


def _as_paper(r: dict) -> dict:
    base = root()
    return {**r, "pdf_path": str(_abs(base, r["rel_path"])) if base else "",
            "published": r["year"], "sources": ["Library"]}


@router.post("/{pid}/retag")
async def retag(pid: int):
    r = _get(pid)
    return _public(await manager.tag({**r, "tagged_by": ""}))


@router.post("/{pid}/deep-dive")
async def deep_dive(pid: int, refresh: bool = False):
    r = _get(pid)
    if r["deep_dive"] and not refresh:
        return {"markdown": r["deep_dive"]}
    ints = interests()
    topic = {"name": ", ".join(ints[:4]) or "my research", "description": ", ".join(ints[4:12])}
    try:
        md = await summarizer.deep_dive(topic, _as_paper(r))
    except API_ERRORS as e:
        raise HTTPException(502, explain(e))
    if summarizer.enabled:
        db.update_record("library", pid, {"deep_dive": md})
    return {"markdown": md}


class AskIn(BaseModel):
    question: str = Field(min_length=1)
    history: list[dict] = []


@router.post("/{pid}/ask")
async def ask(pid: int, body: AskIn):
    try:
        return {"answer": await summarizer.ask(_as_paper(_get(pid)), body.question, body.history)}
    except API_ERRORS as e:
        raise HTTPException(502, explain(e))
