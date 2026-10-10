"""Followed researchers: resolve a name to an author profile, and learn where their lab publishes.

A followed researcher is stored as a topic with kind="researcher". Its papers come from
sources.AUTHOR_FETCHERS, so the feed shows everything coming out of their lab.
"""

import asyncio
import collections
import logging
import re

import httpx

from . import config
from .sources import ATOM, USER_AGENT, _arxiv_entries, arxiv_author_query, normalize_title

log = logging.getLogger("frontline.researchers")

OPENALEX = "https://api.openalex.org"


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=30)


async def _oa_get(c: httpx.AsyncClient, path: str, params: dict) -> httpx.Response:
    """GET against OpenAlex, retrying its occasional 5xx/timeouts."""
    if config.OPENALEX_MAILTO:
        params = {**params, "mailto": config.OPENALEX_MAILTO}
    for attempt in range(3):
        try:
            r = await c.get(f"{OPENALEX}{path}", params=params)
            if r.status_code < 500:
                break
        except httpx.TimeoutException:
            if attempt == 2:
                raise
        await asyncio.sleep(1.5 * (attempt + 1))
    r.raise_for_status()
    return r


def _short_id(openalex_url: str) -> str:
    return (openalex_url or "").rsplit("/", 1)[-1]


def _profile(a: dict) -> dict:
    """The bits of an OpenAlex author record worth showing (and keeping)."""
    insts = a.get("last_known_institutions") or []
    stats = a.get("summary_stats") or {}
    return {
        "openalex_id": _short_id(a["id"]),
        "name": a.get("display_name", ""),
        "institution": ", ".join(i.get("display_name", "") for i in insts if i.get("display_name")),
        "institution_ids": [_short_id(i["id"]) for i in insts if i.get("id")],
        "works_count": a.get("works_count") or 0,
        "cited_by_count": a.get("cited_by_count") or 0,
        "h_index": stats.get("h_index") or 0,
        "areas": [t["display_name"] for t in (a.get("topics") or [])[:6]],
        "orcid": (a.get("orcid") or "").replace("https://orcid.org/", ""),
    }


async def search(name: str) -> list[dict]:
    """Candidate authors for a name, most prominent first, so the user can pick the right one."""
    async with _client() as c:
        r = await _oa_get(c, "/authors", {"search": name, "per-page": 10})
    out = [_profile(a) for a in r.json().get("results") or []]
    # OpenAlex splits some people into stub profiles with a handful of works; hide those
    return [p for p in out if p["works_count"] >= 3] or out


async def resolve(openalex_id: str) -> dict:
    """Full profile for one OpenAlex author ID (raises httpx.HTTPStatusError if unknown)."""
    if not re.fullmatch(r"A\d+", openalex_id):
        raise ValueError("Not an OpenAlex author ID")
    async with _client() as c:
        r = await _oa_get(c, f"/authors/{openalex_id}", {})
    return _profile(r.json())


async def learn_arxiv_categories(profile: dict) -> list[str]:
    """arXiv categories this person publishes in, from arXiv papers confirmed to be theirs.

    arXiv has no author IDs, so its feed is a name search. Restricting it to the categories
    of papers whose titles also appear in their OpenAlex record keeps namesakes in other
    fields out. Returns [] (search all of arXiv) when nothing could be confirmed.
    """
    try:
        async with _client() as c:
            mine, arx = await asyncio.gather(
                _oa_get(c, "/works", {"filter": f"author.id:{profile['openalex_id']}",
                                      "select": "title", "per-page": 200}),
                c.get("https://export.arxiv.org/api/query", timeout=60, params={
                    "search_query": arxiv_author_query(profile["name"], []), "max_results": 200}),
            )
        arx.raise_for_status()
    except httpx.HTTPError as e:
        log.info("Could not learn arXiv categories for %s: %s", profile["name"], e)
        return []
    titles = {normalize_title(w["title"]) for w in mine.json().get("results") or [] if w.get("title")}
    cats = collections.Counter()
    for e in _arxiv_entries(arx.text):
        if normalize_title(e.findtext("a:title", "", ATOM)) in titles:
            cats.update(c.get("term") for c in e.findall("a:category", ATOM) if "." in (c.get("term") or ""))
    # keep categories that show up on at least two of their papers (or the top one)
    return [c for c, n in cats.most_common(6) if n >= 2] or [c for c, _ in cats.most_common(1)]


def topic_fields(profile: dict, arxiv_categories: list[str]) -> dict:
    """Topic row for a followed researcher. Areas become subtopics, so cards can be filtered by them."""
    areas = profile["areas"]
    where = f" at {profile['institution']}" if profile["institution"] else ""
    return {
        "kind": "researcher",
        "name": profile["name"],
        "description": (
            f"Every paper from the lab of {profile['name']}{where}. "
            f"Their research areas: {', '.join(areas) or 'unknown'}."
        ),
        "queries": [],
        "subtopics": areas,
        "arxiv_categories": arxiv_categories,
        "author_ids": {"openalex": profile["openalex_id"], "institutions": profile["institution_ids"]},
        "profile": profile,
    }
