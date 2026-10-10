"""Claude-powered paper cards, deep dives, Q&A and sweep digests (with a no-LLM fallback)."""

import base64
import json
import logging
import re

import anthropic
import httpx

from . import config

log = logging.getLogger("frontline.summarizer")

# Server-side refusal fallback: if the model declines, the API re-runs the request on
# Anthropic's recommended fallback model inside the same call.
FALLBACK_KW = {
    "extra_headers": {"anthropic-beta": "server-side-fallback-2026-07-01"},
    "extra_body": {"fallbacks": "default"},
}

PAPER_TYPES = ["method", "system", "benchmark/dataset", "survey", "theory", "hardware", "application"]

CARD_SYSTEM = """You are a sharp research analyst helping a researcher stay on top of new papers in their field. \
For each paper you produce a compact briefing card. Be concrete and specific: name the actual technique, \
robot/platform, benchmark and numbers when the abstract gives them. Never invent results that are not \
in the provided text. If the abstract is missing or thin, say so briefly instead of guessing.

Relevance scoring (0-10) is about how useful the paper is to someone following the stated research topic:
10 = core contribution squarely in the topic; 7-9 = clearly in-topic; 4-6 = adjacent or applies topic \
ideas tangentially; 1-3 = mentions topic keywords but is about something else; 0 = unrelated."""


class Refused(Exception):
    pass


