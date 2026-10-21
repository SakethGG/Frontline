"""Paper discovery across free scholarly APIs: arXiv, Semantic Scholar and OpenAlex.

Every fetcher returns a list of normalized paper dicts:
    title, authors, abstract, published (YYYY-MM-DD), url, pdf_url, code_url, venue,
    arxiv_id, doi, sources
"""

import asyncio
import hashlib
import html
import logging
import re
import unicodedata
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
    papers = [arxiv_paper(e) for e in _arxiv_entries(r.text)]
    return [p for p in papers if p["published"] and p["published"] >= since.isoformat()]


def arxiv_paper(e: ET.Element) -> dict:
    abs_url = e.findtext("a:id", "", ATOM)
    arxiv_id = re.sub(r"v\d+$", "", abs_url.rsplit("/abs/", 1)[-1])
    pdf_url = next(
        (l.get("href") for l in e.findall("a:link", ATOM) if l.get("title") == "pdf"),
        f"https://arxiv.org/pdf/{arxiv_id}",
    )
    abstract = _clean(e.findtext("a:summary", "", ATOM))
    comment = _clean(e.findtext("arxiv:comment", "", ATOM))
    return {
        "title": _clean(e.findtext("a:title", "", ATOM)),
        "authors": [_clean(a.findtext("a:name", "", ATOM)) for a in e.findall("a:author", ATOM)],
        "abstract": abstract,
        "published": (e.findtext("a:published", "", ATOM) or "")[:10],
        "url": f"https://arxiv.org/abs/{arxiv_id}",
        "pdf_url": pdf_url.replace("http://", "https://"),
        "code_url": _code_url(abstract, comment),
        "venue": comment if len(comment) < 120 else "arXiv",
        "arxiv_id": arxiv_id,
        "doi": e.findtext("arxiv:doi", "", ATOM) or "",
        "sources": ["arXiv"],
    }


async def lookup(client: httpx.AsyncClient, arxiv_id: str = "", doi: str = "") -> dict | None:
    """Canonical metadata for one paper by arXiv ID or DOI (None if neither resolves)."""
    try:
        if arxiv_id:
            r = await client.get("https://export.arxiv.org/api/query",
                                 params={"id_list": arxiv_id, "max_results": 1}, timeout=30)
            r.raise_for_status()
            entries = [e for e in _arxiv_entries(r.text) if e.findtext("a:title", "", ATOM).strip()]
            if entries:
                return arxiv_paper(entries[0])
        if doi:
            params = {"mailto": config.OPENALEX_MAILTO} if config.OPENALEX_MAILTO else {}
            r = await client.get(f"https://api.openalex.org/works/doi:{doi}", params=params, timeout=30)
            if r.status_code == 200:
                return openalex_work(r.json())
    except (httpx.HTTPError, ET.ParseError, ValueError) as e:
        log.info("Lookup failed for arxiv=%r doi=%r: %s", arxiv_id, doi, e)
    return None


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
                p["verified"] = i == 0  # listed under their own author ID
                papers.append(p)
    return papers


async def fetch_s2_author(client: httpx.AsyncClient, topic: dict, since: date) -> list[dict]:
    """Recent papers of Semantic Scholar authors with this name (only for Scholar-linked
    researchers, whose co-authors are used to drop namesakes afterwards)."""
    if not (topic.get("author_ids") or {}).get("scholar"):
        return []
    base = "https://api.semanticscholar.org/graph/v1"
    r = await s2_get(client, f"{base}/author/search", {"query": topic["name"], "fields": "name", "limit": 5})
    me = surname(topic["name"])
    ids = [a["authorId"] for a in r.json().get("data") or [] if surname(a.get("name", "")) == me][:3]
    papers = []
    for aid in ids:  # one at a time: the public API is rate limited
        r = await s2_get(client, f"{base}/author/{aid}/papers", {"fields": S2_PAPER_FIELDS, "limit": 500})
        papers += [s2_paper(d) for d in r.json().get("data") or []
                   if d.get("title") and (d.get("publicationDate") or "") >= since.isoformat()]
    return papers


# ---------------------------------------------------------------- Google Scholar
# Scholar has no API and its author search needs a sign-in, but public profile pages can be
# read. For a researcher followed by Scholar link, their profile is the final say: every
# recent paper on it is included, and papers found elsewhere must look like theirs.

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
ARXIV_ID_RE = re.compile(r"arXiv:(\d{4}\.\d{4,5})", re.I)


