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

# Which AI writes summaries: "claude", "openai", or "auto" (OpenAI if only its key is set)
LLM_PROVIDER = (os.getenv("FRONTLINE_LLM") or "auto").strip().lower()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY") or ""
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") or ""
OPENAI_MODEL = os.getenv("OPENAI_MODEL") or "gpt-5-mini"
MODEL = os.getenv("FRONTLINE_MODEL") or "claude-opus-5-5"
SUMMARY_EFFORT = os.getenv("FRONTLINE_SUMMARY_EFFORT") or "low"
DEEP_DIVE_EFFORT = os.getenv("FRONTLINE_DEEP_DIVE_EFFORT") or "medium"

MAX_PAPERS_PER_TOPIC = _int("FRONTLINE_MAX_PAPERS_PER_TOPIC", 40)
# Followed researchers: how far back the first sweep looks, and the per-sweep summary cap
RESEARCHER_LOOKBACK_DAYS = _int("FRONTLINE_RESEARCHER_LOOKBACK_DAYS", 180)
MAX_PAPERS_PER_RESEARCHER = _int("FRONTLINE_MAX_PAPERS_PER_RESEARCHER", 60)
CONCURRENCY = _int("FRONTLINE_CONCURRENCY", 4)
AUTO_SWEEP_HOURS = _float("FRONTLINE_AUTO_SWEEP_HOURS", 0)

# Paper library: the folder holding your PDFs (e.g. a Google Drive / Dropbox / OneDrive synced
# folder). Can also be picked in the app, which takes precedence.
LIBRARY_DIR = os.getenv("FRONTLINE_LIBRARY_DIR") or ""
# Conference catalog: Hugging Face's community-maintained AI deadlines (one YAML file per venue)
CONFERENCE_CATALOG_URL = (os.getenv("FRONTLINE_CONFERENCE_CATALOG_URL")
                          or "https://codeload.github.com/huggingface/ai-deadlines/tar.gz/refs/heads/main")

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

# Set by frontline.desktop: enables native Windows toasts under this app identity
DESKTOP = os.getenv("FRONTLINE_DESKTOP") == "1"
APP_ID = "Frontline.ResearchRadar"

# Topics seeded into a fresh database. Empty: you add your own from the UI.
DEFAULT_TOPICS: list[dict] = []
