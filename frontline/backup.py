"""Backup & restore: everything you've built up in Frontline, as one file you can carry to another computer.

A backup is a zip with a `.frontline` extension:
    manifest.json   what's inside (format version, when, UI preferences, original library folder)
    frontline.db    a consistent snapshot of the database: topics, researchers, papers, summaries,
                    stars/read marks, library index, ideas, projects, tasks, conferences, settings
    library/…       (optional) the library PDFs, by their path inside the library folder
API keys in .env are never included: copy .env yourself if you want them on the new machine.
"""

import asyncio
import json
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import date
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from . import db, library
from .sweep import manager as sweep_manager

router = APIRouter(prefix="/api/backup")

FORMAT = 1
DB_NAME = "frontline.db"
LIB_PREFIX = "library/"
SQLITE_MAGIC = b"SQLite format 3\x00"
MAX_UPLOAD = 20 * 1024**3


def _busy() -> str | None:
    if sweep_manager.running:
        return "A sweep is running: wait for it to finish first"
    if library.manager.running:
        return "The library is being scanned: wait for it to finish first"
    return None


# ---------------------------------------------------------------- export

def _build(out: Path, ui_prefs: dict, with_pdfs: bool) -> None:
    base = library.root()
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / DB_NAME
        db.snapshot(snap)
        rel_paths = [r[0] for r in sqlite3.connect(snap).execute("SELECT rel_path FROM library")] if with_pdfs else []
        pdfs = [(rel, base / rel) for rel in rel_paths if base and (base / rel).is_file()]
        manifest = {
            "app": "frontline", "format": FORMAT, "created_at": db.now_iso(),
            "ui_prefs": ui_prefs, "library_dir": str(base or ""), "pdfs": len(pdfs),
        }
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json", json.dumps(manifest, indent=2))
            z.write(snap, DB_NAME)
            for rel, p in pdfs:
                z.write(p, LIB_PREFIX + rel, compress_type=zipfile.ZIP_STORED)  # PDFs barely compress


@router.get("")
async def export(pdfs: bool = False, prefs: str = "{}"):
    try:
        ui_prefs = json.loads(prefs)
    except ValueError:
        ui_prefs = {}
    fd, name = tempfile.mkstemp(suffix=".frontline")
    out = Path(name)
    with open(fd, "wb"):
        pass
    try:
        await asyncio.to_thread(_build, out, ui_prefs, pdfs)
    except Exception:
        out.unlink(missing_ok=True)
        raise
    return FileResponse(out, media_type="application/zip",
                        filename=f"frontline-backup-{date.today().isoformat()}.frontline",
                        background=BackgroundTask(out.unlink, missing_ok=True))


# ---------------------------------------------------------------- restore

def _check_db(path: Path) -> None:
    with open(path, "rb") as f:
        if f.read(16) != SQLITE_MAGIC:
            raise HTTPException(422, "That isn't a Frontline backup")
    c = sqlite3.connect(path)
    try:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"topics", "papers", "settings"} <= tables:
            raise HTTPException(422, "That database isn't from Frontline")
        if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise HTTPException(422, "The backup's database is damaged")
    finally:
        c.close()


def _restore_pdfs(z: zipfile.ZipFile, names: list[str], old_dir: str) -> tuple[Path, int]:
    """Put the PDFs back: into the library folder if it exists here, else ~/Frontline Library."""
    current = library.root()
    if current and current.is_dir():
        target = current
    elif old_dir and Path(old_dir).is_dir():
        target = Path(old_dir)
    else:
        target = Path.home() / "Frontline Library"
    target.mkdir(parents=True, exist_ok=True)
    resolved, copied = target.resolve(), 0
    for name in names:
        rel = PurePosixPath(name[len(LIB_PREFIX):])
        if not rel.parts or rel.is_absolute() or ".." in rel.parts:
            continue
        dest = target.joinpath(*rel.parts)
        if not dest.resolve().is_relative_to(resolved) or dest.exists():
            continue  # never write outside the folder, never overwrite your files
        dest.parent.mkdir(parents=True, exist_ok=True)
        with z.open(name) as src, open(dest, "wb") as dst:
            shutil.copyfileobj(src, dst)
        copied += 1
    return target, copied


def _restore(upload: Path) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        dbfile, manifest, z = upload, {}, None
        if zipfile.is_zipfile(upload):
            z = zipfile.ZipFile(upload)
            names = set(z.namelist())
            if DB_NAME not in names:
                raise HTTPException(422, "That isn't a Frontline backup")
            if "manifest.json" in names:
                manifest = json.loads(z.read("manifest.json"))
                if manifest.get("format", FORMAT) > FORMAT:
                    raise HTTPException(422, "This backup is from a newer Frontline: update the app first")
            dbfile = Path(tmp) / DB_NAME
            with z.open(DB_NAME) as src, open(dbfile, "wb") as dst:
                shutil.copyfileobj(src, dst)
        try:
            _check_db(dbfile)
            here = library.root()
            db.restore(dbfile)  # replaces everything, then brings the schema up to date
            restored = library.root()
            if here and here.is_dir() and not (restored and restored.is_dir()):
                db.set_setting("library_dir", str(here))  # the old laptop's path means nothing here
            result = {"ui_prefs": manifest.get("ui_prefs") or {}, "pdfs": 0, "library_dir": ""}
            pdf_names = [n for n in z.namelist() if n.startswith(LIB_PREFIX) and not n.endswith("/")] if z else []
            if pdf_names:
                target, result["pdfs"] = _restore_pdfs(z, pdf_names, manifest.get("library_dir", ""))
                db.set_setting("library_dir", str(target))
            base = library.root()
            result["library_dir"] = str(base) if base and base.is_dir() else ""
            return result
        finally:
            if z:
                z.close()


@router.post("/restore")
async def restore(request: Request):
    """The backup file is the raw request body (no multipart needed)."""
    if msg := _busy():
        raise HTTPException(409, msg)
    fd, name = tempfile.mkstemp(suffix=".frontline")
    upload, size = Path(name), 0
    try:
        with open(fd, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, "That file is too big")
                f.write(chunk)
        if not size:
            raise HTTPException(422, "The file is empty")
        result = await asyncio.to_thread(_restore, upload)
    finally:
        upload.unlink(missing_ok=True)
    if result["library_dir"]:
        library.manager.start()  # index any PDFs that are in the folder but not in the backup
    return result
