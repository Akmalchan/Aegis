"""Contract 2: ClickHouse access layer (fleet memory). Never raises: when ClickHouse is unconfigured, down or erroring,
every function logs a warning and returns the same neutral value the stub did, so the scanner keeps working.

Config (env / .env): CLICKHOUSE_HOST, CLICKHOUSE_PORT (8443 if secure else 8123), CLICKHOUSE_USER (default),
CLICKHOUSE_PASSWORD, CLICKHOUSE_SECURE ("1"/"true" for Cloud). One client per process (safe under multiprocessing).

Writes:   insert_scan, insert_findings, insert_action          (contract)
Detect:   enrich(repo, findings) -> findings + seen_before, dismissed_before, repo_mttr_h   (contract, 1 query)
Monitor:  insights(hours) -> {rising_repos, noisy_rules, reopened, agent_latency}         (contract)
          recent_events(n)   -> [{ts (epoch s), kind ('scan' | action kind), agent, repo, sha, verdict, n_findings,
                                  total_ms, trigger, ref, fingerprint, latency_ms, session_url}]  (no backfill scans)
Dashboard (extra):
          posture_timeline(weeks=52) -> [{week 'YYYY-MM-DD', repo, severity, n}]  n = distinct open fingerprints
                                        by commit week (real history from backfill), window ends at latest commit
          repo_mttr()        -> [{repo, mttr_h, closed}]           issue_opened -> issue_closed per fingerprint
          fleet_counts()     -> [{repo, agent, opened, closed, open_now}]   from actions
          stats()            -> {findings, scans, actions, repos, rules, query_ms}
Admin:    init() applies clickhouse/schema.sql.  CLI: python -m aegis.ch selftest | init | stats | purge-selftest
"""
from __future__ import annotations

import logging, os, sys, threading, time, uuid
from datetime import datetime, timedelta, timezone

from . import config  # noqa: F401  (loads .env)

log = logging.getLogger("aegis.ch")
_lock = threading.Lock()
_clients: dict[int, object] = {}
_probe = {"at": 0.0, "ok": False}
PROBE_TTL = 30.0

SCAN_COLS = ["ts", "agent", "repo", "sha", "base_sha", "trigger", "n_files", "n_findings", "verdict", "semgrep_ms",
             "total_ms"]
FINDING_COLS = ["ts", "agent", "repo", "sha", "commit_ts", "fingerprint", "rule_id", "severity", "cwe", "path", "line",
                "has_fix", "status"]
ACTION_COLS = ["ts", "agent", "repo", "kind", "ref", "fingerprint", "latency_ms", "session_url"]
NEUTRAL_INSIGHTS = {"rising_repos": [], "noisy_rules": [], "reopened": [], "agent_latency": []}


def _host() -> str:
    h = os.getenv("CLICKHOUSE_HOST", "").strip()
    return h.split("://", 1)[-1].rstrip("/").split(":")[0]


def _client():
    pid = os.getpid()
    c = _clients.get(pid)
    if c is not None:
        return c
    with _lock:
        if pid not in _clients:
            import clickhouse_connect
            from clickhouse_connect import common
            common.set_setting("autogenerate_session_id", False)  # no session locks across threads
            secure = os.getenv("CLICKHOUSE_SECURE", "").lower() in ("1", "true", "yes")
            _clients[pid] = clickhouse_connect.get_client(
                host=_host(), port=int(os.getenv("CLICKHOUSE_PORT") or (8443 if secure else 8123)),
                username=os.getenv("CLICKHOUSE_USER") or "default", password=os.getenv("CLICKHOUSE_PASSWORD", ""),
                secure=secure, connect_timeout=5, send_receive_timeout=60,
                settings={"async_insert": 1, "wait_for_async_insert": 1})
        return _clients[pid]


def _down(e: Exception, what: str) -> None:
    log.warning("clickhouse %s failed: %s", what, e)
    _clients.pop(os.getpid(), None)
    _probe.update(at=time.monotonic(), ok=False)


