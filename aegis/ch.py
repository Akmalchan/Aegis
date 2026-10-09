"""Contract 2: ClickHouse access layer. STUB — stream C (subagent C1) replaces the bodies, signatures stay.
Until then every function is a no-op that returns neutral values, so the scanner works without ClickHouse."""
from __future__ import annotations

# Config (stream C fills in): CLICKHOUSE_HOST, CLICKHOUSE_PORT (8443 cloud / 8123 docker), CLICKHOUSE_USER,
# CLICKHOUSE_PASSWORD, CLICKHOUSE_SECURE ("1" for cloud). Read them from aegis.config.


def enabled() -> bool:
    """True when a ClickHouse connection is configured and reachable."""
    return False


def insert_scan(agent: str, repo: str, sha: str, base_sha: str, trigger: str, n_files: int,
                n_findings: int, verdict: str, semgrep_ms: int, total_ms: int) -> None:
    """One row into aegis.scans."""


def insert_findings(agent: str, repo: str, sha: str, findings: list[dict], status: str = "new",
                    commit_ts=None) -> None:
    """One row per finding into aegis.findings. `findings` are scanner dicts (rule_id, path, start_line, severity, cwe,
    fingerprint, fix?). `status` in new|still_open|resolved|dismissed."""


def insert_action(agent: str, repo: str, kind: str, ref: str, fingerprint: str = "",
                  latency_ms: int = 0, session_url: str = "") -> None:
    """One row into aegis.actions."""


def enrich(repo: str, findings: list[dict]) -> list[dict]:
    """Add seen_before (int), dismissed_before (bool), repo_mttr_h (float) to each finding, in place and returned.
    Stub: zeros/False."""
    for f in findings:
        f.setdefault("seen_before", 0)
        f.setdefault("dismissed_before", False)
        f.setdefault("repo_mttr_h", 0.0)
    return findings


def insights(hours: int = 24) -> dict:
    """Fleet analytics for the warden:
    rising_repos  [{repo, findings_now, findings_prev, ratio}]
    noisy_rules   [{rule_id, filed, dismissed, dismiss_rate}]
    reopened      [{repo, fingerprint, rule_id, times}]
    agent_latency [{agent, p50_ms, p95_ms, scans}]"""
    return {"rising_repos": [], "noisy_rules": [], "reopened": [], "agent_latency": []}


def recent_events(n: int = 50) -> list[dict]:
    """Union of scans+actions ordered by ts desc, for the dashboard feed."""
    return []
