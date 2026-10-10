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
"""

# Columns added after the first release; init() adds any that an older database is missing.
# kind is "topic" (keyword search) or "researcher" (a followed person, fetched by author ID).
MIGRATIONS = {
    "topics": {
        "kind": "TEXT NOT NULL DEFAULT 'topic'",
        "author_ids": "TEXT NOT NULL DEFAULT '{}'",
        "profile": "TEXT NOT NULL DEFAULT '{}'",
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
