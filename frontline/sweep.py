"""Sweep orchestration: discover -> skip known -> summarize -> store -> digest -> notify."""

import asyncio
import logging
from datetime import date, timedelta

from . import config, db, notify, sources
from .summarizer import summarizer

log = logging.getLogger("frontline.sweep")


class SweepManager:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.state: dict = {"running": False}
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._lock.locked()

    def start(self, topic_ids: list[int] | None = None, trigger: str = "manual") -> bool:
        if self.running:
            return False
        self._task = asyncio.create_task(self.run(topic_ids, trigger))
        return True

    def _log(self, msg: str) -> None:
        log.info(msg)
        self.state.setdefault("log", []).append(msg)
        self.state["log"] = self.state["log"][-40:]

    async def run(self, topic_ids: list[int] | None, trigger: str) -> None:
        async with self._lock:
            topics = [t for t in db.list_topics()
                      if t["enabled"] and (not topic_ids or t["id"] in topic_ids)]
            sweep_id = db.start_sweep(trigger)
            self.state = {
                "running": True, "sweep_id": sweep_id, "phase": "searching",
                "topics_total": len(topics), "topics_done": 0,
                "to_summarize": 0, "summarized": 0, "new": 0, "log": [],
            }
            stats: dict = {"topics": {}}
            new_items: list[tuple[dict, dict]] = []
            try:
                for topic in topics:
                    new_items += await self._sweep_topic(sweep_id, topic, stats)
                    self.state["topics_done"] += 1
                self.state["phase"] = "digest"
                self._log("Writing digest…")
                digest = await summarizer.digest(
                    sorted(new_items, key=lambda x: x[1]["relevance"], reverse=True))
                stats["new"] = len(new_items)
                self.state["phase"] = "notifying"
                stats["notified"] = await notify.notify_sweep(
                    sweep_id, [p for _, p in new_items], digest)
                db.finish_sweep(sweep_id, "done", stats, digest)
                self._log(f"Done — {len(new_items)} new papers.")
            except Exception as e:
                log.exception("sweep failed")
                stats["error"] = str(e)
                db.finish_sweep(sweep_id, "failed", stats)
                self._log(f"Sweep failed: {e}")
            finally:
                self.state.update(running=False, phase="done", new=len(new_items))

    async def _sweep_topic(self, sweep_id: int, topic: dict, stats: dict) -> list[tuple[dict, dict]]:
        since = date.today() - timedelta(days=max(1, int(topic["lookback_days"])))
        researcher = sources.is_researcher(topic)
        where = "their lab's papers" if researcher else "arXiv, Semantic Scholar, OpenAlex"
        self._log(f"[{topic['name']}] searching {where} since {since}…")
        found, src_stats = await sources.discover(topic, since)
        known = db.known_keys(topic["id"])
        cap = config.MAX_PAPERS_PER_RESEARCHER if researcher else config.MAX_PAPERS_PER_TOPIC
        fresh = [p for p in found if p["paper_key"] not in known][:cap]
        stats["topics"][topic["name"]] = {**src_stats, "new": len(fresh)}
        self._log(f"[{topic['name']}] {src_stats} → {len(fresh)} new to summarize")
        self.state["phase"] = "summarizing"
        self.state["to_summarize"] += len(fresh)

        sem = asyncio.Semaphore(max(1, config.CONCURRENCY))
        out: list[tuple[dict, dict]] = []

        async def handle(p: dict) -> None:
            async with sem:
                summary, by = await summarizer.card(topic, p)
            p.update(topic_id=topic["id"], summary=summary, summarized_by=by,
                     relevance=summary["relevance"], sweep_id=sweep_id)
            pid = db.insert_paper(p)
            self.state["summarized"] += 1
            if pid:
                p["id"] = pid
                out.append((topic, p))
                self.state["new"] += 1

        await asyncio.gather(*(handle(p) for p in fresh))
        return out


manager = SweepManager()


async def auto_sweep_loop() -> None:
    if config.AUTO_SWEEP_HOURS <= 0:
        return
    while True:
        await asyncio.sleep(config.AUTO_SWEEP_HOURS * 3600)
        if not manager.running:
            await manager.run(None, "scheduled")