def enabled() -> bool:
    """True when a ClickHouse connection is configured and reachable (probe cached for 30 s)."""
    if not _host():
        return False
    if time.monotonic() - _probe["at"] < PROBE_TTL and _probe["at"]:
        return _probe["ok"]
    try:
        ok = _client().command("SELECT 1") == 1
        _probe.update(at=time.monotonic(), ok=ok)
    except Exception as e:
        _down(e, "probe")
    return _probe["ok"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dt(v) -> datetime:
    """None | datetime | epoch | ISO string -> aware UTC datetime."""
    try:
        if v is None or v == "":
            return _now()
        if isinstance(v, datetime):
            return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v, timezone.utc)
        s = str(v).strip()
        if s.lstrip("-").replace(".", "", 1).isdigit():
            return datetime.fromtimestamp(float(s), timezone.utc)
        return _dt(datetime.fromisoformat(s.replace("Z", "+00:00")))
    except Exception:
        return _now()


def _insert(table: str, rows: list[list], cols: list[str]) -> None:
    if rows and enabled():
        try:
            _client().insert(f"aegis.{table}", rows, column_names=cols)
        except Exception as e:
            _down(e, f"insert {table}")


def _rows(sql: str, params: dict | None = None, what: str = "query") -> list[dict] | None:
    """named rows, or None when disabled/failed."""
    if not enabled():
        return None
    try:
        return list(_client().query(sql, parameters=params or {}).named_results())
    except Exception as e:
        _down(e, what)
        return None


def _plain(v):
    if isinstance(v, float):
        return 0.0 if v != v or v in (float("inf"), float("-inf")) else round(v, 3)
    if isinstance(v, int) or isinstance(v, str):
        return v
    try:
        return float(v)
    except Exception:
        return str(v)


def _clean(rows: list[dict]) -> list[dict]:
    return [{k: _plain(v) for k, v in r.items()} for r in rows]


def init() -> bool:
    """Apply clickhouse/schema.sql (CREATE DATABASE + 3 tables). Idempotent."""
    if not enabled():
        return False
    try:
        sql = (config.ROOT / "clickhouse" / "schema.sql").read_text()
        body = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
        for stmt in filter(None, (s.strip() for s in body.split(";"))):
            _client().command(stmt)
        return True
    except Exception as e:
        _down(e, "init")
        return False


def insert_scan(agent: str, repo: str, sha: str, base_sha: str, trigger: str, n_files: int,
                n_findings: int, verdict: str, semgrep_ms: int, total_ms: int) -> None:
    """One row into aegis.scans."""
    try:
        _insert("scans", [[_now(), agent, repo, sha, base_sha or "", trigger, int(n_files or 0), int(n_findings or 0),
                           verdict, int(semgrep_ms or 0), int(total_ms or 0)]], SCAN_COLS)
    except Exception as e:
        log.warning("insert_scan: %s", e)


def _cwe(v) -> str:
    if isinstance(v, (list, tuple)):
        v = v[0] if v else ""
    return str(v or "")


def insert_findings(agent: str, repo: str, sha: str, findings: list[dict], status: str = "new",
                    commit_ts=None) -> None:
    """One row per finding into aegis.findings. `findings` are scanner dicts (rule_id, path, start_line, severity, cwe,
    fingerprint, fix?). `status` in new|still_open|resolved|dismissed."""
    if not findings:
        return
    try:
        ts, cts = _now(), _dt(commit_ts)
        rows = [[ts, agent, repo, sha, cts, f.get("fingerprint", ""), f.get("rule_id", ""), str(f.get("severity") or ""),
                 _cwe(f.get("cwe")), f.get("path", ""), int(f.get("start_line") or 0), 1 if f.get("fix") else 0, status]
                for f in findings]
        _insert("findings", rows, FINDING_COLS)
    except Exception as e:
        log.warning("insert_findings: %s", e)


