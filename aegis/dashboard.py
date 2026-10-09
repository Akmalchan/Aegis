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
    from . import patrol
    patrol.start()
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


def _is_test_repo(repo) -> bool:
    r = str(repo or "")
    return r.startswith("local:") or r.startswith("/private/") or r == "selftest/repo"


def _not_test(rows):
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            out.append(r); continue
        if _is_test_repo(r.get("repo")):
            continue
        ref = str(r.get("ref") or "")
        if str(r.get("kind", "")).startswith("handoff") and (ref.startswith("t->t:") or (r.get("agent") == "t" and r.get("to_agent") == "t")):
            continue
        out.append(r)
    return out


@router.get("/api/events", tags=["dashboard"])
def api_events(n: int = Query(60, ge=1, le=500)):
    events, src = _events(n)
    return {"source": src, "events": _not_test(events)}


@router.get("/api/timeline", tags=["dashboard"])
def api_timeline(weeks: int = Query(52, ge=1, le=1200), bucket: str = Query("week", pattern="^(week|month|quarter)$")):
    t0 = time.perf_counter()
    fast, ok = _call("posture_fast", weeks * 7, bucket, default=None)  # posture_daily MV rollup
    src = "posture_daily"
    if ok and isinstance(fast, dict) and fast.get("ok") and fast.get("rows"):
        rows = fast["rows"]
    else:  # rollup missing/empty -> scan aegis.findings
        rows, ok = _call("posture_timeline", weeks, bucket, default=[])
        src = "findings"
    rows = _not_test(_clean(list(rows or [])))
    for r in rows:
        r["week"] = str(r.get("week", ""))[:10]
        r["n"] = int(r.get("n") or 0)
        r["severity"] = str(r.get("severity") or "INFO").upper()
    return {"ch": ok, "rows": rows, "ms": int((time.perf_counter() - t0) * 1000), "source": src,
            "note": "" if ok else "ClickHouse offline — no history"}


@router.get("/api/funnel", tags=["dashboard"])
def api_funnel(hours: int = Query(24 * 30, ge=1, le=24 * 3650)):
    """detected -> issue_opened -> pr_opened -> verified -> issue_closed (windowFunnel over live findings + actions)."""
    t0 = time.perf_counter()
    d, ok = _call("fix_funnel", hours, default=None)
    d = _clean(d) if isinstance(d, dict) else {}
    return {"ch": ok, "hours": hours, "stages": d.get("stages", []), "per_repo": d.get("per_repo", []),
            "query_ms": d.get("query_ms", 0), "ms": int((time.perf_counter() - t0) * 1000)}


@router.get("/api/anomalies", tags=["dashboard"])
def api_anomalies(minutes: int = Query(60, ge=1, le=24 * 60)):
    """Agents acting > 3x their own per-minute median, new bursts, or any denied action (agent_activity_1m MV)."""
    t0 = time.perf_counter()
    rows, ok = _call("agent_anomalies", minutes, default=[])
    ms = round((time.perf_counter() - t0) * 1000, 1)
    return {"ch": ok, "minutes": minutes, "anomalies": _clean(list(rows or [])), "query_ms": ms}


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
        for k, v in list(data.items()):
            if isinstance(v, list):
                data[k] = _not_test(v)
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


@router.get("/api/alerts", tags=["dashboard"])
def api_alerts(hours: int = Query(48, ge=1, le=24 * 30)):
    """Attacks on the fleet itself: prompt injection planted in a repo, and policy denials."""
    ch = _ch()
    if ch is None or not _ch_on():
        return {"ch": False, "alerts": []}
    inj = ch._rows(
        "SELECT max(ts) AS last_ts, agent, repo, path, min(line) AS first_line, rule_id FROM aegis.findings "
        "WHERE rule_id LIKE '%agent-directed%' AND agent != 'backfill' AND ts > now() - INTERVAL {h:UInt32} HOUR "
        "GROUP BY agent, repo, path, rule_id ORDER BY last_ts DESC LIMIT 10", {"h": hours}, "alerts") or []
    den = ch._rows(
        "SELECT ts, agent, repo, ref FROM aegis.actions WHERE kind = 'denied' "
        "AND ts > now() - INTERVAL {h:UInt32} HOUR ORDER BY ts DESC LIMIT 10", {"h": hours}, "alerts") or []
    out = [{"type": "injection", "ts": r["last_ts"], "line": r["first_line"], **r} for r in inj] + [{"type": "denied", **r} for r in den]
    out = _not_test(_clean(out))
    out.sort(key=lambda r: r.get("ts") or 0, reverse=True)
    return {"ch": True, "alerts": out}


