"""Exposure clock: how long has each vulnerability been live?

Per finding: `git blame` on the flagged lines gives the commit that put that code there; ClickHouse history (backfill +
every past scan) gives the first commit where Semgrep ever saw the same fingerprint. The older of the two wins:
introduced_sha / introduced_at / exposed_days. A finding introduced by the push being scanned gets exposed_days = 0,
which is the point of AEGIS: caught on day 0 instead of after months.

Fleet-wide (GET /exposure, GET /api/exposure): from ClickHouse history, how long fixed bugs lived before someone fixed
them (median, p90) and the longest-exposed bugs that are still open."""
from __future__ import annotations
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter
from . import ch

router = APIRouter()      # agent-facing, behind X-AEGIS-Key (mounted in server.py)
public = APIRouter()      # dashboard, read-only aggregate like the other /api/* routes


def _blame(workdir: Path, path: str, start: int, end: int) -> tuple[str, datetime, str] | None:
    """Oldest commit among the flagged lines: (sha, author time, author name)."""
    try:
        out = subprocess.run(["git", "-C", str(workdir), "blame", "--porcelain", "-L", f"{start},{end or start}", "--", path],
                             capture_output=True, text=True, timeout=20).stdout
    except Exception:  # noqa
        return None
    commits: dict[str, dict] = {}
    cur = None
    for line in out.splitlines():
        parts = line.split(" ")
        if len(parts) >= 3 and len(parts[0]) == 40 and all(c in "0123456789abcdef" for c in parts[0]):
            cur = commits.setdefault(parts[0], {})
        elif cur is not None and line.startswith("author-time "):
            cur["t"] = int(line.split(" ", 1)[1])
        elif cur is not None and line.startswith("author "):
            cur["a"] = line.split(" ", 1)[1]
    dated = [(c["t"], sha, c.get("a", "")) for sha, c in commits.items() if "t" in c and not sha.startswith("0000000")]
    if not dated:
        return None
    t, sha, author = min(dated)
    return sha, datetime.fromtimestamp(t, tz=timezone.utc), author


def _first_seen(repo: str, fingerprints: list[str]) -> dict[str, tuple[str, datetime]]:
    """Earliest commit in ClickHouse history per fingerprint (one query for all findings)."""
    if not fingerprints:
        return {}
    rows = ch._rows("SELECT fingerprint, argMin(sha, commit_ts) AS sha, min(commit_ts) AS t FROM aegis.findings "
                    "WHERE repo = {repo:String} AND fingerprint IN {fps:Array(String)} GROUP BY fingerprint",
                    {"repo": repo, "fps": fingerprints}, what="exposure first_seen") or []
    out = {}
    for r in rows:
        t = r["t"] if r["t"].tzinfo else r["t"].replace(tzinfo=timezone.utc)
        if t.year > 1971:  # commit_ts DEFAULT now() rows carry no history
            out[r["fingerprint"]] = (r["sha"], t)
    return out


def apply(workdir: Path, repo: str, findings: list[dict]) -> list[dict]:
    """Adds introduced_sha, introduced_at (ISO, UTC), introduced_by, exposed_days. Never raises."""
    now = datetime.now(timezone.utc)
    try:
        seen = _first_seen(repo, [f["fingerprint"] for f in findings if f.get("fingerprint")])
    except Exception:  # noqa
        seen = {}
    for f in findings:
        try:
            cands = []
            if f.get("start_line") and f.get("path") and not f.get("supply_chain"):
                b = _blame(workdir, f["path"], f["start_line"], f.get("end_line") or f["start_line"])
                if b:
                    cands.append(b)
            if f.get("fingerprint") in seen:
                sha, t = seen[f["fingerprint"]]
                cands.append((sha, t, ""))
            if not cands:
                continue
            sha, t, author = min(cands, key=lambda c: c[1])
            f["introduced_sha"] = sha
            f["introduced_at"] = t.isoformat(timespec="seconds")
            if author:
                f["introduced_by"] = author
            f["exposed_days"] = max(0, (now - t).days)
        except Exception:  # noqa
            continue
    return findings


def label(f: dict) -> str:
    """One line for Issues/alerts: 'Exposed for 416 days, since 3f2a1bc (2025-08-20).'"""
    if "exposed_days" not in f:
        return ""
    d = f["exposed_days"]
    since = f"since {f.get('introduced_sha', '')[:7]} ({f.get('introduced_at', '')[:10]})"
    return "New in this push: caught on day 0." if d == 0 else f"Exposed for {d:,} days, {since}."


FLEET_SQL = """
WITH life AS (
  SELECT repo, fingerprint, any(rule_id) AS rule_id, any(severity) AS severity, any(path) AS path,
         argMin(sha, commit_ts) AS first_sha, min(commit_ts) AS first_seen, max(commit_ts) AS last_seen
  FROM aegis.findings WHERE agent = 'backfill' GROUP BY repo, fingerprint),
heads AS (SELECT repo, max(commit_ts) AS head_ts FROM aegis.findings WHERE agent = 'backfill' GROUP BY repo)
SELECT {cols} FROM life JOIN heads USING repo {tail}
"""


def fleet(top: int = 10) -> dict:
    """How long bugs live without AEGIS: real commit history only (backfill rows carry the true commit date)."""
    summary = ch._rows(FLEET_SQL.format(cols="""
        countIf(last_seen < head_ts) AS fixed, countIf(last_seen >= head_ts) AS open,
        round(quantileIf(0.5)(dateDiff('day', first_seen, last_seen), last_seen < head_ts)) AS median_days_to_fix,
        round(quantileIf(0.9)(dateDiff('day', first_seen, last_seen), last_seen < head_ts)) AS p90_days_to_fix,
        round(quantileIf(0.5)(dateDiff('day', first_seen, now()), last_seen >= head_ts)) AS median_open_days,
        maxIf(dateDiff('day', first_seen, now()), last_seen >= head_ts) AS max_open_days""", tail=""),
        what="exposure fleet") or [{}]
    by_repo = ch._rows(FLEET_SQL.format(cols="""
        repo, countIf(last_seen < head_ts) AS fixed, countIf(last_seen >= head_ts) AS open,
        round(quantileIf(0.5)(dateDiff('day', first_seen, last_seen), last_seen < head_ts)) AS median_days_to_fix""",
        tail="GROUP BY repo ORDER BY fixed + open DESC"), what="exposure by repo") or []
    longest = ch._rows(FLEET_SQL.format(cols="""
        repo, rule_id, severity, path, first_sha, toString(toDate(first_seen)) AS since,
        dateDiff('day', first_seen, now()) AS exposed_days""",
        tail="WHERE last_seen >= head_ts AND severity = 'ERROR' ORDER BY exposed_days DESC LIMIT {top:UInt16}"),
        {"top": top}, what="exposure longest") or []
    return {"ch": ch.enabled(), **ch._clean(summary)[0], "by_repo": ch._clean(by_repo), "longest_open": ch._clean(longest)}


@router.get("/exposure", operation_id="fleet_exposure", tags=["exposure"],
            summary="Exposure clock: how long vulnerabilities lived before being fixed, and the longest-exposed open ones.")
def get_exposure(top: int = 10):
    return fleet(top)


@public.get("/api/exposure", tags=["dashboard"])
def api_exposure(top: int = 10):
    return fleet(top)