def insert_action(agent: str, repo: str, kind: str, ref: str, fingerprint: str = "",
                  latency_ms: int = 0, session_url: str = "") -> None:
    """One row into aegis.actions."""
    try:
        _insert("actions", [[_now(), agent, repo, kind, str(ref or ""), fingerprint or "", int(latency_ms or 0),
                             session_url or ""]], ACTION_COLS)
    except Exception as e:
        log.warning("insert_action: %s", e)


# open -> first close per (repo, fingerprint), hours. Shared by enrich and repo_mttr.
_CYCLES = """SELECT repo, fingerprint, (toUnixTimestamp64Milli(c) - toUnixTimestamp64Milli(o)) / 3600000 AS h
  FROM (SELECT repo, fingerprint, minIf(ts, kind = 'issue_opened') AS o, maxIf(ts, kind = 'issue_closed') AS c,
               countIf(kind = 'issue_opened') AS no, countIf(kind = 'issue_closed') AS nc
        FROM aegis.actions WHERE kind IN ('issue_opened', 'issue_closed') AND fingerprint != '' {where}
        GROUP BY repo, fingerprint)
  WHERE no > 0 AND nc > 0 AND c > o"""

_ENRICH = f"""
WITH f AS (SELECT fingerprint, count() AS seen, countIf(status = 'dismissed') AS dis FROM aegis.findings
           WHERE fingerprint IN {{fps:Array(String)}} AND ts < now64(3) GROUP BY fingerprint),
     d AS (SELECT fingerprint, 1 AS x FROM aegis.actions
           WHERE kind = 'dismissed' AND fingerprint IN {{fps:Array(String)}} GROUP BY fingerprint),
     m AS ({_CYCLES.format(where="AND repo = {repo:String}")}),
     r AS (SELECT avgOrDefault(h) AS ra FROM m)
SELECT fp, f.seen AS seen_before, (f.dis > 0 OR d.x = 1) AS dismissed_before,
       if(m.fingerprint != '', m.h, r.ra) AS repo_mttr_h
FROM (SELECT arrayJoin({{fps:Array(String)}}) AS fp) AS q
LEFT JOIN f ON f.fingerprint = q.fp LEFT JOIN d ON d.fingerprint = q.fp LEFT JOIN m ON m.fingerprint = q.fp
CROSS JOIN r
SETTINGS join_use_nulls = 0"""


def enrich(repo: str, findings: list[dict]) -> list[dict]:
    """Add seen_before (int), dismissed_before (bool), repo_mttr_h (float) to each finding, in place and returned.
    One round trip for the whole list. Neutral (0/False/0.0) when ClickHouse is unavailable."""
    for f in findings:
        f.setdefault("seen_before", 0)
        f.setdefault("dismissed_before", False)
        f.setdefault("repo_mttr_h", 0.0)
    fps = sorted({f.get("fingerprint") for f in findings if f.get("fingerprint")})
    if not fps:
        return findings
    rows = _rows(_ENRICH, {"fps": fps, "repo": repo}, "enrich")
    by = {r["fp"]: r for r in rows or []}
    for f in findings:
        r = by.get(f.get("fingerprint"))
        if r:
            f["seen_before"] = int(r["seen_before"])
            f["dismissed_before"] = bool(r["dismissed_before"])
            f["repo_mttr_h"] = _plain(float(r["repo_mttr_h"]))
    return findings


_RISING = """
SELECT repo, countIf(ts >= now64(3) - INTERVAL {h:UInt32} HOUR) AS findings_now,
       countIf(ts < now64(3) - INTERVAL {h:UInt32} HOUR) AS findings_prev,
       findings_now / greatest(findings_prev, 1) AS ratio
FROM aegis.findings WHERE ts >= now64(3) - INTERVAL {h2:UInt32} HOUR AND status IN ('new', 'still_open')
  AND agent != 'backfill'  -- backfill rows carry load time in ts; their real time is commit_ts
GROUP BY repo HAVING findings_now > 0 ORDER BY ratio DESC, findings_now DESC LIMIT 10"""