# ---------- Guild live feed (background poller over the `guild` CLI) ----------
import json as _json
import shutil as _shutil
import subprocess as _sp
import threading as _th
from concurrent.futures import ThreadPoolExecutor as _Pool

_GUILD_OWNER = config.GUILD_OWNER or "andriidrok1"
_GUILD_POLL_S = 4.0
_TERMINAL = {"DONE", "ERROR", "CANCELLED", "CANCELED", "FAILED", "INTERRUPTED"}
_guild_lock = _th.Lock()
_guild = {"ok": False, "error": "not polled yet", "sessions": [], "agents": {}, "polled_at": 0.0}
_guild_tasks: dict[str, dict] = {}   # session id -> {"status", "tasks", "fetched_at", "note"}
_guild_thread: _th.Thread | None = None


def _guild_cli(*args: str, timeout: float = 20.0):
    """Run `guild --mode json ...`; returns parsed JSON or raises."""
    exe = _shutil.which("guild")
    if not exe:
        raise RuntimeError("guild CLI not on PATH")
    p = _sp.run([exe, "--mode", "json", *args], capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout).strip()[:200] or f"guild exit {p.returncode}")
    return _json.loads(p.stdout)


def _iso_ts(s) -> float:
    try:
        return _dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:  # noqa: BLE001
        return 0.0


def _guild_agents() -> dict[str, str]:
    """agent id -> name, cached for the process lifetime (refreshed every 10 min)."""
    now = time.time()
    with _guild_lock:
        cached = _guild["agents"]
        if cached and now - cached.get("_at", 0) < 600:
            return cached
    try:
        d = _guild_cli("agent", "list", "--owner", _GUILD_OWNER)
        m = {a.get("id"): a.get("name") for a in d.get("items", []) if a.get("id")}
    except Exception as e:  # noqa: BLE001
        log.warning("guild agent list failed: %s", e)
        m = {}
    m["_at"] = now
    with _guild_lock:
        _guild["agents"] = m
    return m


def _fetch_tasks(sid: str, want_note: bool = True) -> dict:
    """Session root status + tool calls via `guild session tasks`; falls back to events for the thought text."""
    out = {"status": "UNKNOWN", "tasks": [], "fetched_at": time.time(), "note": ""}
    try:
        d = _guild_cli("session", "tasks", sid, "--limit", "30")
        items = d.get("items", []) if isinstance(d, dict) else []
        roots = [t for t in items if t.get("entity_type") == "EntTaskAgent" and not t.get("parent_task_id")]
        agent_tasks = [t for t in items if t.get("entity_type") == "EntTaskAgent"]
        if roots:
            out["status"] = str(roots[0].get("status") or "UNKNOWN").upper()
        elif agent_tasks:
            out["status"] = str(agent_tasks[-1].get("status") or "UNKNOWN").upper()
        tools = [t for t in items if t.get("entity_type") == "EntTaskTool"]
        tools.sort(key=lambda t: _iso_ts(t.get("created_at")))
        out["tasks"] = [{"name": t.get("tool_name"), "status": str(t.get("status") or "").upper(),
                         "http": t.get("http_status_code"), "ts": _iso_ts(t.get("created_at")),
                         "req": t.get("request_bytes"), "res": t.get("response_bytes")} for t in tools][-12:]
        # a sub-agent still running keeps the session alive even if the root says DISPATCHED
        if any(str(t.get("status")).upper() in ("STARTED", "RUNNING") for t in items):
            out["status"] = "STARTED"
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:120]
    if not want_note or out["status"] in _TERMINAL:
        return out
    try:
        d = _guild_cli("session", "events", sid, "--limit", "30")
        for e in reversed(d.get("items", []) if isinstance(d, dict) else []):
            c = e.get("content") or {}
            txt = c.get("text") if isinstance(c, dict) else None
            if txt:
                first = next((ln.strip("* ").strip() for ln in str(txt).splitlines() if ln.strip()), "")
                out["note"] = first[:90]
                break
    except Exception:  # noqa: BLE001
        pass
    return out