class ScholarUnavailable(Exception):
    """Scholar didn't return the profile (usually rate limiting or a sign-in wall)."""


def _ascii_words(name: str) -> list[str]:
    return re.findall(r"[a-z]+", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower())


def surname(name: str) -> str:
    words = _ascii_words(name)
    return words[-1] if words else ""


def author_key(name: str) -> str:
    """First initial + surname, so 'Alex Krizhevsky' and Scholar's 'A Krizhevsky' match."""
    words = _ascii_words(name)
    return f"{words[0][0]} {words[-1]}" if len(words) >= 2 else ""


def _strip_tags(s: str) -> str:
    return _clean(html.unescape(re.sub(r"<[^>]+>", "", s)))


async def scholar_page(client: httpx.AsyncClient, uid: str, sortby: str = "") -> dict:
    """A public Scholar profile: name, affiliation, interests, stats and up to 100 papers
    (most cited first, or newest first with sortby="pubdate")."""
    params = {"user": uid, "hl": "en", "pagesize": 100, **({"sortby": sortby} if sortby else {})}
    try:
        r = await client.get("https://scholar.google.com/citations", params=params, timeout=30,
                             headers={"User-Agent": BROWSER_UA, "Accept-Language": "en-US,en;q=0.9"})
    except httpx.HTTPError as e:
        raise ScholarUnavailable(f"Couldn't reach Google Scholar: {e}") from e
    name = re.search(r'id="gsc_prf_in">(.*?)</div>', r.text, re.S)
    if r.status_code != 200 or "accounts.google.com" in str(r.url) or not name:
        raise ScholarUnavailable("Google Scholar didn't return that profile (it may be rate-limiting). "
                                 "Try again in a few minutes.")
    affiliation = re.search(r'class="gsc_prf_il">(.*?)</div>', r.text, re.S)
    stats = [int(x) for x in re.findall(r'class="gsc_rsb_std">(\d+)<', r.text)]
    rows = []
    for row in re.findall(r'<tr class="gsc_a_tr">(.*?)</tr>', r.text, re.S):
        link = re.search(r'<a href="([^"]+)" class="gsc_a_at">(.*?)</a>', row, re.S)
        if not link:
            continue
        gray = re.findall(r'<div class="gs_gray">(.*?)</div>', row, re.S) + ["", ""]
        year = re.search(r'class="gsc_a_h gsc_a_hc[^"]*">(\d{4})<', row)
        rows.append({
            "title": _strip_tags(link.group(2)),
            "url": "https://scholar.google.com" + html.unescape(link.group(1)),
            "authors": [a.strip() for a in _strip_tags(gray[0]).split(",") if a.strip() not in ("", "...")],
            "venue": _strip_tags(re.sub(r'<span class="gs_oph">.*?</span>', "", gray[1], flags=re.S)),
            "year": int(year.group(1)) if year else 0,
        })
    photo = re.search(r'<img[^>]*id="gsc_prf_pup-img"[^>]*>', r.text)
    srcs = re.findall(r'(https://scholar\.googleusercontent\.com/citations\?[^"\s]+)', html.unescape(photo.group(0))) if photo else []
    return {
        "name": _strip_tags(name.group(1)),
        # largest version; profiles without a photo show Scholar's default avatar, which isn't on googleusercontent
        "photo": next((u for u in srcs if "medium_photo" in u), srcs[0] if srcs else ""),
        "affiliation": _strip_tags(affiliation.group(1)) if affiliation else "",
        "interests": [_strip_tags(i) for i in re.findall(r'class="gsc_prf_inta[^"]*"[^>]*>(.*?)</a>', r.text, re.S)],
        "citations": stats[0] if stats else 0,
        "h_index": stats[2] if len(stats) > 2 else 0,
        "rows": rows,
    }


def _scholar_paper(row: dict) -> dict:
    """A Scholar row as a paper. Abstract, exact date and PDF get merged in from the other
    sources when they find the same title (Scholar itself only gives the year)."""
    m = ARXIV_ID_RE.search(row["venue"])
    arxiv_id = m.group(1) if m else ""
    return {
        "title": row["title"],
        "authors": row["authors"],
        "abstract": "",
        "published": str(row["year"] or ""),
        "url": f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else row["url"],
        "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else "",
        "code_url": "",
        "venue": row["venue"],
        "arxiv_id": arxiv_id,
        "doi": "",
        "sources": ["Google Scholar"],
    }