# filed/dismissed are distinct fingerprints so a dismissal logged both as a finding row and an action counts once.
# Backfill rows are history, not filings, so they are excluded.
_NOISY = """
WITH fr AS (SELECT fingerprint, any(rule_id) AS rule_id FROM aegis.findings GROUP BY fingerprint),
     dis AS (SELECT rule_id, fingerprint FROM aegis.findings WHERE status = 'dismissed'
             UNION ALL
             SELECT fr.rule_id AS rule_id, a.fingerprint AS fingerprint FROM aegis.actions AS a
             INNER JOIN fr ON fr.fingerprint = a.fingerprint WHERE a.kind = 'dismissed'),
     d AS (SELECT rule_id, uniqExact(fingerprint) AS dismissed FROM dis GROUP BY rule_id),
     f AS (SELECT rule_id, uniqExactIf(fingerprint, status = 'new') AS filed FROM aegis.findings
           WHERE agent != 'backfill' GROUP BY rule_id)
SELECT if(f.rule_id != '', f.rule_id, d.rule_id) AS rule_id, f.filed AS filed, d.dismissed AS dismissed,
       dismissed / greatest(filed, 1) AS dismiss_rate
FROM f FULL OUTER JOIN d ON d.rule_id = f.rule_id
WHERE filed > 0 OR dismissed > 0
ORDER BY dismiss_rate DESC, filed DESC LIMIT 10
SETTINGS join_use_nulls = 0"""

_REOPENED = """
SELECT repo, fingerprint, any(rule_id) AS rule_id, countIf(status = 'new') AS times
FROM aegis.findings WHERE agent != 'backfill'
GROUP BY repo, fingerprint HAVING times >= 2 ORDER BY times DESC LIMIT 20"""

# live agents only, unless backfill is all there is
_LATENCY = """
SELECT agent, quantiles(0.5, 0.95)(total_ms) AS q, count() AS scans FROM aegis.scans
WHERE agent != 'backfill' OR (SELECT countIf(agent != 'backfill') FROM aegis.scans) = 0
GROUP BY agent ORDER BY scans DESC"""


def insights(hours: int = 24) -> dict:
    """Fleet analytics for the warden:
    rising_repos  [{repo, findings_now, findings_prev, ratio}]
    noisy_rules   [{rule_id, filed, dismissed, dismiss_rate}]
    reopened      [{repo, fingerprint, rule_id, times}]
    agent_latency [{agent, p50_ms, p95_ms, scans}]"""
    out = {k: [] for k in NEUTRAL_INSIGHTS}
    h = max(int(hours or 24), 1)
    out["rising_repos"] = _clean(_rows(_RISING, {"h": h, "h2": 2 * h}, "insights.rising") or [])
    out["noisy_rules"] = _clean(_rows(_NOISY, None, "insights.noisy") or [])
    out["reopened"] = _clean(_rows(_REOPENED, None, "insights.reopened") or [])
    out["agent_latency"] = [{"agent": r["agent"], "p50_ms": _plain(float(r["q"][0])), "p95_ms": _plain(float(r["q"][1])),
                             "scans": int(r["scans"])} for r in _rows(_LATENCY, None, "insights.latency") or []]
    return out


