import json, os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1")
GUILD_OWNER = os.getenv("GUILD_OWNER", "")
GUILD_WORKSPACE = os.getenv("GUILD_WORKSPACE", "")
GUILD_TRIGGER_KEY = os.getenv("GUILD_TRIGGER_KEY", "")
GUILD_AGENT_ID = os.getenv("GUILD_AGENT_ID", "")
GUILD_ENABLED = bool(GUILD_OWNER and GUILD_WORKSPACE and GUILD_TRIGGER_KEY)


def redact(text: str) -> str:
    """Strip secrets from anything that goes to a log, an HTTP response or ClickHouse (git errors echo the clone URL)."""
    for secret in (GITHUB_TOKEN, GUILD_TRIGGER_KEY, OPENAI_API_KEY, os.getenv("SCANNER_KEY", "")):
        if secret:
            text = text.replace(secret, "***")
    return text

# Repo checkout (dev/server): everything lives next to the code. Installed CLI (pipx/pip): rules ship inside the
# package, caches go to ~/.aegis (or $AEGIS_HOME) so nothing is written into site-packages.
_IN_REPO = (ROOT / "pyproject.toml").exists() and (ROOT / "rules").is_dir()
HOME = ROOT if _IN_REPO else Path(os.getenv("AEGIS_HOME") or Path.home() / ".aegis")
CACHE_DIR = HOME / ".cache"
STATE_DIR = HOME / "state"
RULES_DIR = ROOT / "rules" if _IN_REPO else Path(__file__).resolve().parent / "rules"
EVENTS_LOG = STATE_DIR / "events.jsonl"
for d in (CACHE_DIR, STATE_DIR):
    d.mkdir(parents=True, exist_ok=True)


def load_fleet() -> dict[str, list[str]]:
    return json.loads((ROOT / "fleet.json").read_text())["agents"]


def agent_for_repo(repo: str) -> str | None:
    for name, repos in load_fleet().items():
        if repo in repos:
            return name
    return None
