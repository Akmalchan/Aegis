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

CACHE_DIR = ROOT / ".cache"
STATE_DIR = ROOT / "state"
RULES_DIR = ROOT / "rules"
EVENTS_LOG = STATE_DIR / "events.jsonl"
for d in (CACHE_DIR, STATE_DIR):
    d.mkdir(exist_ok=True)


def load_fleet() -> dict[str, list[str]]:
    return json.loads((ROOT / "fleet.json").read_text())["agents"]


def agent_for_repo(repo: str) -> str | None:
    for name, repos in load_fleet().items():
        if repo in repos:
            return name
    return None