_EVENTS = """
SELECT * FROM (
  SELECT toUnixTimestamp64Milli(ts) AS ms, 'scan' AS kind, agent, repo, sha, verdict, n_findings, total_ms, trigger,
         '' AS ref, '' AS fingerprint, toUInt32(0) AS latency_ms, '' AS session_url
  FROM aegis.scans WHERE trigger != 'backfill' ORDER BY ts DESC LIMIT {n:UInt32}
  UNION ALL
  SELECT toUnixTimestamp64Milli(ts) AS ms, toString(kind) AS kind, agent, repo, '' AS sha, '' AS verdict,
         toUInt32(0) AS n_findings, toUInt32(0) AS total_ms, '' AS trigger, ref, fingerprint, latency_ms, session_url
  FROM aegis.actions ORDER BY ts DESC LIMIT {n:UInt32}
) ORDER BY ms DESC LIMIT {n:UInt32}"""


def recent_events(n: int = 50) -> list[dict]:
    """Union of scans+actions ordered by ts desc, for the dashboard feed. `ts` is float epoch seconds."""
    out = []
    for r in _clean(_rows(_EVENTS, {"n": max(int(n), 1)}, "recent_events") or []):
        r["ts"] = r.pop("ms") / 1000
        out.append(r)
    return out


_POSTURE = """
SELECT toString(BUCKET(commit_ts)) AS week, repo, severity, uniqExact(fingerprint) AS n
FROM aegis.findings
WHERE status IN ('new', 'still_open')
  AND commit_ts >= (SELECT max(commit_ts) FROM aegis.findings) - INTERVAL {w:UInt32} WEEK
GROUP BY week, repo, severity ORDER BY week, repo, severity"""


_BUCKETS = {"week": "toStartOfWeek", "month": "toStartOfMonth", "quarter": "toStartOfQuarter"}


def posture_timeline(weeks: int = 52, bucket: str = "week") -> list[dict]:
    """[{week, repo, severity, n}]: distinct open fingerprints per commit week/month/quarter (`week` = bucket start;
    window of `weeks` ends at the newest commit)."""
    sql = _POSTURE.replace("BUCKET", _BUCKETS.get(bucket, "toStartOfWeek"))  # whitelisted function name
    return _clean(_rows(sql, {"w": max(int(weeks), 1)}, "posture_timeline") or [])


def repo_mttr() -> list[dict]:
    """[{repo, mttr_h, closed}]: avg hours issue_opened -> issue_closed per fingerprint, per repo."""
    sql = f"SELECT repo, avg(h) AS mttr_h, count() AS closed FROM ({_CYCLES.format(where='')}) GROUP BY repo ORDER BY repo"
    return _clean(_rows(sql, None, "repo_mttr") or [])


def fleet_counts() -> list[dict]:
    """[{repo, agent, opened, closed, open_now}] from aegis.actions."""
    sql = """SELECT repo, anyLast(agent) AS agent, countIf(kind = 'issue_opened') AS opened,
                    countIf(kind = 'issue_closed') AS closed, toUInt64(greatest(toInt64(opened) - toInt64(closed), 0)) AS open_now
             FROM aegis.actions GROUP BY repo ORDER BY repo"""
    return _clean(_rows(sql, None, "fleet_counts") or [])


def stats() -> dict:
    """{findings, scans, actions, repos, rules, query_ms}: table sizes plus round-trip latency of this query."""
    neutral = {"findings": 0, "scans": 0, "actions": 0, "repos": 0, "rules": 0, "query_ms": 0.0}
    t = time.perf_counter()
    rows = _rows("""SELECT (SELECT count() FROM aegis.findings) AS findings, (SELECT count() FROM aegis.scans) AS scans,
                           (SELECT count() FROM aegis.actions) AS actions,
                           (SELECT uniqExact(repo) FROM aegis.findings) AS repos,
                           (SELECT uniqExact(rule_id) FROM aegis.findings) AS rules""", None, "stats")
    if not rows:
        return neutral
    return {**_clean(rows)[0], "query_ms": round((time.perf_counter() - t) * 1000, 1)}


# ---------------------------------------------------------------- CLI
SELFTEST_REPO, SELFTEST_AGENT = "selftest/repo", "selftest"