def _guild_poll_once() -> None:
    agents = _guild_agents()
    try:
        d = _guild_cli("session", "list", "--limit", "15")
    except Exception as e:  # noqa: BLE001
        with _guild_lock:
            _guild.update(ok=False, error=str(e)[:160], polled_at=time.time())
        return
    items = d.get("items", []) if isinstance(d, dict) else []
    _publish(items, agents)   # first paint: session list only, tasks come from cache
    now = time.time()
    need = []
    for s in sorted(items, key=lambda x: _iso_ts(x.get("created_at")), reverse=True):
        sid = s.get("id")
        cur = _guild_tasks.get(sid)
        created = _iso_ts(s.get("created_at"))
        fresh = now - created < 3600
        if cur is None or (cur["status"] not in _TERMINAL and (fresh or now - cur["fetched_at"] > 60)):
            need.append((sid, created, fresh))
    need = need[:6]   # at most 6 sessions per poll; the rest catch up on the next tick
    if need:
        with _Pool(max_workers=6) as pool:
            for (sid, created, fresh), res in zip(need, pool.map(lambda x: _fetch_tasks(x[0], x[2]), need)):
                if res["status"] not in _TERMINAL and not fresh:
                    res["stale"] = True   # old session that never reported a terminal state: treat as done
                _guild_tasks[sid] = res
        _publish(items, agents)


def _publish(items: list, agents: dict) -> None:
    sessions = []
    for s in items:
        sid = s.get("id")
        trig = s.get("trigger") or {}
        ag = trig.get("agent") or {}
        name = (ag.get("name") or agents.get(s.get("agent_id") or "") or (trig.get("name") or "").split("--")[0]
                or s.get("agent_name") or s.get("agent_id") or "agent")
        t = _guild_tasks.get(sid) or {"status": "UNKNOWN", "tasks": [], "note": ""}
        last = t["tasks"][-1] if t.get("tasks") else None
        st = t.get("status", "UNKNOWN")
        if t.get("stale"):
            st = "DONE"
        status = "working" if st in ("STARTED", "RUNNING", "DISPATCHED", "PENDING", "QUEUED") else (
            "failed" if st in ("ERROR", "FAILED") else "done" if st in _TERMINAL else "unknown")
        if status == "unknown" and time.time() - _iso_ts(s.get("created_at")) < 900:
            status = "working"   # young session we have not inspected yet: assume awake until proven otherwise
        repo = (trig.get("service_config") or {}).get("repo") or ""
        sessions.append({
            "id": sid, "agent": name, "agent_id": ag.get("id") or s.get("agent_id"), "status": status, "raw_status": st,
            "created_at": _iso_ts(s.get("created_at")), "last_activity_at": _iso_ts(s.get("last_activity_at")),
            "event": trig.get("event_type") or s.get("session_type") or "", "action": trig.get("action") or "",
            "repo": repo, "trigger_name": trig.get("name") or "",
            "last_tool_call": {"name": last["name"], "status": last["status"], "args": _tool_args(last, t), "ts": last["ts"]} if last else None,
            "tools": [x["name"] for x in (t.get("tasks") or [])],
            "n_tools": len(t.get("tasks") or []), "note": t.get("note", ""),
            "session_url": s.get("session_url") or f"https://app.guild.ai/sessions/{sid}",
        })
    with _guild_lock:
        _guild.update(ok=True, error="", sessions=sessions, polled_at=time.time())