class Summarizer:
    def __init__(self) -> None:
        self.client: anthropic.AsyncAnthropic | None = None
        self.disabled_reason = ""
        try:
            self.client = anthropic.AsyncAnthropic()
        except Exception as e:  # no credentials resolvable at construction time
            self.disabled_reason = str(e)

    async def probe(self) -> None:
        """Cheap credential check (lists one model, no tokens) so the UI shows the real state."""
        if not self.enabled:
            return
        try:
            await self.client.models.list(limit=1)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, TypeError) as e:
            self._disable(str(e))
        except anthropic.APIError as e:
            log.info("Claude probe inconclusive: %s", e)

    @property
    def enabled(self) -> bool:
        return self.client is not None and not self.disabled_reason

    def _disable(self, reason: str) -> None:
        log.warning("Claude disabled, using fallback summaries: %s", reason)
        self.disabled_reason = reason

    # ------------------------------------------------------------ cards

    def _card_schema(self, subtopics: list[str]) -> dict:
        str_list = {"type": "array", "items": {"type": "string"}}
        return {
            "type": "object",
            "properties": {
                "tldr": {"type": "string", "description": "One sentence, at most 30 words."},
                "key_points": {**str_list, "description": "3-5 crisp bullet points, the most important takeaways."},
                "problem": {"type": "string"},
                "method": {"type": "string"},
                "results": {"type": "string"},
                "why_it_matters": {"type": "string"},
                "limitations": {"type": "string"},
                "subtopics": {
                    "type": "array",
                    "items": {"type": "string", "enum": [*subtopics, "Other"]},
                },
                "paper_type": {"type": "string", "enum": PAPER_TYPES},
                "relevance": {"type": "integer", "description": "0-10, see rubric."},
                "relevance_reason": {"type": "string", "description": "One short sentence."},
            },
            "required": [
                "tldr", "key_points", "problem", "method", "results", "why_it_matters",
                "limitations", "subtopics", "paper_type", "relevance", "relevance_reason",
            ],
            "additionalProperties": False,
        }

    async def card(self, topic: dict, paper: dict) -> tuple[dict, str]:
        """Return (summary, summarized_by)."""
        if self.enabled:
            try:
                return await self._claude_card(topic, paper), config.MODEL
            except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
                self._disable(f"{type(e).__name__}: {e}")
            except TypeError as e:  # SDK raises this when no credentials could be resolved
                if "auth" in str(e).lower() or "api_key" in str(e).lower():
                    self._disable(str(e))
                else:
                    raise
            except (anthropic.APIError, Refused, ValueError) as e:
                log.warning("Claude card failed for %r: %s", paper["title"][:60], e)
        return fallback_card(topic, paper), "fallback"

    async def _claude_card(self, topic: dict, paper: dict) -> dict:
        subtopics = topic.get("subtopics") or []
        if topic.get("kind") == "researcher":
            # Score fit with the lab's known work: this is what exposes papers that author
            # databases misattributed to a namesake.
            context = (
                f"<followed_researcher>\nName: {topic['name']}\n{topic.get('description', '')}\n"
                f"Subtopics: {', '.join(subtopics) or '(none)'}\n</followed_researcher>\n\n"
                "The reader follows this researcher's lab. For relevance, score how well the paper "
                "fits this lab's research areas: 7-10 if it is clearly their kind of work, 0-2 if it "
                "looks like a different person with the same name (unrelated field).\n\n"
            )
        else:
            context = (
                f"<research_topic>\nName: {topic['name']}\nDescription: {topic.get('description', '')}\n"
                f"Subtopics: {', '.join(subtopics) or '(none)'}\n</research_topic>\n\n"
            )
        prompt = (
            context +
            f"<paper>\nTitle: {paper['title']}\n"
            f"Authors: {', '.join(paper.get('authors', [])[:12])}\n"
            f"Published: {paper.get('published', '')}   Venue: {paper.get('venue', '')}\n"
            f"Abstract: {paper.get('abstract') or '(no abstract available)'}\n</paper>\n\n"
            "Write the briefing card. Tag only the subtopics that genuinely apply."
        )
        resp = await self.client.messages.create(
            model=config.MODEL,
            max_tokens=8000,
            system=CARD_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_config={
                "effort": config.SUMMARY_EFFORT,
                "format": {"type": "json_schema", "schema": self._card_schema(subtopics)},
            },
            **FALLBACK_KW,
        )
        if resp.stop_reason == "refusal":
            raise Refused(getattr(resp.stop_details, "category", None))
        text = next(b.text for b in resp.content if b.type == "text")
        data = json.loads(text)
        data["relevance"] = max(0, min(10, int(data.get("relevance", 0))))
        return data

    # ------------------------------------------------------------ deep dive & Q&A

    async def _paper_content(self, paper: dict) -> list[dict]:
        """PDF as a document block when we can fetch it, else the abstract as text."""
        if paper.get("pdf_url"):
            try:
                async with httpx.AsyncClient(follow_redirects=True, timeout=60) as c:
                    r = await c.get(paper["pdf_url"], headers={"User-Agent": "Frontline/0.1"})
                if (r.status_code == 200 and r.content[:4] == b"%PDF"
                        and len(r.content) < 30 * 1024 * 1024):
                    return [{
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": base64.standard_b64encode(r.content).decode(),
                        },
                        "title": paper["title"],
                        "cache_control": {"type": "ephemeral"},
                    }]
            except httpx.HTTPError as e:
                log.info("PDF fetch failed (%s), using abstract", e)
        return [{
            "type": "text",
            "text": f"Title: {paper['title']}\nAuthors: {', '.join(paper.get('authors', []))}\n"
                    f"Abstract (full text unavailable): {paper.get('abstract', '')}",
        }]

    async def _stream_text(self, *, system: str, messages: list, effort: str) -> str:
        async with self.client.messages.stream(
            model=config.MODEL,
            max_tokens=32000,
            system=system,
            messages=messages,
            output_config={"effort": effort},
            **FALLBACK_KW,
        ) as stream:
            msg = await stream.get_final_message()
        if msg.stop_reason == "refusal":
            return "_Claude declined to answer this request._"
        return "".join(b.text for b in msg.content if b.type == "text").strip()

    async def deep_dive(self, topic: dict, paper: dict) -> str:
        if not self.enabled:
            return "_Deep dives need Claude credentials — set `ANTHROPIC_API_KEY` in `.env` and restart._"
        content = await self._paper_content(paper)
        content.append({"type": "text", "text": (
            f"I follow research on **{topic['name']}** ({topic.get('description', '')}).\n\n"
            "Give me a deep-dive briefing on this paper in Markdown with these sections:\n"
            "## Core idea\n## What's new vs. prior work\n## Method (with the key equations or "
            "architecture in words)\n## Experimental setup (platforms, sim/real, baselines)\n"
            "## Results (with the actual numbers)\n## Limitations & open questions\n"
            "## Ideas worth stealing\n## Verdict — should I read the full paper? (one line)\n\n"
            "Be specific and dense; skip filler. If you only have the abstract, say so at the top."
        )})
        return await self._stream_text(
            system="You are an expert research analyst writing for a researcher in the field.",
            messages=[{"role": "user", "content": content}],
            effort=config.DEEP_DIVE_EFFORT,
        )

    async def ask(self, paper: dict, question: str, history: list[dict]) -> str:
        if not self.enabled:
            return "_Q&A needs Claude credentials — set `ANTHROPIC_API_KEY` in `.env` and restart._"
        content = await self._paper_content(paper)
        content.append({"type": "text", "text": "This is the paper we'll discuss."})
        messages = [
            {"role": "user", "content": content},
            {"role": "assistant", "content": "Got it — ask me anything about this paper."},
        ]
        for turn in history[-8:]:
            if turn.get("q") and turn.get("a"):
                messages += [{"role": "user", "content": turn["q"]},
                             {"role": "assistant", "content": turn["a"]}]
        messages.append({"role": "user", "content": question})
        return await self._stream_text(
            system="Answer questions about the provided paper precisely and concisely, citing "
                   "sections or numbers from it. Say clearly when the paper doesn't cover something.",
            messages=messages,
            effort="medium",
        )

    # ------------------------------------------------------------ sweep digest

    async def digest(self, items: list[tuple[dict, dict]]) -> str:
        """items: (topic, paper-with-summary). Returns a Markdown briefing across the sweep."""
        if not items:
            return ""
        if not self.enabled:
            return fallback_digest(items)
        lines = []
        for topic, p in items[:80]:
            s = p["summary"]
            lines.append(
                f"- [{topic['name']}] (rel {p['relevance']}/10) **{p['title']}** — {s.get('tldr', '')} "
                f"Subtopics: {', '.join(s.get('subtopics', []))}"
            )
        try:
            resp = await self.client.messages.create(
                model=config.MODEL,
                max_tokens=16000,
                system="You write tight, insightful research digests for busy researchers.",
                messages=[{"role": "user", "content": (
                    "Here are the new papers found in today's sweep:\n\n" + "\n".join(lines) +
                    "\n\nWrite a Markdown digest (max ~300 words) with:\n"
                    "### Headlines — the 3 must-read papers and why (one line each, use the exact titles)\n"
                    "### Themes — 2-4 trends or clusters you see across these papers\n"
                    "### Worth watching — anything surprising, contrarian or a new direction\n"
                    "No preamble."
                )}],
                output_config={"effort": "medium"},
                **FALLBACK_KW,
            )
            if resp.stop_reason != "refusal":
                return "".join(b.text for b in resp.content if b.type == "text").strip()
        except anthropic.APIError as e:
            log.warning("digest failed: %s", e)
        return fallback_digest(items)


