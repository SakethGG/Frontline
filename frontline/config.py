import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name) or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name) or default)
    except ValueError:
        return default


DB_PATH = Path(os.getenv("FRONTLINE_DB") or ROOT / "data" / "frontline.db")
STATIC_DIR = ROOT / "static"

MODEL = os.getenv("FRONTLINE_MODEL") or "claude-opus-5-5"
SUMMARY_EFFORT = os.getenv("FRONTLINE_SUMMARY_EFFORT") or "low"
DEEP_DIVE_EFFORT = os.getenv("FRONTLINE_DEEP_DIVE_EFFORT") or "medium"

MAX_PAPERS_PER_TOPIC = _int("FRONTLINE_MAX_PAPERS_PER_TOPIC", 40)
# Followed researchers: how far back the first sweep looks, and the per-sweep summary cap
RESEARCHER_LOOKBACK_DAYS = _int("FRONTLINE_RESEARCHER_LOOKBACK_DAYS", 180)
MAX_PAPERS_PER_RESEARCHER = _int("FRONTLINE_MAX_PAPERS_PER_RESEARCHER", 60)
CONCURRENCY = _int("FRONTLINE_CONCURRENCY", 4)
AUTO_SWEEP_HOURS = _float("FRONTLINE_AUTO_SWEEP_HOURS", 0)

SEMANTIC_SCHOLAR_API_KEY = os.getenv("SEMANTIC_SCHOLAR_API_KEY") or ""
OPENALEX_MAILTO = os.getenv("OPENALEX_MAILTO") or ""

NTFY_TOPIC = os.getenv("NTFY_TOPIC") or ""
NTFY_SERVER = (os.getenv("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
SMTP_HOST = os.getenv("SMTP_HOST") or ""
SMTP_PORT = _int("SMTP_PORT", 587)
SMTP_USER = os.getenv("SMTP_USER") or ""
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD") or ""
DIGEST_TO = os.getenv("DIGEST_TO") or ""
PUBLIC_URL = (os.getenv("PUBLIC_URL") or "http://localhost:8000").rstrip("/")

DEFAULT_TOPICS = [
    {
        "slug": "legged-robots",
        "name": "Legged Robots",
        "description": (
            "Legged locomotion and loco-manipulation for quadrupeds, bipeds and humanoids: "
            "controller design, learning-based locomotion, morphology/controller co-design, "
            "actuation and hardware, perception for rough terrain, and sim-to-real transfer."
        ),
        "queries": [
            "legged robot",
            "quadruped",
            "humanoid locomotion",
            "bipedal locomotion",
            "legged locomotion",
            "loco-manipulation",
        ],
        "subtopics": [
            "Co-design",
            "Control (MPC / WBC)",
            "Reinforcement Learning",
            "Sim-to-Real",
            "Perception & Terrain",
            "Hardware & Actuation",
            "Loco-manipulation",
        ],
        "arxiv_categories": ["cs.RO", "cs.LG", "eess.SY", "cs.AI"],
        "lookback_days": 7,
        "enabled": True,
    }
]