async def fetch_scholar_author(client: httpx.AsyncClient, topic: dict, since: date) -> tuple[list[dict], set[str]]:
    """Recent papers on their Scholar profile, plus the co-authors it lists (to vet other sources)."""
    gs = await scholar_page(client, topic["author_ids"]["scholar"], sortby="pubdate")
    coauthors = {author_key(a) for row in gs["rows"] for a in row["authors"]} - {author_key(topic["name"]), ""}
    return [_scholar_paper(row) for row in gs["rows"] if row["year"] >= since.year], coauthors


def looks_like_theirs(topic: dict, p: dict, coauthors: set[str]) -> bool:
    """For a paper found outside their Scholar profile: their name is on it and it shares a
    co-author with their Scholar papers (or OpenAlex lists it under their author ID)."""
    if p.get("verified"):
        return True
    keys = {author_key(a) for a in p["authors"]}
    return author_key(topic["name"]) in keys and bool(keys & coauthors)


# ---------------------------------------------------------------- orchestration

FETCHERS = {
    "arXiv": fetch_arxiv,
    "Semantic Scholar": fetch_semantic_scholar,
    "OpenAlex": fetch_openalex,
}
AUTHOR_FETCHERS = {
    "arXiv": fetch_arxiv_author,
    "OpenAlex": fetch_openalex_author,
    "Semantic Scholar": fetch_s2_author,
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
    if len(into.get("published", "")) < len(other.get("published", "")):  # a full date beats Scholar's year
        into["published"] = other["published"]
    into["verified"] = into.get("verified") or other.get("verified")
    into["sources"] = sorted(set(into["sources"]) | set(other["sources"]))


def keyword_hits(topic: dict, p: dict) -> int:
    """How many of the topic's query phrases appear in title+abstract (all words of a phrase)."""
    text = f"{p['title']} {p.get('abstract', '')}".lower()
    hits = 0
    for q in topic["queries"]:
        words = re.findall(r"[a-z0-9]+", q.lower())
        # match word stems loosely so "network" also catches "networks"
        if words and all(re.search(rf"\b{re.escape(w[:max(4, len(w) - 2)])}", text) for w in words):
            hits += 1
    return hits


async def discover(topic: dict, since: date) -> tuple[list[dict], dict]:
    """Query every source concurrently, dedupe, and drop off-topic hits.

    Followed researchers are fetched by author ID and skip the keyword filter: everything
    they co-author counts (Claude's relevance score later flags misattributed papers).
    For one followed by Google Scholar link, every recent paper on their Scholar profile is
    kept, and papers found elsewhere must share a co-author with it.
    """
    stats: dict = {}
    researcher = is_researcher(topic)
    fetchers = AUTHOR_FETCHERS if researcher else FETCHERS
    scholar = researcher and bool((topic.get("author_ids") or {}).get("scholar"))
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
        names = list(fetchers)
        results = await asyncio.gather(
            *(fetchers[n](client, topic, since) for n in names),
            *([fetch_scholar_author(client, topic, since)] if scholar else []),
            return_exceptions=True,
        )
    coauthors: set[str] | None = None
    if scholar:
        gs = results[-1]
        if isinstance(gs, Exception):  # Scholar unreachable: fall back to the other sources as-is
            log.warning("Google Scholar failed for %s: %s", topic["name"], gs)
            stats["Google Scholar"] = f"error: {type(gs).__name__}"
        else:
            papers_gs, coauthors = gs
            stats["Google Scholar"] = len(papers_gs)
        # Google Scholar first so its list is the one others merge into
        names = ["Google Scholar", *names]
        results = [[] if isinstance(gs, Exception) else papers_gs, *results[:-1]]
        if coauthors is None:  # without Scholar's co-authors, Semantic Scholar namesakes can't be vetted
            results[names.index("Semantic Scholar")] = []
    by_key: dict[str, dict] = {}
    by_title: dict[str, str] = {}
    for name, res in zip(names, results):
        if isinstance(res, Exception):
            log.warning("%s failed for %s: %s", name, topic["name"], res)
            stats[name] = f"error: {type(res).__name__}"
            continue
        stats.setdefault(name, len(res))
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
        if coauthors is not None and "Google Scholar" not in p["sources"] and not looks_like_theirs(topic, p, coauthors):
            continue
        p["paper_key"] = key
        p["keyword_hits"] = 1 if researcher else keyword_hits(topic, p)
        if p["keyword_hits"] > 0:
            papers.append(p)
    papers.sort(key=lambda p: (p["published"], p["keyword_hits"]), reverse=True)
    stats["unique_on_topic"] = len(papers)
    return papers, stats