# ---------------------------------------------------------------- fallback (no LLM)

def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", text) if len(s.strip()) > 20]


def fallback_card(topic: dict, paper: dict) -> dict:
    sents = _sentences(paper.get("abstract", ""))
    text = f"{paper['title']} {paper.get('abstract', '')}".lower()
    subtopics = [
        st for st in topic.get("subtopics", [])
        if any(len(w) > 3 and re.search(rf"\b{re.escape(w[:6])}", text)
               for w in re.findall(r"[a-z][a-z-]*", st.lower()))
    ]
    hits = paper.get("keyword_hits", 1)
    title = paper["title"].lower()
    in_title = any(q.lower().split()[0][:6] in title for q in topic.get("queries", []) if q.strip())
    if topic.get("kind") == "researcher":
        # matched by author, not keywords: show it, with a neutral score
        return {
            "tldr": sents[0] if sents else paper["title"],
            "key_points": sents[1:5],
            "problem": "", "method": "", "results": "", "why_it_matters": "", "limitations": "",
            "subtopics": subtopics,
            "paper_type": "method",
            "relevance": 6,
            "relevance_reason": f"From {topic['name']}'s lab; add Claude credentials for AI summaries.",
        }
    return {
        "tldr": sents[0] if sents else paper["title"],
        "key_points": sents[1:5],
        "problem": "", "method": "", "results": "", "why_it_matters": "", "limitations": "",
        "subtopics": subtopics,
        "paper_type": "method",
        "relevance": min(10, 1 + 2 * hits + (3 if in_title else 0) + (1 if paper.get("arxiv_id") else 0)),
        "relevance_reason": f"Keyword match ({hits} topic phrase{'s' if hits != 1 else ''}); "
                            "add Claude credentials for AI summaries.",
    }


def fallback_digest(items: list[tuple[dict, dict]]) -> str:
    top = sorted(items, key=lambda x: x[1]["relevance"], reverse=True)[:5]
    return "### Top new papers\n" + "\n".join(
        f"- **{p['title']}** ({t['name']}) — {p['summary'].get('tldr', '')}" for t, p in top
    )


summarizer = Summarizer()
