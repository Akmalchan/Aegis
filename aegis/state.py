"""Per-repo memory of open findings -> GitHub issue numbers. Also an append-only event log for the dashboard."""
import json, time
from . import config


def _path(repo: str):
    return config.STATE_DIR / (repo.replace("/", "__") + ".json")


def load(repo: str) -> dict:
    p = _path(repo)
    return json.loads(p.read_text()) if p.exists() else {"open": {}, "history": []}


def save(repo: str, data: dict) -> None:
    _path(repo).write_text(json.dumps(data, indent=2))


def log_event(kind: str, **fields) -> None:
    rec = {"ts": time.time(), "kind": kind, **fields}
    with config.EVENTS_LOG.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")


def recent_events(n: int = 50) -> list[dict]:
    if not config.EVENTS_LOG.exists():
        return []
    lines = config.EVENTS_LOG.read_text().splitlines()[-n:]
    return [json.loads(l) for l in lines][::-1]
