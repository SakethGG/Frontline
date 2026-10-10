"""Paper discovery across free scholarly APIs: arXiv, Semantic Scholar and OpenAlex.

Every fetcher returns a list of normalized paper dicts:
    title, authors, abstract, published (YYYY-MM-DD), url, pdf_url, code_url, venue,
    arxiv_id, doi, sources
"""

import asyncio
import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from datetime import date

import httpx

from . import config

log = logging.getLogger("frontline.sources")

USER_AGENT = "Frontline/0.1 (research paper monitor)"
CODE_RE = re.compile(r"https?://(?:www\.)?(?:github\.com|gitlab\.com|huggingface\.co)/[^\s)\]}>,;\"']+")
ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _code_url(*texts: str) -> str:
    for t in texts:
        m = CODE_RE.search(t or "")
        if m:
            return m.group(0).rstrip(".")
    return ""


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", title.lower())


def paper_key(p: dict) -> str:
    if p.get("arxiv_id"):
        return f"arxiv:{p['arxiv_id']}"
    if p.get("doi"):
        return f"doi:{p['doi'].lower()}"
    return "title:" + hashlib.sha1(normalize_title(p["title"]).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- arXiv

def _arxiv_query(topic: dict) -> str:
    terms = " OR ".join(f'all:"{q}"' if " " in q else f"all:{q}" for q in topic["queries"])
    query = f"({terms})"
    cats = topic.get("arxiv_categories") or []
    if cats:
        query += " AND (" + " OR ".join(f"cat:{c}" for c in cats) + ")"
    return query


async def fetch_arxiv(client: httpx.AsyncClient, topic: dict, since: date,
                      query: str | None = None) -> list[dict]:
    params = {
        "search_query": query or _arxiv_query(topic),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "start": 0,
        "max_results": 150,
    }
    r = await client.get("https://export.arxiv.org/api/query", params=params, timeout=60)
    r.raise_for_status()
    papers = []
    for e in _arxiv_entries(r.text):
        published = (e.findtext("a:published", "", ATOM) or "")[:10]
        if not published or published < since.isoformat():
            continue
        abs_url = e.findtext("a:id", "", ATOM)
        arxiv_id = re.sub(r"v\d+$", "", abs_url.rsplit("/abs/", 1)[-1])
        pdf_url = next(
            (l.get("href") for l in e.findall("a:link", ATOM) if l.get("title") == "pdf"),
            f"https://arxiv.org/pdf/{arxiv_id}",
        )
        abstract = _clean(e.findtext("a:summary", "", ATOM))
        comment = _clean(e.findtext("arxiv:comment", "", ATOM))
        papers.append({
            "title": _clean(e.findtext("a:title", "", ATOM)),
            "authors": [_clean(a.findtext("a:name", "", ATOM)) for a in e.findall("a:author", ATOM)],
            "abstract": abstract,
            "published": published,
            "url": f"https://arxiv.org/abs/{arxiv_id}",
            "pdf_url": pdf_url.replace("http://", "https://"),
            "code_url": _code_url(abstract, comment),
            "venue": comment if len(comment) < 120 else "arXiv",
            "arxiv_id": arxiv_id,
            "doi": e.findtext("arxiv:doi", "", ATOM) or "",
            "sources": ["arXiv"],
        })
    return papers


# ---------------------------------------------------------------- Semantic Scholar

def _boolean_query(queries: list[str], or_op: str) -> str:
    return f" {or_op} ".join(f'"{q}"' if " " in q else q for q in queries)


async def fetch_semantic_scholar(client: httpx.AsyncClient, topic: dict, since: date) -> list[dict]:
    params = {
        "query": _boolean_query(topic["queries"], "|"),
        "fields": S2_PAPER_FIELDS,
        "publicationDateOrYear": f"{since.isoformat()}:{date.today().isoformat()}",
        "sort": "publicationDate:desc",
    }
    r = await s2_get(client, "https://api.semanticscholar.org/graph/v1/paper/search/bulk", params)
    return [s2_paper(d) for d in (r.json().get("data") or [])[:150] if d.get("title")]


S2_PAPER_FIELDS = "title,abstract,authors,url,venue,publicationDate,externalIds,openAccessPdf"


async def s2_get(client: httpx.AsyncClient, url: str, params: dict) -> httpx.Response:
    """GET against Semantic Scholar, backing off on its shared public rate limit."""
    headers = {"x-api-key": config.SEMANTIC_SCHOLAR_API_KEY} if config.SEMANTIC_SCHOLAR_API_KEY else {}
    for attempt in range(3):
        r = await client.get(url, params=params, headers=headers, timeout=60)
        if r.status_code != 429:
            break
        await asyncio.sleep(3 * (attempt + 1))
    r.raise_for_status()
    return r


def s2_paper(d: dict) -> dict:
    ext = d.get("externalIds") or {}
    arxiv_id = ext.get("ArXiv") or ""
    abstract = _clean(d.get("abstract"))
    pdf = (d.get("openAccessPdf") or {}).get("url") or (
        f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else "")
    return {
        "title": _clean(d["title"]),
        "authors": [a.get("name", "") for a in d.get("authors") or []],
        "abstract": abstract,
        "published": d.get("publicationDate") or "",
        "url": f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else d.get("url", ""),
        "pdf_url": pdf,
        "code_url": _code_url(abstract),
        "venue": d.get("venue") or "",
        "arxiv_id": arxiv_id,
        "doi": ext.get("DOI") or "",
        "sources": ["Semantic Scholar"],
    }


# ---------------------------------------------------------------- OpenAlex

def _openalex_abstract(inv: dict | None) -> str:
    if not inv:
        return ""
    words = sorted((pos, w) for w, positions in inv.items() for pos in positions)
    return _clean(" ".join(w for _, w in words))


async def fetch_openalex(client: httpx.AsyncClient, topic: dict, since: date) -> list[dict]:
    params = {
        "search": _boolean_query(topic["queries"], "OR"),
        "filter": f"from_publication_date:{since.isoformat()},to_publication_date:{date.today().isoformat()}",
        "sort": "publication_date:desc",
        "per-page": 100,
    }
    if config.OPENALEX_MAILTO:
        params["mailto"] = config.OPENALEX_MAILTO
    r = await client.get("https://api.openalex.org/works", params=params, timeout=60)
    r.raise_for_status()
    return [p for p in map(openalex_work, r.json().get("results") or []) if p]


def openalex_work(w: dict) -> dict | None:
    title = _clean(w.get("title") or w.get("display_name"))
    if not title:
        return None
    ids = w.get("ids") or {}
    doi = (w.get("doi") or ids.get("doi") or "").replace("https://doi.org/", "")
    arxiv_id = ""
    m = re.search(r"10\.48550/arxiv\.(.+)", doi, re.I)
    if m:
        arxiv_id = m.group(1)
    loc = w.get("primary_location") or {}
    best = w.get("best_oa_location") or {}
    abstract = _openalex_abstract(w.get("abstract_inverted_index"))
    return {
        "title": title,
        "authors": [
            (a.get("author") or {}).get("display_name", "") for a in w.get("authorships") or []
        ],
        "abstract": abstract,
        "published": w.get("publication_date") or "",
        "url": (f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id
                else loc.get("landing_page_url") or w.get("doi") or w.get("id", "")),
        "pdf_url": best.get("pdf_url") or loc.get("pdf_url") or "",
        "code_url": _code_url(abstract),
        "venue": ((loc.get("source") or {}).get("display_name")) or "",
        "arxiv_id": arxiv_id,
        "doi": "" if arxiv_id else doi,
        "sources": ["OpenAlex"],
    }


# ---------------------------------------------------------------- followed researchers
# A followed researcher's "lab output" is every paper they co-author. Author databases split
# one person into several profiles, so OpenAlex is queried by author ID *and* by name at their
# institution, and arXiv (the freshest source) by name within the categories they publish in.

def name_key(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower())


def _arxiv_entries(xml_text: str) -> list[ET.Element]:
    return ET.fromstring(xml_text).findall("a:entry", ATOM)


def arxiv_author_query(name: str, categories: list[str]) -> str:
    query = f'au:"{name}"'
    if categories:
        query += " AND (" + " OR ".join(f"cat:{c}" for c in categories) + ")"
    return query


async def fetch_arxiv_author(client: httpx.AsyncClient, topic: dict, since: date) -> list[dict]:
    return await fetch_arxiv(client, topic, since,
                             query=arxiv_author_query(topic["name"], topic.get("arxiv_categories") or []))


async def fetch_openalex_author(client: httpx.AsyncClient, topic: dict, since: date) -> list[dict]:
    ids = topic.get("author_ids") or {}
    if not ids.get("openalex"):
        return []
    window = f"from_publication_date:{since.isoformat()}"
    base = {"sort": "publication_date:desc", "per-page": 200}
    if config.OPENALEX_MAILTO:
        base["mailto"] = config.OPENALEX_MAILTO
    queries = [{**base, "filter": f"author.id:{ids['openalex']},{window}"}]
    insts = ids.get("institutions") or []
    if insts:
        queries.append({**base, "filter": (f"raw_author_name.search:{topic['name']},"
                                           f"authorships.institutions.lineage:{'|'.join(insts)},{window}")})
    results = await asyncio.gather(
        *(client.get("https://api.openalex.org/works", params=q, timeout=60) for q in queries))
    me, inst_urls = name_key(topic["name"]), {f"https://openalex.org/{i}" for i in insts}
    papers, seen = [], set()
    for i, r in enumerate(results):
        r.raise_for_status()
        for w in r.json().get("results") or []:
            if w["id"] in seen:
                continue
            if i > 0:  # name search: the same-named author must be the one at their institution
                if not any(
                    name_key(a.get("raw_author_name") or (a.get("author") or {}).get("display_name", "")) == me
                    and inst_urls & {x for inst in a.get("institutions") or []
                                     for x in [inst.get("id"), *(inst.get("lineage") or [])]}
                    for a in w.get("authorships") or []
                ):
                    continue
            seen.add(w["id"])
            if p := openalex_work(w):
                papers.append(p)
    return papers


# ---------------------------------------------------------------- orchestration

FETCHERS = {
    "arXiv": fetch_arxiv,
    "Semantic Scholar": fetch_semantic_scholar,
    "OpenAlex": fetch_openalex,
}
AUTHOR_FETCHERS = {
    "arXiv": fetch_arxiv_author,
    "OpenAlex": fetch_openalex_author,
}


def is_researcher(topic: dict) -> bool:
    return topic.get("kind") == "researcher"


def _merge(into: dict, other: dict) -> None:
    for k in ("abstract", "pdf_url", "code_url", "venue", "arxiv_id", "doi"):
        if not into.get(k) and other.get(k):
            into[k] = other[k]
    if len(other.get("abstract", "")) > len(into.get("abstract", "")):
        into["abstract"] = other["abstract"]
    if into.get("venue") in ("", "arXiv") and other.get("venue"):
        into["venue"] = other["venue"]
    into["sources"] = sorted(set(into["sources"]) | set(other["sources"]))


def keyword_hits(topic: dict, p: dict) -> int:
    """How many of the topic's query phrases appear in title+abstract (all words of a phrase)."""
    text = f"{p['title']} {p.get('abstract', '')}".lower()
    hits = 0
    for q in topic["queries"]:
        words = re.findall(r"[a-z0-9]+", q.lower())
        # match word stems loosely so "quadruped" also catches "quadrupedal"
        if words and all(re.search(rf"\b{re.escape(w[:max(4, len(w) - 2)])}", text) for w in words):
            hits += 1
    return hits


async def discover(topic: dict, since: date) -> tuple[list[dict], dict]:
    """Query every source concurrently, dedupe, and drop off-topic hits.

    Followed researchers are fetched by author ID and skip the keyword filter: everything
    they co-author counts (Claude's relevance score later flags misattributed papers).
    """
    stats: dict = {}
    researcher = is_researcher(topic)
    fetchers = AUTHOR_FETCHERS if researcher else FETCHERS
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
        names = list(fetchers)
        results = await asyncio.gather(
            *(fetchers[n](client, topic, since) for n in names), return_exceptions=True
        )
    by_key: dict[str, dict] = {}
    by_title: dict[str, str] = {}
    for name, res in zip(names, results):
        if isinstance(res, Exception):
            log.warning("%s failed for %s: %s", name, topic["name"], res)
            stats[name] = f"error: {type(res).__name__}"
            continue
        stats[name] = len(res)
        for p in res:
            nt = normalize_title(p["title"])
            key = by_title.get(nt) or paper_key(p)
            if key in by_key:
                _merge(by_key[key], p)
            else:
                by_key[key] = p
                by_title[nt] = key
    papers = []
    for key, p in by_key.items():
        p["paper_key"] = key
        p["keyword_hits"] = 1 if researcher else keyword_hits(topic, p)
        if p["keyword_hits"] > 0:
            papers.append(p)
    papers.sort(key=lambda p: (p["published"], p["keyword_hits"]), reverse=True)
    stats["unique_on_topic"] = len(papers)
    return papers, stats
