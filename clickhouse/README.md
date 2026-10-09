# ClickHouse: AEGIS fleet memory

**In one sentence:** ClickHouse is the fleet's memory. Every scan, finding and agent action is written to it as it happens, and the agents read it back to decide *what is real, what to fix first, and whether the fleet itself is behaving*.

| ClickHouse prize criterion | What AEGIS does with it |
|---|---|
| **Data scale** | Semgrep replayed over the git history of the fleet plus long-lived OSS projects (2005 to 2026), plus every live agent run. See `uv run python -m aegis.ch stats`. |
| **Latency** | One query per scan for enrichment (tens of ms on Cloud); the dashboard reads pre-aggregated materialized views, so the 20-year posture timeline answers in ~45 ms. |
| **Insights that drive action** | Detection: fleet-dismissed false positives are suppressed everywhere. Remediation: a ClickHouse-computed `priority` decides which finding gets the one fix per push. Monitoring: the warden's drift report, the fix funnel, and an anomaly watch over the agents themselves. |

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
| `enrich()` → `priority`, `priority_reasons` | **scanner response, remediator (remediation)** | Same single query also returns `rule_dismiss_rate` (share of this rule's fingerprints the fleet dismissed) and `exposure_days` (days since the fingerprint first appeared in this repo's history). Python turns that into a 0-100 `priority`: severity base (ERROR 60 / WARNING 35 / INFO 15), +15 if it recurred in the fleet, up to +15 for exposure time, +10 if the repo fixes slowly (MTTR > 24 h), minus up to 40 for a noisy rule; a fleet-dismissed finding is 0. `priority_reasons` explains it in words ("seen 25× before in the fleet", "exposed 7622 days"). `/scan/diff` and `/scan/full` return findings sorted by priority (`ch.rank`), so the one finding the remediator fixes per push is the one fleet history says matters most. |
| `fix_funnel(hours)` | dashboard "Fix funnel" (remediation) | `windowFunnel` over live findings and actions per (repo, fingerprint): detected → issue_opened → pr_opened → verified → issue_closed, with counts and the median time between stages. Shows where findings stall. |
| `posture_fast(days)` | dashboard timeline | Reads the `posture_daily` rollup instead of raw findings, same output as `posture_timeline`. |
| `agent_anomalies(minutes)` | dashboard "Agent watch" (monitoring) | From the `agent_activity_1m` rollup: agents whose actions per minute exceed 3× their own trailing median, new agents bursting, or any `denied` action (a sentinel trying to touch a repo it does not own). ClickHouse watches the AI agents, not just the code. |

## Real-time rollups (`views.sql`)

`aegis.ch init` applies `schema.sql` and then `views.sql`. The base tables stay frozen; everything here is additive and idempotent.

| Object | Engine | Fed by | Used for |
|---|---|---|---|
| `posture_daily` + `posture_daily_mv` | AggregatingMergeTree (`uniqState(fingerprint)`, `countState()`) per (commit day, repo, severity) | every insert into `findings` | 20-year posture timeline without scanning raw rows |
| `agent_activity_1m` + `agent_activity_mv` | SummingMergeTree per (agent, kind, minute) | every insert into `actions` | agent anomaly watch |
| `mv_backfill` | MergeTree marker table | `init` | guarantees the one-time history backfill of the rollups runs once |

Materialized views are updated on insert, so the dashboard and the agents always read current aggregates with no batch job.

## How the memory changes what agents do

- **Detection: false positives are suppressed across the fleet.** If any agent, or any human, has dismissed a fingerprint, `enrich()` returns `dismissed_before=True` for it everywhere. The scanner keeps the finding in the report but does not count it toward an `unsafe` verdict, so the same false positive is never filed twice, in any repo.
- **Remediation: priority.** Each finding arrives with a ClickHouse-computed `priority` and the reasons for it, and the scanner returns findings highest priority first. The remediator fixes one finding per push, so fleet history (recurrence, exposure time, repo MTTR, rule noise) decides which one gets the PR.
- **Remediation: funnel.** `fix_funnel()` shows how many findings reached each stage (Issue, PR, verified, closed) and how long each step took, so a stuck stage is visible at a glance.
- **Monitoring: the agents themselves.** `agent_anomalies()` flags an agent that suddenly writes far more than usual or hits a policy `denied`.
- **Monitoring: drift.** The warden's cron run calls `insights()` and reports rising repos, rules worth retuning (high `dismiss_rate`), findings that keep coming back, and agents that are getting slow (p95).

Latency: on the local docker server `enrich()` is about 35 ms for a whole scan's findings and `stats()` under 10 ms. On ClickHouse Cloud (us-west-2, from San Francisco) a warm `enrich()` is ~70-180 ms, mostly network round trip; `/api/timeline` from the rollup ~45 ms, `/api/funnel` ~70 ms, `/api/anomalies` ~50 ms. Everything runs in one query per call, so latency does not grow with the number of findings.

Known quirk: backfill rows carry their load time in `ts` (their real time is `commit_ts`), so time-window insights (`rising_repos`, `reopened`, `noisy_rules`) exclude `agent='backfill'`, and the timeline and `exposure_days` use `commit_ts`.