def _tool_args(last: dict, t: dict) -> str:
    bits = []
    if last.get("http"):
        bits.append(f"http {last['http']}")
    if last.get("req"):
        bits.append(f"{last['req']} B in")
    if last.get("res"):
        bits.append(f"{last['res']} B out")
    return " · ".join(bits)


def _guild_loop() -> None:
    while True:
        t0 = time.time()
        try:
            _guild_poll_once()
        except Exception as e:  # noqa: BLE001
            log.warning("guild poll failed: %s", e)
            with _guild_lock:
                _guild.update(ok=False, error=str(e)[:160], polled_at=time.time())
        time.sleep(max(1.0, _GUILD_POLL_S - (time.time() - t0)))


def _ensure_guild_thread() -> None:
    global _guild_thread
    if _guild_thread is None or not _guild_thread.is_alive():
        _guild_thread = _th.Thread(target=_guild_loop, name="aegis-guild-poll", daemon=True)
        _guild_thread.start()


@router.get("/api/guild", tags=["dashboard"])
def api_guild():
    """Live Guild sessions: who is awake, what tool it is calling, link to the session. Never 500s."""
    _ensure_guild_thread()
    with _guild_lock:
        snap = dict(_guild)
    sessions = list(snap.get("sessions") or [])
    by_agent: dict[str, dict] = {}
    for s in sorted(sessions, key=lambda x: x.get("created_at") or 0, reverse=True):
        a = by_agent.setdefault(s["agent"], {"agent": s["agent"], "state": "idle", "sessions": 0, "latest": None})
        a["sessions"] += 1
        if a["latest"] is None:
            a["latest"] = s
        if s["status"] == "working":
            a["state"] = "working"
            a["current"] = s
    return {"ok": bool(snap.get("ok")), "error": snap.get("error") or "", "polled_at": snap.get("polled_at") or 0,
            "poll_s": _GUILD_POLL_S, "sessions": sessions, "agents": list(by_agent.values())}


@router.get("/api/handoffs", tags=["dashboard"])
def api_handoffs(n: int = Query(200, ge=1, le=1000)):
    """Agent-to-agent handoffs the guard validated (kind handoff_ok / handoff_rejected / handoff)."""
    events, src = _events(n)
    rows = _not_test([e for e in events if str(e.get("kind", "")).startswith("handoff")])
    return {"source": src, "handoffs": rows[:40]}


# start polling at import so the first page load already has data
try:
    _ensure_guild_thread()
except Exception as _e:  # noqa: BLE001
    log.warning("guild poll thread not started: %s", _e)


@router.get("/api/breakdown", tags=["dashboard"])
def api_breakdown():
    """Findings by severity, top rules and top repos, across everything ClickHouse remembers."""
    ch = _ch()
    if ch is None or not _ch_on():
        return {"ch": False, "severity": [], "rules": [], "repos": []}
    sev = ch._rows("SELECT severity, count() AS n FROM aegis.findings GROUP BY severity ORDER BY n DESC", None, "breakdown") or []
    rules = ch._rows("SELECT rule_id, count() AS n FROM aegis.findings GROUP BY rule_id ORDER BY n DESC LIMIT 8", None, "breakdown") or []
    repos = ch._rows("SELECT repo, count() AS n, uniqExact(fingerprint) AS uniq FROM aegis.findings GROUP BY repo ORDER BY n DESC LIMIT 16", None, "breakdown") or []
    repos = _not_test(repos)[:8]
    return _clean({"ch": True, "severity": sev, "rules": rules, "repos": repos})


@router.get("/api/stream", tags=["dashboard"])
def api_stream(after: int = Query(0, ge=0)):
    """Patrol log lines after a sequence number, plus running totals."""
    from . import patrol
    lines = patrol.since(after)[-120:]
    st = dict(patrol.STATS)
    st["rules"] = sorted(st["rules"].items(), key=lambda kv: -kv[1])[:5]
    return {"on": bool(st["started"]), "lines": lines, "stats": st}
