"""SQLite persistence for topics, papers and sweep history."""

import json
import sqlite3
import threading
from datetime import datetime, timezone

from . import config

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    queries TEXT NOT NULL DEFAULT '[]',
    subtopics TEXT NOT NULL DEFAULT '[]',
    arxiv_categories TEXT NOT NULL DEFAULT '[]',
    lookback_days INTEGER NOT NULL DEFAULT 7,
    enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS papers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_key TEXT NOT NULL,
    topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    authors TEXT NOT NULL DEFAULT '[]',
    abstract TEXT NOT NULL DEFAULT '',
    published TEXT NOT NULL DEFAULT '',
    sources TEXT NOT NULL DEFAULT '[]',
    url TEXT NOT NULL DEFAULT '',
    pdf_url TEXT NOT NULL DEFAULT '',
    code_url TEXT NOT NULL DEFAULT '',
    venue TEXT NOT NULL DEFAULT '',
    arxiv_id TEXT NOT NULL DEFAULT '',
    doi TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '{}',
    summarized_by TEXT NOT NULL DEFAULT '',
    relevance INTEGER NOT NULL DEFAULT 0,
    deep_dive TEXT NOT NULL DEFAULT '',
    starred INTEGER NOT NULL DEFAULT 0,
    is_read INTEGER NOT NULL DEFAULT 0,
    sweep_id INTEGER,
    discovered_at TEXT NOT NULL,
    UNIQUE (paper_key, topic_id)
);
CREATE INDEX IF NOT EXISTS idx_papers_topic ON papers(topic_id);
CREATE INDEX IF NOT EXISTS idx_papers_published ON papers(published);
CREATE TABLE IF NOT EXISTS sweeps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    trigger TEXT NOT NULL DEFAULT 'manual',
    stats TEXT NOT NULL DEFAULT '{}',
    digest TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- Paper library: one row per PDF in the library folder (rel_path is relative to that folder).
