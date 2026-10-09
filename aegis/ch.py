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
        for name in ("schema.sql", "views.sql"):  # views.sql: MVs + one-time guarded backfill (optional)
            p = config.ROOT / "clickhouse" / name
            if name != "schema.sql" and not p.exists():
                continue
            body = "\n".join(line.split("--", 1)[0] for line in p.read_text().splitlines())
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

# fps/rids are parallel arrays (fingerprint, rule_id). Rule dismiss rate = dismissed distinct fps of the rule /
# distinct fps filed by live agents (backfill is history, not filings); 0 when no live filings.
# exposure_days = now - earliest commit_ts of the fingerprint in this repo (any agent, incl. backfill).
_ENRICH = f"""
WITH f AS (SELECT fingerprint, countIf(ts < now64(3)) AS seen, countIf(status = 'dismissed' AND ts < now64(3)) AS dis,
                  -- exposure ignores the ts filter: backfill rows carry a load-time ts, their real time is commit_ts
                  countIf(repo = {{repo:String}}) AS here, minIf(commit_ts, repo = {{repo:String}}) AS first_ts
           FROM aegis.findings WHERE fingerprint IN {{fps:Array(String)}} GROUP BY fingerprint),
     d AS (SELECT fingerprint, 1 AS x FROM aegis.actions
           WHERE kind = 'dismissed' AND fingerprint IN {{fps:Array(String)}} GROUP BY fingerprint),
     m AS ({_CYCLES.format(where="AND repo = {repo:String}")}),
     r AS (SELECT avgOrDefault(h) AS ra FROM m),
     fr AS (SELECT fingerprint, any(rule_id) AS rr FROM aegis.findings
            WHERE rule_id IN {{rids:Array(String)}} GROUP BY fingerprint),
     dis AS (SELECT rule_id, fingerprint FROM aegis.findings WHERE status = 'dismissed' AND rule_id IN {{rids:Array(String)}}
             UNION ALL
             SELECT fr.rr AS rule_id, a.fingerprint AS fingerprint FROM aegis.actions AS a
             INNER JOIN fr ON fr.fingerprint = a.fingerprint WHERE a.kind = 'dismissed'),
     rd AS (SELECT rule_id, uniqExact(fingerprint) AS dismissed FROM dis GROUP BY rule_id),
     rf AS (SELECT rule_id, uniqExact(fingerprint) AS filed FROM aegis.findings
            WHERE agent != 'backfill' AND rule_id IN {{rids:Array(String)}} GROUP BY rule_id)
SELECT q.fp AS fp, f.seen AS seen_before, (f.dis > 0 OR d.x = 1) AS dismissed_before,
       if(m.fingerprint != '', m.h, r.ra) AS repo_mttr_h,
       if(rf.filed > 0, least(rd.dismissed / rf.filed, 1), 0) AS rule_dismiss_rate,
       if(f.here > 0, greatest(dateDiff('second', f.first_ts, now()), 0) / 86400, 0) AS exposure_days
FROM (SELECT tupleElement(p, 1) AS fp, tupleElement(p, 2) AS rid
      FROM (SELECT arrayJoin(arrayZip({{fps:Array(String)}}, {{rids:Array(String)}})) AS p)) AS q
LEFT JOIN f ON f.fingerprint = q.fp LEFT JOIN d ON d.fingerprint = q.fp LEFT JOIN m ON m.fingerprint = q.fp
LEFT JOIN rd ON rd.rule_id = q.rid LEFT JOIN rf ON rf.rule_id = q.rid
CROSS JOIN r
SETTINGS join_use_nulls = 0"""

_SEV_BASE = {"ERROR": 60, "WARNING": 35, "INFO": 15}


def _priority(f: dict, live: bool) -> tuple[int, list[str]]:
    """0-100 remediation priority from severity + fleet history (already on f). Pure Python, no I/O."""
    sev = str(f.get("severity") or "INFO").upper()
    p = float(_SEV_BASE.get(sev, 15))
    if not live:
        return int(p), ["ClickHouse offline: severity only"]
    if f.get("dismissed_before"):
        return 0, ["fleet dismissed as false positive"]
    why = [f"{sev} severity"]
    seen = int(f.get("seen_before") or 0)
    if seen > 0:
        p += 15
        why.append(f"seen {seen}× before in the fleet")
    days = float(f.get("exposure_days") or 0.0)
    if days >= 1:
        p += min(15.0, days / 30)
        why.append(f"exposed {int(days)} days")
    mttr = float(f.get("repo_mttr_h") or 0.0)
    if mttr > 24:
        p += 10
        why.append(f"repo fixes slowly (MTTR {round(mttr)} h)")
    rate = float(f.get("rule_dismiss_rate") or 0.0)
    if rate > 0:
        p -= 40 * rate
        why.append(f"noisy rule (dismissed {round(rate * 100)}%)")
    return int(round(max(0.0, min(100.0, p)))), why


