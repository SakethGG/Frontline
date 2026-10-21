"""AI paper cards, deep dives, Q&A and sweep digests via Claude or OpenAI (with a no-LLM fallback).

FRONTLINE_LLM picks the provider: "claude", "openai", or "auto" (OpenAI when only an OpenAI key
is set, otherwise Claude).
"""

import asyncio
import base64
import json
import logging
import re
from pathlib import Path

import anthropic
import httpx
import openai

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
platform/dataset, benchmark and numbers when the abstract gives them. Never invent results that are not \
in the provided text. If the abstract is missing or thin, say so briefly instead of guessing.

Relevance scoring (0-10) is about how useful the paper is to someone following the stated research topic:
10 = core contribution squarely in the topic; 7-9 = clearly in-topic; 4-6 = adjacent or applies topic \
ideas tangentially; 1-3 = mentions topic keywords but is about something else; 0 = unrelated."""

AUTH_ERRORS = (anthropic.AuthenticationError, anthropic.PermissionDeniedError,
               openai.AuthenticationError, openai.PermissionDeniedError)
API_ERRORS = (anthropic.APIError, openai.APIError)


class Refused(Exception):
    pass


def explain(e: Exception) -> str:
    """A readable reason for a failed AI call (no-credit, rate limit, bad key...)."""
    body = getattr(e, "body", None)
    err = body.get("error", body) if isinstance(body, dict) else None
    detail = err.get("message") if isinstance(err, dict) else None
    status = getattr(e, "status_code", None)
    if isinstance(err, dict) and err.get("type") == "insufficient_quota":
        return f"Your OpenAI account is out of credits. {detail or ''}".strip()
    if status == 429:
        return f"The AI provider is rate-limiting requests; wait a minute and try again. {detail or ''}".strip()
    if isinstance(e, AUTH_ERRORS):
        return f"The AI key was rejected; check it in .env. {detail or ''}".strip()
    return f"The AI request failed: {detail or e}"


def _provider() -> str:
    if config.LLM_PROVIDER in ("claude", "openai"):
        return config.LLM_PROVIDER
    return "openai" if config.OPENAI_API_KEY and not config.ANTHROPIC_API_KEY else "claude"


def _refused(resp) -> bool:
    """Whether an OpenAI Responses API reply is a refusal."""
    return any(c.type == "refusal" for o in resp.output if o.type == "message" for c in o.content)


class Summarizer:
    def __init__(self) -> None:
        self.provider = _provider()
        self.name = "OpenAI" if self.provider == "openai" else "Claude"
        self.model = config.OPENAI_MODEL if self.provider == "openai" else config.MODEL
        self.client = None
        self.disabled_reason = ""
        try:
            if self.provider == "openai":
                if not config.OPENAI_API_KEY:
                    raise ValueError("OPENAI_API_KEY is not set")
                self.client = openai.AsyncOpenAI(api_key=config.OPENAI_API_KEY)
            else:
                self.client = anthropic.AsyncAnthropic()
        except Exception as e:  # no credentials resolvable at construction time
            self.disabled_reason = str(e)

    async def probe(self) -> None:
        """Cheap credential check (no tokens) so the UI shows the real state."""
        if not self.enabled:
            return
        try:
            if self.provider == "openai":
                await self.client.models.retrieve(self.model)
            else:
                await self.client.models.list(limit=1)
        except (*AUTH_ERRORS, TypeError) as e:
            self._disable(str(e))
        except openai.NotFoundError:
            self._disable(f"OpenAI model {self.model!r} not found; set OPENAI_MODEL in .env")
        except API_ERRORS as e:
            log.info("%s probe inconclusive: %s", self.name, e)

    @property
    def enabled(self) -> bool:
        return self.client is not None and not self.disabled_reason

    def _disable(self, reason: str) -> None:
        log.warning("%s disabled, using fallback summaries: %s", self.name, reason)
        self.disabled_reason = reason

    # ------------------------------------------------------------ provider calls
    # Paper content travels as provider-neutral parts: {"text": ...} or {"pdf": base64, "title": ...}.

    def _openai_reasoning(self, effort: str) -> dict:
        # only reasoning models (gpt-5*, o-series) accept an effort setting
        return {"reasoning": {"effort": effort}} if re.match(r"(gpt-5|o\d)", self.model) else {}

    @staticmethod
    def _openai_content(parts: list[dict]) -> list[dict]:
        return [
            {"type": "input_file", "filename": "paper.pdf", "file_data": f"data:application/pdf;base64,{p['pdf']}"}
            if "pdf" in p else {"type": "input_text", "text": p["text"]}
            for p in parts
        ]

    @staticmethod
    def _claude_content(parts: list[dict]) -> list[dict]:
        return [
            {"type": "document", "title": p["title"], "cache_control": {"type": "ephemeral"},
             "source": {"type": "base64", "media_type": "application/pdf", "data": p["pdf"]}}
            if "pdf" in p else {"type": "text", "text": p["text"]}
            for p in parts
        ]

    async def _json(self, *, system: str, prompt: str, schema: dict, effort: str) -> dict:
        """One structured-output call; raises Refused if the model declines."""
        if self.provider == "openai":
            resp = await self.client.responses.create(
                model=self.model, instructions=system, input=prompt, max_output_tokens=8000,
                text={"format": {"type": "json_schema", "name": "card", "schema": schema, "strict": True}},
                **self._openai_reasoning(effort),
            )
            if _refused(resp):
                raise Refused("openai")
            return json.loads(resp.output_text)
        resp = await self.client.messages.create(
            model=self.model, max_tokens=8000, system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            **FALLBACK_KW,
        )
        if resp.stop_reason == "refusal":
            raise Refused(getattr(resp.stop_details, "category", None))
        return json.loads(next(b.text for b in resp.content if b.type == "text"))

    async def _text(self, *, system: str, messages: list[dict], effort: str, max_tokens: int = 32000) -> str:
        """Free-text answer. messages: [{"role", "content": str or neutral parts}]."""
        if self.provider == "openai":
            resp = await self.client.responses.create(
                model=self.model, instructions=system, max_output_tokens=max_tokens,
                input=[{"role": m["role"], "content": m["content"] if isinstance(m["content"], str)
                        else self._openai_content(m["content"])} for m in messages],
                **self._openai_reasoning(effort),
            )
            if _refused(resp):
                return f"_{self.name} declined to answer this request._"
            return resp.output_text.strip()
        async with self.client.messages.stream(
            model=self.model, max_tokens=max_tokens, system=system,
            messages=[{"role": m["role"], "content": m["content"] if isinstance(m["content"], str)
                       else self._claude_content(m["content"])} for m in messages],
            output_config={"effort": effort},
            **FALLBACK_KW,
        ) as stream:
            msg = await stream.get_final_message()
        if msg.stop_reason == "refusal":
            return f"_{self.name} declined to answer this request._"
        return "".join(b.text for b in msg.content if b.type == "text").strip()

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
                return await self._ai_card(topic, paper), self.model
            except AUTH_ERRORS as e:
                self._disable(f"{type(e).__name__}: {e}")
            except TypeError as e:  # SDK raises this when no credentials could be resolved
                if "auth" in str(e).lower() or "api_key" in str(e).lower():
                    self._disable(str(e))
                else:
                    raise
            except (*API_ERRORS, Refused, ValueError) as e:
                log.warning("%s card failed for %r: %s", self.name, paper["title"][:60], e)
                if "insufficient_quota" in str(getattr(e, "body", "")):  # no credits: stop retrying every card
                    self._disable(explain(e))
        return fallback_card(topic, paper), "fallback"

    async def _ai_card(self, topic: dict, paper: dict) -> dict:
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
            if "Google Scholar" in paper.get("sources", []):
                context += ("This paper is listed on the researcher's own Google Scholar profile, so it "
                            "is theirs: do not treat it as a namesake's.\n\n")
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
        data = await self._json(system=CARD_SYSTEM, prompt=prompt, schema=self._card_schema(subtopics),
                                effort=config.SUMMARY_EFFORT)
        data["relevance"] = max(0, min(10, int(data.get("relevance", 0))))
        return data

    # ------------------------------------------------------------ deep dive & Q&A

    async def _paper_content(self, paper: dict) -> list[dict]:
        """The PDF when we can fetch it, else the abstract, as provider-neutral parts."""
        if paper.get("pdf_path"):  # a library paper: the PDF is on disk
            try:
                data = await asyncio.to_thread(Path(paper["pdf_path"]).read_bytes)
                if data[:4] == b"%PDF" and len(data) < 30 * 1024 * 1024:
                    return [{"pdf": base64.standard_b64encode(data).decode(), "title": paper["title"]}]
            except OSError as e:
                log.info("Could not read %s (%s), using abstract", paper["pdf_path"], e)
        elif paper.get("pdf_url"):
            try:
                async with httpx.AsyncClient(follow_redirects=True, timeout=60) as c:
                    r = await c.get(paper["pdf_url"], headers={"User-Agent": "Frontline/0.1"})
                if (r.status_code == 200 and r.content[:4] == b"%PDF"
                        and len(r.content) < 30 * 1024 * 1024):
                    return [{"pdf": base64.standard_b64encode(r.content).decode(), "title": paper["title"]}]
            except httpx.HTTPError as e:
                log.info("PDF fetch failed (%s), using abstract", e)
        return [{
            "text": f"Title: {paper['title']}\nAuthors: {', '.join(paper.get('authors', []))}\n"
                    f"Abstract (full text unavailable): {paper.get('abstract', '')}",
        }]

    async def deep_dive(self, topic: dict, paper: dict) -> str:
        if not self.enabled:
            return f"_Deep dives need an AI key: set `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` in `.env` and restart ({self.disabled_reason or 'no key'})._"
        content = await self._paper_content(paper)
        content.append({"text": (
            f"I follow research on **{topic['name']}** ({topic.get('description', '')}).\n\n"
            "Give me a deep-dive briefing on this paper in Markdown with these sections:\n"
            "## Core idea\n## What's new vs. prior work\n## Method (with the key equations or "
            "architecture in words)\n## Experimental setup (platforms, sim/real, baselines)\n"
            "## Results (with the actual numbers)\n## Limitations & open questions\n"
            "## Ideas worth stealing\n## Verdict — should I read the full paper? (one line)\n\n"
            "Be specific and dense; skip filler. If you only have the abstract, say so at the top."
        )})
        return await self._text(
            system="You are an expert research analyst writing for a researcher in the field.",
            messages=[{"role": "user", "content": content}],
            effort=config.DEEP_DIVE_EFFORT,
        )

    async def ask(self, paper: dict, question: str, history: list[dict]) -> str:
        if not self.enabled:
            return f"_Q&A needs an AI key: set `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` in `.env` and restart ({self.disabled_reason or 'no key'})._"
        content = await self._paper_content(paper)
        content.append({"text": "This is the paper we'll discuss."})
        messages = [
            {"role": "user", "content": content},
            {"role": "assistant", "content": "Got it — ask me anything about this paper."},
        ]
        for turn in history[-8:]:
            if turn.get("q") and turn.get("a"):
                messages += [{"role": "user", "content": turn["q"]},
                             {"role": "assistant", "content": turn["a"]}]
        messages.append({"role": "user", "content": question})
        return await self._text(
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
            return await self._text(
                system="You write tight, insightful research digests for busy researchers.",
                messages=[{"role": "user", "content": (
                    "Here are the new papers found in today's sweep:\n\n" + "\n".join(lines) +
                    "\n\nWrite a Markdown digest (max ~300 words) with:\n"
                    "### Headlines — the 3 must-read papers and why (one line each, use the exact titles)\n"
                    "### Themes — 2-4 trends or clusters you see across these papers\n"
                    "### Worth watching — anything surprising, contrarian or a new direction\n"
                    "No preamble."
                )}],
                effort="medium", max_tokens=16000,
            )
        except API_ERRORS as e:
            log.warning("digest failed: %s", e)
        return fallback_digest(items)

    # ------------------------------------------------------------ library

    async def library_card(self, interests: list[str], paper: dict) -> dict | None:
        """Title/authors/year, a TL;DR and interest tags for a PDF in the library, read from its
        first pages. None when the AI is off or the call fails (callers fall back to keywords)."""
        if not self.enabled:
            return None
        schema = {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "The paper's real title, properly capitalized."},
                "authors": {"type": "array", "items": {"type": "string"}},
                "year": {"type": "string", "description": "Four-digit year, or empty if unknown."},
                "tldr": {"type": "string", "description": "One sentence, at most 30 words."},
                "tags": {"type": "array", "items": {"type": "string", "enum": [*interests, "Other"]}},
                "relevance": {"type": "integer", "description": "0-10: how useful to someone with these interests."},
            },
            "required": ["title", "authors", "year", "tldr", "tags", "relevance"],
            "additionalProperties": False,
        }
        prompt = (
            f"<reader_interests>\n{chr(10).join('- ' + i for i in interests) or '(none given)'}\n</reader_interests>\n\n"
            f"<known_metadata>\nFile: {paper.get('rel_path', '')}\nTitle guess: {paper.get('title', '')}\n"
            f"Authors guess: {', '.join(paper.get('authors', [])[:12])}\nYear guess: {paper.get('year', '')}\n"
            f"</known_metadata>\n\n<first_pages>\n{(paper.get('abstract') or '')[:1500]}\n"
            f"{(paper.get('excerpt') or '')[:6000]}\n</first_pages>\n\n"
            "This PDF is in the reader's paper library. Fill in its real metadata from the text "
            "(fix the guesses if they are wrong), summarize it, and tag only the interests it "
            "genuinely covers."
        )
        try:
            data = await self._json(system=CARD_SYSTEM, prompt=prompt, schema=schema, effort=config.SUMMARY_EFFORT)
        except AUTH_ERRORS as e:
            self._disable(f"{type(e).__name__}: {e}")
            return None
        except (*API_ERRORS, Refused, ValueError) as e:
            log.warning("library card failed for %r: %s", paper.get("rel_path"), e)
            return None
        data["relevance"] = max(0, min(10, int(data.get("relevance", 0))))
        data["tags"] = [t for t in data["tags"] if t != "Other"]
        return data

    # ------------------------------------------------------------ ideation assistant

    IDEA_MODES = {
        "critique": (
            "Pressure-test this research idea like a sharp, constructive senior colleague. Markdown sections:\n"
            "## Verdict (one line)\n## What's genuinely strong\n## Weak spots & risks\n"
            "## Novelty check (against the linked papers and what you know of the field; name specific prior work)\n"
            "## A sharper version of the idea\n## Next 3 concrete steps (each doable within a week)"
        ),
        "expand": (
            "Brainstorm with me. Give 6 distinct directions this idea could go, as a Markdown list. For each: "
            "a bold one-line name, the core twist, why it might work, and the cheapest experiment that would "
            "test it. Mix safe extensions with 1-2 bold, contrarian bets. End with a line recommending one."
        ),
        "proposal": (
            "Turn this into a research proposal draft in Markdown with these sections:\n"
            "## Title\n## Abstract (≤150 words)\n## Motivation & problem\n## Research questions / hypotheses\n"
            "## Related work & gap\n## Approach\n## Experiments & evaluation (datasets/platforms, baselines, metrics)\n"
            "## Expected contributions\n## Risks & mitigations\n## Timeline (phases with rough durations)\n"
            "## Resources needed\nKeep it concrete and specific to the idea; mark assumptions you had to make."
        ),
    }

    async def idea_assist(self, mode: str, idea: dict, papers: list[dict], context: str) -> str:
        """Markdown help for an idea: critique, brainstorm, or a proposal draft."""
        if not self.enabled:
            return f"_The idea assistant needs an AI key: set `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` in `.env` and restart ({self.disabled_reason or 'no key'})._"
        return await self._text(
            system="You are a creative, rigorous research advisor helping a researcher develop ideas.",
            messages=[{"role": "user", "content": _idea_prompt(idea, papers, context) + self.IDEA_MODES[mode]}],
            effort="medium", max_tokens=16000,
        )

    async def idea_roadmap(self, idea: dict, papers: list[dict], context: str) -> list[dict]:
        """A milestone plan for an idea: [{title, detail, weeks}]."""
        if not self.enabled:
            raise ValueError("Roadmaps need an AI key: set OPENAI_API_KEY or ANTHROPIC_API_KEY in .env and restart")
        schema = {
            "type": "object",
            "properties": {"milestones": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short, action-oriented."},
                    "detail": {"type": "string", "description": "One or two sentences: what done looks like."},
                    "weeks": {"type": "integer", "description": "Rough effort in weeks."},
                },
                "required": ["title", "detail", "weeks"], "additionalProperties": False,
            }}},
            "required": ["milestones"], "additionalProperties": False,
        }
        data = await self._json(
            system="You are a pragmatic research advisor who turns ideas into realistic plans.",
            prompt=_idea_prompt(idea, papers, context) +
                   "Break this into 5-9 sequential milestones from first experiment to submission-ready "
                   "paper. Front-load the riskiest assumption so it is tested early.",
            schema=schema, effort="medium",
        )
        return data["milestones"]


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
            "relevance_reason": f"From {topic['name']}'s lab; add an AI key for AI summaries.",
        }
    return {
        "tldr": sents[0] if sents else paper["title"],
        "key_points": sents[1:5],
        "problem": "", "method": "", "results": "", "why_it_matters": "", "limitations": "",
        "subtopics": subtopics,
        "paper_type": "method",
        "relevance": min(10, 1 + 2 * hits + (3 if in_title else 0) + (1 if paper.get("arxiv_id") else 0)),
        "relevance_reason": f"Keyword match ({hits} topic phrase{'s' if hits != 1 else ''}); "
                            "add an AI key for AI summaries.",
    }


def _idea_prompt(idea: dict, papers: list[dict], context: str) -> str:
    linked = "\n".join(
        f"- {p.get('title', '')}" + (f" — {p['tldr']}" if p.get("tldr") else "") for p in papers[:20]
    ) or "(none)"
    milestones = "\n".join(f"- {m.get('title', '')}" for m in idea.get("milestones") or []) or "(none)"
    return (
        f"<researcher_context>\n{context or '(not given)'}\n</researcher_context>\n\n"
        f"<idea kind=\"{idea.get('kind', 'idea')}\" status=\"{idea.get('status', '')}\">\n"
        f"Title: {idea.get('title', '')}\nTags: {', '.join(idea.get('tags') or [])}\n"
        f"Notes:\n{idea.get('body') or '(empty)'}\n\nCurrent milestones:\n{milestones}\n</idea>\n\n"
        f"<linked_papers>\n{linked}\n</linked_papers>\n\n"
    )


def keyword_tags(interests: list[str], text: str) -> list[str]:
    """Interests whose words show up in the text (the no-AI tagger for library papers)."""
    text = text.lower()
    out = []
    for i in interests:
        words = [w for w in re.findall(r"[a-z][a-z0-9-]*", i.lower()) if len(w) > 3]
        if i.lower() in text or (words and all(re.search(rf"\b{re.escape(w[:6])}", text) for w in words)):
            out.append(i)
    return out


def fallback_digest(items: list[tuple[dict, dict]]) -> str:
    top = sorted(items, key=lambda x: x[1]["relevance"], reverse=True)[:5]
    return "### Top new papers\n" + "\n".join(
        f"- **{p['title']}** ({t['name']}) — {p['summary'].get('tldr', '')}" for t, p in top
    )


summarizer = Summarizer()