CREATE TABLE IF NOT EXISTS library (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rel_path TEXT UNIQUE NOT NULL,
    file_hash TEXT NOT NULL DEFAULT '',
    file_size INTEGER NOT NULL DEFAULT 0,
    title TEXT NOT NULL,
    authors TEXT NOT NULL DEFAULT '[]',
    year TEXT NOT NULL DEFAULT '',
    abstract TEXT NOT NULL DEFAULT '',
    excerpt TEXT NOT NULL DEFAULT '',
    venue TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    arxiv_id TEXT NOT NULL DEFAULT '',
    doi TEXT NOT NULL DEFAULT '',
    tldr TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    relevance INTEGER NOT NULL DEFAULT 0,
    tagged_by TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'unread',
    starred INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    deep_dive TEXT NOT NULL DEFAULT '',
    missing INTEGER NOT NULL DEFAULT 0,
    radar_paper_id INTEGER,
    added_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
-- Conferences & workshops the user tracks (catalog ones keep their catalog id in source_key).
CREATE TABLE IF NOT EXISTS conferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key TEXT UNIQUE,
    kind TEXT NOT NULL DEFAULT 'conference',
    name TEXT NOT NULL,
    full_name TEXT NOT NULL DEFAULT '',
    year INTEGER,
    parent TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    start_date TEXT NOT NULL DEFAULT '',
    end_date TEXT NOT NULL DEFAULT '',
    date_text TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    rankings TEXT NOT NULL DEFAULT '',
    deadlines TEXT NOT NULL DEFAULT '[]',
    notes TEXT NOT NULL DEFAULT '',
    custom INTEGER NOT NULL DEFAULT 0,  -- edited by hand: catalog refreshes leave it alone
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ideas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'idea',
    status TEXT NOT NULL DEFAULT 'spark',
    tags TEXT NOT NULL DEFAULT '[]',
    papers TEXT NOT NULL DEFAULT '[]',
    milestones TEXT NOT NULL DEFAULT '[]',
    pinned INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    conference_id INTEGER REFERENCES conferences(id) ON DELETE SET NULL,
    idea_id INTEGER REFERENCES ideas(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
-- status: todo | doing | done | future ("future" = the someday/later backlog)
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'todo',
    priority INTEGER NOT NULL DEFAULT 1,
    due TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    done_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
-- Personal deadlines (thesis draft, grant, visa…): `at` is a UTC instant like conference deadlines.
CREATE TABLE IF NOT EXISTS deadlines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    at TEXT NOT NULL,
    all_day INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

# Columns added after the first release; init() adds any that an older database is missing.
# kind is "topic" (keyword search) or "researcher" (a followed person, fetched by author ID).
MIGRATIONS = {
    "topics": {
        "kind": "TEXT NOT NULL DEFAULT 'topic'",
        "author_ids": "TEXT NOT NULL DEFAULT '{}'",
        "profile": "TEXT NOT NULL DEFAULT '{}'",
    },
    "ideas": {
        "notes": "TEXT NOT NULL DEFAULT '[]'",  # quick notes: [{text, color, at}], beside the long-form body
        "meta": "TEXT NOT NULL DEFAULT '{}'",  # per-kind extras, e.g. a blog post's {hook, target, url}
        "binned_at": "TEXT NOT NULL DEFAULT ''",  # completed or deleted: sits in the bin, deleted for good after BIN_DAYS
        "bin_reason": "TEXT NOT NULL DEFAULT ''",  # "completed" | "deleted"
    },
    "projects": {
        "notes": "TEXT NOT NULL DEFAULT '[]'",  # quick notes: [{text, color, at}], in the right-hand sidebar
        "binned_at": "TEXT NOT NULL DEFAULT ''",  # as for ideas
        "bin_reason": "TEXT NOT NULL DEFAULT ''",
    },
}

TOPIC_JSON = ("queries", "subtopics", "arxiv_categories", "author_ids", "profile")
PAPER_JSON = ("authors", "sources", "summary")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA foreign_keys = ON")
        _conn.execute("PRAGMA journal_mode = WAL")
    return _conn


def init() -> None:
    with _lock:
        c = conn()
        c.executescript(SCHEMA)
        for table, cols in MIGRATIONS.items():
            have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
            for col, decl in cols.items():
                if col not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        if c.execute("SELECT COUNT(*) FROM topics").fetchone()[0] == 0:
            for t in config.DEFAULT_TOPICS:
                _insert_topic(c, t)
        # A server restart mid-sweep leaves a dangling "running" row.
        c.execute("UPDATE sweeps SET status='interrupted' WHERE status='running'")
        c.commit()


def snapshot(dest) -> None:
    """Copy the whole database to `dest` as one consistent, self-contained file."""
    out = sqlite3.connect(dest)
    try:
        with _lock:
            conn().backup(out)
        out.execute("PRAGMA journal_mode = DELETE")  # no -wal side file to forget
    finally:
        out.close()


def restore(src) -> None:
    """Replace everything in the database with the contents of `src` (a Frontline database file)."""
    source = sqlite3.connect(src)
    try:
        with _lock:
            source.backup(conn())
    finally:
        source.close()
    init()  # a backup from an older version gets the newer tables and columns


def _row(row: sqlite3.Row | None, json_fields) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for f in json_fields:
        if f in d:
            d[f] = json.loads(d[f] or "null")
    return d


# ---------------------------------------------------------------- topics

def _insert_topic(c: sqlite3.Connection, t: dict) -> int:
    cur = c.execute(
        """INSERT INTO topics (slug, name, description, queries, subtopics, arxiv_categories,
                               lookback_days, enabled, kind, author_ids, profile)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            t["slug"], t["name"], t.get("description", ""),
            json.dumps(t.get("queries", [])), json.dumps(t.get("subtopics", [])),
            json.dumps(t.get("arxiv_categories", [])),
            int(t.get("lookback_days", 7)), int(bool(t.get("enabled", True))),
            t.get("kind", "topic"), json.dumps(t.get("author_ids", {})),
            json.dumps(t.get("profile", {})),
        ),
    )
    return cur.lastrowid


def list_topics(min_relevance: int = 0) -> list[dict]:
    """unread_count respects min_relevance so pill badges match what the feed shows."""
    with _lock:
        rows = conn().execute(
            """SELECT t.*,
                      (SELECT COUNT(*) FROM papers p WHERE p.topic_id = t.id) AS paper_count,
                      (SELECT COUNT(*) FROM papers p WHERE p.topic_id = t.id AND p.is_read = 0
                                                       AND p.relevance >= ?) AS unread_count
               FROM topics t ORDER BY t.id""",
            (min_relevance,),
        ).fetchall()
    return [_row(r, TOPIC_JSON) for r in rows]


def get_topic(topic_id: int) -> dict | None:
    with _lock:
        r = conn().execute("SELECT * FROM topics WHERE id = ?", (topic_id,)).fetchone()
    return _row(r, TOPIC_JSON)


def create_topic(t: dict) -> dict:
    with _lock:
        c = conn()
        tid = _insert_topic(c, t)
        c.commit()
    return get_topic(tid)


def update_topic(topic_id: int, t: dict) -> dict | None:
    fields, values = [], []
    for k in ("slug", "name", "description", "lookback_days", "enabled", "kind", *TOPIC_JSON):
        if k in t:
            v = t[k]
            if k in TOPIC_JSON:
                v = json.dumps(v)
            elif k == "enabled":
                v = int(bool(v))
            fields.append(f"{k} = ?")
            values.append(v)
    if fields:
        with _lock:
            c = conn()
            c.execute(f"UPDATE topics SET {', '.join(fields)} WHERE id = ?", (*values, topic_id))
            c.commit()
    return get_topic(topic_id)


def delete_topic(topic_id: int) -> None:
    with _lock:
        c = conn()
        c.execute("DELETE FROM topics WHERE id = ?", (topic_id,))
        c.commit()


# ---------------------------------------------------------------- papers

def known_keys(topic_id: int) -> set[str]:
    with _lock:
        rows = conn().execute(
            "SELECT paper_key FROM papers WHERE topic_id = ?", (topic_id,)
        ).fetchall()
    return {r[0] for r in rows}


def insert_paper(p: dict) -> int | None:
    with _lock:
        c = conn()
        cur = c.execute(
            """INSERT OR IGNORE INTO papers
               (paper_key, topic_id, title, authors, abstract, published, sources, url, pdf_url,
                code_url, venue, arxiv_id, doi, summary, summarized_by, relevance, sweep_id,
                discovered_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                p["paper_key"], p["topic_id"], p["title"], json.dumps(p.get("authors", [])),
                p.get("abstract", ""), p.get("published", ""), json.dumps(p.get("sources", [])),
                p.get("url", ""), p.get("pdf_url", ""), p.get("code_url", ""), p.get("venue", ""),
                p.get("arxiv_id", ""), p.get("doi", ""), json.dumps(p.get("summary", {})),
                p.get("summarized_by", ""), int(p.get("relevance", 0)), p.get("sweep_id"),
                now_iso(),
            ),
        )
        c.commit()
        return cur.lastrowid if cur.rowcount else None


def get_paper(paper_id: int) -> dict | None:
    with _lock:
        r = conn().execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
    return _row(r, PAPER_JSON)


def update_paper(paper_id: int, **fields) -> dict | None:
    allowed = {"starred", "is_read", "deep_dive", "summary", "relevance", "summarized_by"}
    sets, values = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "summary":
            v = json.dumps(v)
        elif k in ("starred", "is_read"):
            v = int(bool(v))
        sets.append(f"{k} = ?")
        values.append(v)
    if sets:
        with _lock:
            c = conn()
            c.execute(f"UPDATE papers SET {', '.join(sets)} WHERE id = ?", (*values, paper_id))
            c.commit()
    return get_paper(paper_id)


def mark_all_read(topic_id: int | None) -> None:
    with _lock:
        c = conn()
        if topic_id:
            c.execute("UPDATE papers SET is_read = 1 WHERE topic_id = ?", (topic_id,))
        else:
            c.execute("UPDATE papers SET is_read = 1")
        c.commit()


SORTS = {
    "relevance": "relevance DESC, published DESC",
    "newest": "published DESC, relevance DESC",
    "discovered": "discovered_at DESC, relevance DESC",
}


def query_papers(
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
) -> tuple[list[dict], int]:
    where, args = ["relevance >= ?"], [min_relevance]
    if topic_id:
        where.append("topic_id = ?")
        args.append(topic_id)
    if subtopic:
        # subtopics live inside the summary JSON
        where.append("EXISTS (SELECT 1 FROM json_each(summary, '$.subtopics') WHERE value = ?)")
        args.append(subtopic)
    if q:
        where.append("(title LIKE ? OR abstract LIKE ? OR authors LIKE ? OR summary LIKE ?)")
        args += [f"%{q}%"] * 4
    if starred:
        where.append("starred = 1")
    if unread:
        where.append("is_read = 0")
    if sweep_id:
        where.append("sweep_id = ?")
        args.append(sweep_id)
    clause = " AND ".join(where)
    order = SORTS.get(sort, SORTS["relevance"])
    with _lock:
        c = conn()
        total = c.execute(f"SELECT COUNT(*) FROM papers WHERE {clause}", args).fetchone()[0]
        rows = c.execute(
            f"SELECT * FROM papers WHERE {clause} ORDER BY {order} LIMIT ? OFFSET ?",
            (*args, limit, offset),
        ).fetchall()
    return [_row(r, PAPER_JSON) for r in rows], total


def counts(topic_id: int | None, min_relevance: int) -> dict:
    """Badge counts for the New / All / Starred tabs under the current topic + relevance filter."""
    where, args = "relevance >= ?", [min_relevance]
    if topic_id:
        where += " AND topic_id = ?"
        args.append(topic_id)
    with _lock:
        r = conn().execute(
            f"""SELECT COUNT(*), COALESCE(SUM(is_read = 0), 0), COALESCE(SUM(starred), 0)
                FROM papers WHERE {where}""",
            args,
        ).fetchone()
    return {"all": r[0], "new": r[1], "starred": r[2]}


# ---------------------------------------------------------------- sweeps

def start_sweep(trigger: str) -> int:
    with _lock:
        c = conn()
        cur = c.execute(
            "INSERT INTO sweeps (started_at, status, trigger) VALUES (?, 'running', ?)",
            (now_iso(), trigger),
        )
        c.commit()
        return cur.lastrowid


def finish_sweep(sweep_id: int, status: str, stats: dict, digest: str = "") -> None:
    with _lock:
        c = conn()
        c.execute(
            "UPDATE sweeps SET finished_at = ?, status = ?, stats = ?, digest = ? WHERE id = ?",
            (now_iso(), status, json.dumps(stats), digest, sweep_id),
        )
        c.commit()


def list_sweeps(limit: int = 20) -> list[dict]:
    with _lock:
        rows = conn().execute(
            "SELECT * FROM sweeps ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row(r, ("stats",)) for r in rows]


def get_sweep(sweep_id: int) -> dict | None:
    with _lock:
        r = conn().execute("SELECT * FROM sweeps WHERE id = ?", (sweep_id,)).fetchone()
    return _row(r, ("stats",))


# ---------------------------------------------------------------- settings

def get_setting(key: str, default=None):
    with _lock:
        r = conn().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(r[0]) if r else default


def set_setting(key: str, value) -> None:
    with _lock:
        c = conn()
        c.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                  "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, json.dumps(value)))
        c.commit()


# ---------------------------------------------------------------- planner records
# Library papers, conferences, ideas, projects, tasks and personal deadlines are plain records: these helpers
# handle JSON/bool columns and timestamps, and ignore keys that aren't columns.

RECORD_JSON = {
    "library": ("authors", "tags"),
    "conferences": ("tags", "deadlines"),
    "ideas": ("tags", "papers", "milestones", "notes", "meta"),
    "projects": ("notes",),
    "tasks": (),
    "deadlines": (),
}
RECORD_BOOL = ("starred", "missing", "pinned", "custom", "all_day", "done")
_cols: dict[str, set[str]] = {}


def _columns(table: str) -> set[str]:
    if table not in _cols:
        with _lock:
            _cols[table] = {r[1] for r in conn().execute(f"PRAGMA table_info({table})")}
    return _cols[table]


def _encode(table: str, d: dict) -> dict:
    cols = _columns(table)
    out = {}
    for k, v in d.items():
        if k not in cols or k == "id":
            continue
        if k in RECORD_JSON[table]:
            v = json.dumps(v)
        elif k in RECORD_BOOL:
            v = int(bool(v))
        out[k] = v
    return out


def get_record(table: str, rid: int) -> dict | None:
    with _lock:
        r = conn().execute(f"SELECT * FROM {table} WHERE id = ?", (rid,)).fetchone()
    return _row(r, RECORD_JSON[table])


def list_records(table: str, where: str = "", args: tuple | list = (), order: str = "id") -> list[dict]:
    sql = f"SELECT * FROM {table}" + (f" WHERE {where}" if where else "") + f" ORDER BY {order}"
    with _lock:
        rows = conn().execute(sql, tuple(args)).fetchall()
    return [_row(r, RECORD_JSON[table]) for r in rows]


def insert_record(table: str, d: dict) -> dict:
    now = now_iso()
    d = _encode(table, {"created_at": now, "added_at": now, "updated_at": now, **d})
    with _lock:
        c = conn()
        cur = c.execute(f"INSERT INTO {table} ({', '.join(d)}) VALUES ({', '.join('?' * len(d))})",
                        tuple(d.values()))
        c.commit()
    return get_record(table, cur.lastrowid)


def update_record(table: str, rid: int, d: dict) -> dict | None:
    d = _encode(table, {**d, "updated_at": now_iso()})
    with _lock:
        c = conn()
        c.execute(f"UPDATE {table} SET {', '.join(f'{k} = ?' for k in d)} WHERE id = ?",
                  (*d.values(), rid))
        c.commit()
    return get_record(table, rid)


def delete_record(table: str, rid: int) -> None:
    with _lock:
        c = conn()
        c.execute(f"DELETE FROM {table} WHERE id = ?", (rid,))
        c.commit()