def enrich(repo: str, findings: list[dict]) -> list[dict]:
    """Add seen_before (int), dismissed_before (bool), repo_mttr_h (float), rule_dismiss_rate (0-1),
    exposure_days (float), priority (0-100) and priority_reasons (list[str]) to each finding, in place and returned.
    One round trip for the whole list. Neutral (0/False/0.0, priority from severity only) when ClickHouse is unavailable."""
    for f in findings:
        f.setdefault("seen_before", 0)
        f.setdefault("dismissed_before", False)
        f.setdefault("repo_mttr_h", 0.0)
        f.setdefault("rule_dismiss_rate", 0.0)
        f.setdefault("exposure_days", 0.0)
    pairs = sorted({(f["fingerprint"], str(f.get("rule_id") or "")) for f in findings if f.get("fingerprint")})
    rows = None
    if pairs:
        rows = _rows(_ENRICH, {"fps": [p[0] for p in pairs], "rids": [p[1] for p in pairs], "repo": repo}, "enrich")
    live = rows is not None if pairs else enabled()
    by = {r["fp"]: r for r in rows or []}
    for f in findings:
        r = by.get(f.get("fingerprint"))
        if r:
            f["seen_before"] = int(r["seen_before"])
            f["dismissed_before"] = bool(r["dismissed_before"])
            f["repo_mttr_h"] = _plain(float(r["repo_mttr_h"]))
            f["rule_dismiss_rate"] = _plain(float(r["rule_dismiss_rate"]))
            f["exposure_days"] = _plain(float(r["exposure_days"]))
        f["priority"], f["priority_reasons"] = _priority(f, live)
    return findings


def rank(findings: list[dict]) -> list[dict]:
    """New list, highest priority first (stable; findings without priority sort last)."""
    return sorted(findings, key=lambda f: -int(f.get("priority") or 0))


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


# ---------------------------------------------------------------- real-time rollups (clickhouse/views.sql)
FUNNEL_STAGES = ["detected", "issue_opened", "pr_opened", "verified", "issue_closed"]

# Per (repo, fingerprint): windowFunnel depth over detected -> issue -> PR -> verified -> closed, plus the first time each
# stage was reached; GROUPING SETS gives per-repo rows and the fleet total in one round trip.
_FUNNEL = """
WITH ev AS (
  SELECT repo, fingerprint, ts, toUInt8(0) AS s FROM aegis.findings
  WHERE status = 'new' AND agent != 'backfill' AND fingerprint != '' AND ts >= now64(3) - INTERVAL {h:UInt32} HOUR
  UNION ALL
  SELECT repo, fingerprint, ts, toUInt8(multiIf(kind = 'issue_opened', 1, kind = 'pr_opened', 2, kind = 'verified', 3, 4))
  FROM aegis.actions
  WHERE kind IN ('issue_opened', 'pr_opened', 'verified', 'issue_closed') AND fingerprint != ''
    AND ts >= now64(3) - INTERVAL {h:UInt32} HOUR),
per AS (
  SELECT repo, fingerprint, windowFunnel({win:UInt64})(toDateTime(ts), s = 0, s = 1, s = 2, s = 3, s = 4) AS lvl,
         minIf(toUnixTimestamp64Milli(ts), s = 0) AS t0, minIf(toUnixTimestamp64Milli(ts), s = 1) AS t1,
         minIf(toUnixTimestamp64Milli(ts), s = 2) AS t2, minIf(toUnixTimestamp64Milli(ts), s = 3) AS t3,
         minIf(toUnixTimestamp64Milli(ts), s = 4) AS t4
  FROM ev GROUP BY repo, fingerprint)
SELECT grouping(repo) AS total, toString(repo) AS repo_name,
       countIf(lvl >= 1) AS n0, countIf(lvl >= 2) AS n1, countIf(lvl >= 3) AS n2, countIf(lvl >= 4) AS n3,
       countIf(lvl >= 5) AS n4,
       medianIf(greatest(t1 - t0, 0) / 1000, lvl >= 2) AS m1, medianIf(greatest(t2 - t1, 0) / 1000, lvl >= 3) AS m2,
       medianIf(greatest(t3 - t2, 0) / 1000, lvl >= 4) AS m3, medianIf(greatest(t4 - t3, 0) / 1000, lvl >= 5) AS m4
FROM per GROUP BY GROUPING SETS ((repo), ()) HAVING n0 > 0 ORDER BY total DESC, n0 DESC"""


