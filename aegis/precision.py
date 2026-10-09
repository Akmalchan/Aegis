"""Rule precision from fleet memory. Every triage decision lands in ClickHouse as an action on a fingerprint:
issue_opened / verified = confirmed true positive, dismissed = false positive. Joining actions to findings by
fingerprint gives per-rule precision. Rules with >= MIN_DECISIONS and precision < DEMOTE_BELOW are demoted to INFO in
scan output (still reported, never blocking). GET /rules/precision exposes the table."""
import threading, time
from fastapi import APIRouter, Query
from . import ch

router = APIRouter()
MIN_DECISIONS = 5
DEMOTE_BELOW = 0.3
CACHE_S = 60
_cache: dict = {"at": 0.0, "hours": 0, "rows": {}}
_lock = threading.Lock()

# one decision per (fingerprint, verdict): repeated issue_opened rows for the same finding count once
_SQL = """
SELECT f.rule_id AS rule_id,
       uniqExactIf(a.fingerprint, a.kind IN ('issue_opened', 'verified')) AS confirmed,
       uniqExactIf(a.fingerprint, a.kind = 'dismissed') AS dismissed
FROM aegis.actions AS a
INNER JOIN (SELECT fingerprint, any(rule_id) AS rule_id FROM aegis.findings
            WHERE fingerprint != '' GROUP BY fingerprint) AS f ON f.fingerprint = a.fingerprint
WHERE a.ts > now64(3) - toIntervalHour({hours:UInt32})
  AND a.kind IN ('issue_opened', 'verified', 'dismissed') AND a.fingerprint != ''
GROUP BY rule_id
ORDER BY rule_id
"""


def table(hours: int = 168) -> dict[str, dict]:
    """{rule_id: {confirmed, dismissed, decisions, precision}}; {} when ClickHouse is off. Cached 60 s."""
    hours = max(1, min(int(hours), 24 * 365))
    with _lock:
        if _cache["hours"] == hours and time.monotonic() - _cache["at"] < CACHE_S:
            return _cache["rows"]
    rows = ch._rows(_SQL, {"hours": hours}, "rule precision") or []
    out = {}
    for r in rows:
        c, d = int(r["confirmed"]), int(r["dismissed"])
        n = c + d
        out[r["rule_id"]] = {"confirmed": c, "dismissed": d, "decisions": n, "precision": round(c / n, 3) if n else None}
    with _lock:
        _cache.update(at=time.monotonic(), hours=hours, rows=out)
    return out


def apply(findings: list[dict], hours: int = 168) -> list[dict]:
    """Attach rule_precision; demote noisy rules to INFO with demoted=true. Never raises."""
    try:
        t = table(hours)
    except Exception:  # noqa
        return findings
    for f in findings:
        p = t.get(f.get("rule_id", ""))
        if not p:
            continue
        f["rule_precision"] = p
        if p["decisions"] >= MIN_DECISIONS and p["precision"] is not None and p["precision"] < DEMOTE_BELOW \
                and f.get("severity") != "INFO":
            f["original_severity"] = f["severity"]
            f["severity"] = "INFO"
            f["demoted"] = True
    return findings


@router.get("/rules/precision", operation_id="rule_precision",
            summary="Per-rule precision from fleet triage history in ClickHouse (confirmed = issue_opened/verified, "
                    "dismissed = false positive). Rules with >=5 decisions and precision < 0.3 are demoted to INFO in scans.")
def rule_precision(hours: int = Query(168, ge=1, le=8760)):
    t = table(hours)
    rules = [{"rule_id": k, **v, "demoted": v["decisions"] >= MIN_DECISIONS and (v["precision"] or 0) < DEMOTE_BELOW}
             for k, v in t.items()]
    rules.sort(key=lambda r: (r["precision"] if r["precision"] is not None else 1, -r["decisions"]))
    return {"hours": hours, "clickhouse": ch.enabled(), "min_decisions": MIN_DECISIONS, "demote_below": DEMOTE_BELOW,
            "n_rules": len(rules), "rules": rules}
