# ClickHouse: AEGIS fleet memory

Every scan, every finding and every agent action in the fleet goes into ClickHouse, and the agents read it back. Before a sentinel posts a verdict, it checks what the whole fleet already knows about each finding. The warden's drift report and the dashboard are SQL over the same three tables.

Schema (`schema.sql`, frozen contract): `aegis.scans` (one row per scan: agent, repo, sha, trigger, verdict, semgrep_ms, total_ms), `aegis.findings` (one row per finding per scan: fingerprint, rule, severity, CWE, `commit_ts`, status `new|still_open|resolved|dismissed`), `aegis.actions` (one row per agent action: `issue_opened`, `issue_closed`, `pr_opened`, `dismissed`, `denied` and so on, plus latency and Guild session URL). All three are MergeTree tables. `findings` is ordered by `(repo, fingerprint, ts)`, so a fingerprint lookup reads only a few granules even with millions of rows.

## Run it

```bash
# local server (same schema works on Cloud)
docker compose -f clickhouse/docker-compose.yml up -d     # :8123 HTTP, :9000 native, user default / aegis
cp .env.example .env                                       # docker settings are the default block

# ClickHouse Cloud instead: in .env set
#   CLICKHOUSE_HOST=xxxx.us-east-1.aws.clickhouse.cloud  CLICKHOUSE_PORT=8443  CLICKHOUSE_SECURE=1
#   CLICKHOUSE_USER=default  CLICKHOUSE_PASSWORD=<from the Cloud console>

uv run python -m aegis.ch init             # apply schema.sql (idempotent)
uv run python -m aegis.ch selftest         # write one of each row, check enrich/insights end to end
uv run python -m aegis.ch purge-selftest   # delete the selftest/repo rows
uv run python -m aegis.ch stats            # row counts and query latency
uv run python clickhouse/backfill.py       # Semgrep over the full git history of the target repos -> findings
```

If `CLICKHOUSE_HOST` is empty or the server cannot be reached, `aegis/ch.py` logs a warning and returns neutral values (0, False, empty lists), so the scanner keeps working. It re-probes the server every 30 s. Each process gets its own cached client, which makes it safe to call from the backfill process pool. Inserts use `async_insert=1, wait_for_async_insert=1`, so many small writes from many agents are batched on the server side.

## What each function computes

| Function | Used by | Query |
|---|---|---|
| `insert_scan / insert_findings / insert_action` | scanner, `POST /actions`, backfill | Batched inserts, one call per scan. A backfill commit with hundreds of findings is still one insert. |
| `enrich(repo, findings)` | **scanner, before the verdict (detection)** | One round trip for the whole finding list (`fingerprint IN {fps:Array(String)}`). For each fingerprint it returns `seen_before`, the number of earlier rows with that fingerprint in any repo; `dismissed_before`, true if any repo dismissed it (a finding row or a `dismissed` action); and `repo_mttr_h`, the average hours from `issue_opened` to `issue_closed` for this fingerprint in this repo, or the repo-wide average when there is no history for it. |
| `insights(hours)` | warden drift report, `GET /insights` (monitoring) | `rising_repos`: finding rows in the last `hours` compared with the previous window, ranked by ratio. `noisy_rules`: distinct dismissed fingerprints divided by distinct filed fingerprints, per rule. `reopened`: fingerprints filed as `new` 2 or more times in the same repo. `agent_latency`: `quantiles(0.5, 0.95)(total_ms)` per agent. |
| `posture_timeline(weeks)` | dashboard timeline | Distinct open fingerprints per `toStartOfWeek(commit_ts)`, repo and severity. The data is the backfilled git history, so this is the security posture over real time, not insert time. |
| `repo_mttr()` | dashboard (remediation) | Mean time to remediate per repo, from issue_opened to issue_closed pairs. |
| `fleet_counts()` | dashboard fleet cards | Issues opened, closed and still open per repo, from `actions`. |
| `recent_events(n)` | dashboard live feed | `UNION ALL` of scans and actions ordered by `ts`. Backfill scans are excluded. |
| `stats()` | dashboard header | Row counts for the 3 tables, number of distinct repos and rules, and query round-trip time in ms. |

## How the memory changes what agents do

- **Detection: false positives are suppressed across the fleet.** If any agent, or any human, has dismissed a fingerprint, `enrich()` returns `dismissed_before=True` for it everywhere. The scanner keeps the finding in the report but does not count it toward an `unsafe` verdict, so the same false positive is never filed twice, in any repo.
- **Remediation: priority.** `repo_mttr_h` and `seen_before` reach the triage agent with each finding. A finding in a repo whose issues stay open for days, or one that has already appeared across the fleet, goes to the top and gets a PR first.
- **Monitoring: drift.** The warden's cron run calls `insights()` and reports rising repos, rules worth retuning (high `dismiss_rate`), findings that keep coming back, and agents that are getting slow (p95).

Latency on the local docker server: `enrich()` is about 35 ms for a whole scan's findings, and `stats()` is under 10 ms. Everything runs in one query per call, so latency does not grow with the number of findings.