def fix_funnel(hours: int = 24 * 30) -> dict:
    """{stages: [{stage, n, median_s_from_prev}], per_repo: [{repo, detected, issue_opened, pr_opened, verified,
    issue_closed}], query_ms}: how far each live finding got through remediation (windowFunnel, ordered stages within
    the window). Backfill history is excluded. Neutral (empty lists) on failure."""
    h = max(int(hours or 1), 1)
    t = time.perf_counter()
    rows = _rows(_FUNNEL, {"h": h, "win": h * 3600}, "fix_funnel") or []
    ms = round((time.perf_counter() - t) * 1000, 1)
    stages, per_repo = [], []
    for r in _clean(rows):
        if int(r["total"]) == 1:
            stages = [{"stage": s, "n": int(r[f"n{i}"]), "median_s_from_prev": None if i == 0 else
                       (r[f"m{i}"] if int(r[f"n{i}"]) else None)} for i, s in enumerate(FUNNEL_STAGES)]
        else:
            per_repo.append({"repo": r["repo_name"], **{s: int(r[f"n{i}"]) for i, s in enumerate(FUNNEL_STAGES)}})
    return {"stages": stages, "per_repo": per_repo, "hours": h, "query_ms": ms}


_POSTURE_FAST = """
SELECT toString(BUCKET(day)) AS week, repo, severity, uniqMerge(fps) AS n
FROM aegis.posture_daily
WHERE day >= (SELECT max(day) FROM aegis.posture_daily) - {d:UInt32}
GROUP BY week, repo, severity HAVING n > 0 ORDER BY week, repo, severity"""


def posture_fast(days: int = 7300, bucket: str = "month") -> dict:
    """{rows: [{week, repo, severity, n}], query_ms, ok}: same rows as posture_timeline but read from the
    posture_daily rollup (AggregatingMergeTree fed by posture_daily_mv), so it scans ~1k pre-aggregated rows instead of
    every finding. n = approx distinct open fingerprints (uniq). ok=False when the rollup is missing/unreachable."""
    sql = _POSTURE_FAST.replace("BUCKET", _BUCKETS.get(bucket, "toStartOfMonth"))  # whitelisted function name
    t = time.perf_counter()
    rows = _rows(sql, {"d": max(int(days or 1), 1)}, "posture_fast")
    return {"rows": _clean(rows or []), "query_ms": round((time.perf_counter() - t) * 1000, 1), "ok": rows is not None}


# per (agent, kind): peak actions/minute in the recent window vs the median of that agent's own active minutes over the
# trailing 7 days; any `denied` action in the window is always an anomaly. SummingMergeTree may be unmerged -> sum(n).
_ANOMALIES = """
WITH m AS (SELECT agent, kind, minute, sum(n) AS n FROM aegis.agent_activity_1m
           WHERE minute >= now() - INTERVAL 7 DAY GROUP BY agent, kind, minute)
SELECT toString(agent) AS agent, toString(kind) AS kind,
       maxIf(n, minute >= now() - INTERVAL {m:UInt32} MINUTE) AS recent,
       sumIf(n, minute >= now() - INTERVAL {m:UInt32} MINUTE) AS recent_total,
       medianIf(n, minute < now() - INTERVAL {m:UInt32} MINUTE) AS baseline,
       countIf(minute < now() - INTERVAL {m:UInt32} MINUTE) AS base_minutes
FROM m GROUP BY agent, kind
HAVING recent > 0 AND (kind = 'denied' OR (base_minutes >= 3 AND recent > 3 * baseline)
                       OR (base_minutes < 3 AND recent >= {burst:UInt32}))
ORDER BY kind = 'denied' DESC, recent DESC LIMIT 20"""


def agent_anomalies(minutes: int = 60, burst: int = 20) -> list[dict]:
    """[{agent, kind, recent, baseline, reason}] from agent_activity_1m: agents whose peak actions/minute in the last
    `minutes` exceed 3x their own trailing median (active minutes, 7 days), brand-new agents bursting >= `burst`/min,
    or any `denied` action. [] when all agents are nominal or ClickHouse is unavailable."""
    out = []
    for r in _clean(_rows(_ANOMALIES, {"m": max(int(minutes or 1), 1), "burst": max(int(burst), 1)},
                          "agent_anomalies") or []):
        rec, base = int(r["recent"]), float(r["baseline"] or 0)
        if r["kind"] == "denied":
            reason = f"{int(r['recent_total'])} denied action(s) in {minutes} min"
        elif int(r["base_minutes"]) >= 3:
            reason = f"{rec}/min vs median {base:g}/min ({rec / max(base, 1e-9):.1f}x)"
        else:
            reason = f"new burst: {rec}/min with no baseline"
        out.append({"agent": r["agent"], "kind": r["kind"], "recent": rec, "baseline": round(base, 2),
                    "recent_total": int(r["recent_total"]), "reason": reason})
    return out


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