def _selftest() -> int:
    assert enabled(), f"ClickHouse not reachable at {_host() or '(CLICKHOUSE_HOST unset)'}"
    assert init(), "init failed"
    fp = "st" + uuid.uuid4().hex[:10]
    f = {"rule_id": "aegis.selftest-rule", "path": "app.py", "start_line": 7, "severity": "ERROR",
         "cwe": ["CWE-89: SQL Injection"], "fingerprint": fp, "fix": "use params"}
    insert_scan(SELFTEST_AGENT, SELFTEST_REPO, "abc123", "000000", "manual", 1, 1, "unsafe", 120, 340)
    insert_findings(SELFTEST_AGENT, SELFTEST_REPO, "abc123", [f], commit_ts="2026-10-01T12:00:00Z")
    insert_findings(SELFTEST_AGENT, SELFTEST_REPO, "abc123", [f], status="dismissed", commit_ts=1759320000)
    now = _now()
    _insert("actions", [[now - timedelta(hours=1), SELFTEST_AGENT, SELFTEST_REPO, "issue_opened", "#1", fp, 900, ""],
                        [now, SELFTEST_AGENT, SELFTEST_REPO, "issue_closed", "#1", fp, 800, ""]], ACTION_COLS)
    t = time.perf_counter()
    e = enrich(SELFTEST_REPO, [{"fingerprint": fp}, {"fingerprint": "never-seen"}])
    print(f"enrich ({(time.perf_counter() - t) * 1000:.1f} ms):", e)
    assert e[0]["seen_before"] >= 1 and e[0]["dismissed_before"] is True and e[0]["repo_mttr_h"] > 0, e[0]
    assert e[1]["seen_before"] == 0 and e[1]["dismissed_before"] is False, e[1]
    ins = insights(24)
    print("insights:", {k: v[:3] for k, v in ins.items()})
    assert any(r["repo"] == SELFTEST_REPO for r in ins["rising_repos"]), "rising_repos misses selftest repo"
    assert any(r["rule_id"] == f["rule_id"] and r["dismissed"] >= 1 for r in ins["noisy_rules"]), "noisy_rules"
    assert any(r["agent"] == SELFTEST_AGENT for r in ins["agent_latency"]), "agent_latency"
    pt = posture_timeline()
    print(f"posture_timeline: {len(pt)} rows, e.g. {pt[:2]}")
    fc = fleet_counts()
    print("fleet_counts:", [r for r in fc if r["repo"] == SELFTEST_REPO])
    assert any(r["repo"] == SELFTEST_REPO and r["opened"] >= 1 and r["closed"] >= 1 for r in fc), "fleet_counts"
    print("repo_mttr:", [r for r in repo_mttr() if r["repo"] == SELFTEST_REPO])
    ev = recent_events(5)
    print("recent_events:", ev[:3])
    assert ev and ev[0]["repo"] == SELFTEST_REPO and isinstance(ev[0]["ts"], float), "recent_events"
    print("stats:", stats())
    print("SELFTEST OK")
    return 0


def _purge() -> int:
    assert enabled(), "ClickHouse not reachable"
    for t in ("scans", "findings", "actions"):
        _client().command(f"ALTER TABLE aegis.{t} DELETE WHERE repo = '{SELFTEST_REPO}'",
                          settings={"mutations_sync": 1})
    print("purged", SELFTEST_REPO, stats())
    return 0


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    cmd = argv[0] if argv else "stats"
    try:
        if cmd == "selftest":
            return _selftest()
        if cmd == "init":
            ok = init()
            print("init", "ok" if ok else "FAILED")
            return 0 if ok else 1
        if cmd == "stats":
            print(stats() if enabled() else "ClickHouse disabled/unreachable")
            return 0 if enabled() else 1
        if cmd == "purge-selftest":
            return _purge()
    except AssertionError as e:
        print("FAIL:", e)
        return 1
    print("usage: python -m aegis.ch selftest | init | stats | purge-selftest")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
