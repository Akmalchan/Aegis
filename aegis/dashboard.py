"""AEGIS dashboard: static page at / plus read-only JSON under /api/*.

Every endpoint reads ClickHouse through aegis.ch when it is enabled and falls back to local state files
(state/*.json, state/events.jsonl) otherwise. Nothing here ever returns 500 because ClickHouse is down:
errors are logged and the endpoint answers with empty data and `"ch": false`.
"""
from __future__ import annotations

import datetime as _dt
import decimal
import importlib
import logging
import math
import time
from pathlib import Path

from fastapi import APIRouter, FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config, state

log = logging.getLogger("aegis.dashboard")
STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_DIR.mkdir(exist_ok=True)

router = APIRouter()

_EMPTY_INSIGHTS = {"rising_repos": [], "noisy_rules": [], "reopened": [], "agent_latency": []}


# ---------- helpers ----------

def _ch():
    """aegis.ch module, or None if it can't be imported (e.g. mid-edit) — never raises."""
    try:
        return importlib.import_module("aegis.ch")
    except Exception as e:  # noqa: BLE001
        log.warning("aegis.ch import failed: %s", e)
        return None


def _ch_on() -> bool:
    ch = _ch()
    if ch is None:
        return False
    try:
        return bool(ch.enabled())
    except Exception as e:  # noqa: BLE001
        log.warning("ch.enabled() failed: %s", e)
        return False


def _call(name: str, *args, default=None):
    """Call aegis.ch.<name>(*args) if CH is enabled and the function exists. Returns (value, ok)."""
    ch = _ch()
    fn = getattr(ch, name, None) if ch is not None else None
    if fn is None or not _ch_on():
        return default, False
    try:
        return fn(*args), True
    except Exception as e:  # noqa: BLE001
        log.warning("ch.%s failed: %s", name, e)
        return default, False


def _clean(v):
    """Make ClickHouse values JSON-safe (dates, decimals, NaN, bytes)."""
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if isinstance(v, _dt.datetime):
        return v.timestamp() if v.tzinfo else v.replace(tzinfo=_dt.timezone.utc).timestamp()
    if isinstance(v, _dt.date):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return 0.0
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return v


def _fleet() -> dict[str, list[str]]:
    try:
        return config.load_fleet()
    except Exception as e:  # noqa: BLE001
        log.warning("load_fleet failed: %s", e)
        return {}


def _local_events(n: int) -> list[dict]:
    try:
        return state.recent_events(n)
    except Exception as e:  # noqa: BLE001
        log.warning("state.recent_events failed: %s", e)
        return []


def _live(e: dict) -> bool:
    """Backfill rows are history, not live activity — keep them out of the feed and the WAKE flash."""
    return e.get("agent") != "backfill" and e.get("trigger") != "backfill"


def _events(n: int) -> tuple[list[dict], str]:
    rows, ok = _call("recent_events", n * 4, default=None)
    if ok and rows:
        live = [e for e in _clean(list(rows)) if isinstance(e, dict) and _live(e)]
        if live:
            return live[:n], "clickhouse"
    return [e for e in _local_events(n) if _live(e)], "local"


def _state(repo: str) -> dict:
    try:
        return state.load(repo)
    except Exception:  # noqa: BLE001
        return {"open": {}, "history": []}


# ---------- page + static ----------

@router.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})


def mount(app: FastAPI) -> None:
    """Attach the dashboard router and /static to the scanner app."""
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------- JSON ----------

@router.get("/api/fleet", tags=["dashboard"])
def api_fleet():
    counts, ok = _call("fleet_counts", default=None)
    by_repo = {}
    if ok and counts:
        for r in counts:
            by_repo[r.get("repo")] = r
    out = []
    for agent, repos in _fleet().items():
        rows = []
        for repo in repos:
            c = by_repo.get(repo)
            if c is not None:
                rows.append({"repo": repo, "open": int(c.get("open_now") or 0), "resolved": int(c.get("closed") or 0),
                             "opened": int(c.get("opened") or 0), "source": "clickhouse"})
            else:
                st = _state(repo)
                rows.append({"repo": repo, "open": len(st.get("open", {})), "resolved": len(st.get("history", [])),
                             "opened": len(st.get("open", {})) + len(st.get("history", [])), "source": "local"})
        out.append({"agent": agent, "repos": rows})
    events, _src = _events(40)
    return {"analyst": "guild" if config.GUILD_ENABLED else "openai", "ch": _ch_on(), "agents": out, "events": events}


@router.get("/api/events", tags=["dashboard"])
def api_events(n: int = Query(60, ge=1, le=500)):
    events, src = _events(n)
    return {"source": src, "events": events}


@router.get("/api/timeline", tags=["dashboard"])
def api_timeline(weeks: int = Query(52, ge=1, le=520)):
    t0 = time.perf_counter()
    rows, ok = _call("posture_timeline", weeks, default=[])
    rows = _clean(list(rows or []))
    for r in rows:
        r["week"] = str(r.get("week", ""))[:10]
        r["n"] = int(r.get("n") or 0)
        r["severity"] = str(r.get("severity") or "INFO").upper()
    return {"ch": ok, "rows": rows, "ms": int((time.perf_counter() - t0) * 1000),
            "note": "" if ok else "ClickHouse offline — no history"}


@router.get("/api/latency", tags=["dashboard"])
def api_latency(hours: int = Query(24 * 7, ge=1, le=24 * 3650)):
    ins, ok = _call("insights", hours, default=None)
    rows = (ins or {}).get("agent_latency", []) if isinstance(ins, dict) else []
    return {"ch": ok, "agents": _clean(list(rows))}


@router.get("/api/mttr", tags=["dashboard"])
def api_mttr():
    rows, ok = _call("repo_mttr", default=None)
    if ok and rows is not None:
        return {"ch": True, "repos": _clean(list(rows))}
    # fallback: local state history (opened_at / closed_at epoch seconds)
    out = []
    for repos in _fleet().values():
        for repo in repos:
            hist = _state(repo).get("history", [])
            d = [h["closed_at"] - h["opened_at"] for h in hist if h.get("closed_at") and h.get("opened_at")]
            if d:
                out.append({"repo": repo, "mttr_h": sum(d) / len(d) / 3600, "closed": len(d)})
    return {"ch": False, "repos": out}


@router.get("/api/insights", tags=["dashboard"])
def api_insights(hours: int = Query(24, ge=1, le=24 * 3650)):
    ins, ok = _call("insights", hours, default=None)
    data = dict(_EMPTY_INSIGHTS)
    if isinstance(ins, dict):
        data.update(_clean(ins))
    return {"ch": ok, "hours": hours, **data}


@router.get("/api/stats", tags=["dashboard"])
def api_stats():
    fleet = _fleet()
    base = {"agents": len(fleet), "repos": sum(len(v) for v in fleet.values()),
            "analyst": "guild" if config.GUILD_ENABLED else "openai"}
    t0 = time.perf_counter()
    s, ok = _call("stats", default=None)
    ms = int((time.perf_counter() - t0) * 1000)
    s = _clean(s) if isinstance(s, dict) else {}
    return {**base, "ch": ok,
            "findings": int(s.get("findings") or 0), "scans": int(s.get("scans") or 0),
            "actions": int(s.get("actions") or 0), "ch_repos": int(s.get("repos") or 0),
            "rules": int(s.get("rules") or 0), "query_ms": s.get("query_ms", ms if ok else 0)}
