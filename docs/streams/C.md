# Stream C — ClickHouse + data + demo (owner: person 3)

Paste this whole file as the first message of a Claude Code session opened in this repo.

## Context for Claude

Read `PLAN.md`, `clickhouse/schema.sql` (Contract 2 — frozen), `aegis/ch.py` (stub whose signatures you keep), `aegis/server.py` (dashboard HTML lives there today; you move it to `aegis/dashboard.py` + `aegis/static/`), `aegis/scanner.py` (`checkout`, `run_semgrep`, `fingerprint` — reuse for backfill).

ClickHouse facts: Python `clickhouse-connect` (`uv add clickhouse-connect`); Cloud: `get_client(host="X.clickhouse.cloud", port=8443, username="default", password=...)`; docker: `clickhouse/clickhouse-server` on 8123, user `default`, no password unless set. Inserts: `client.insert("aegis.findings", rows, column_names=[...])`; use `settings={"async_insert": 1, "wait_for_async_insert": 1}` on the client. Queries: `client.query(sql).result_rows` / `.named_results()`. Prize criteria: data scale, latency, insights that directly drive detection/remediation/monitoring.

GitHub: `gh` CLI is logged in as `andriidrok1`; `gh repo create <name> --public --source=. --push`.

## Spawn 3 subagents IN PARALLEL (Agent tool, one message), then integrate

### C1 — store (owns `clickhouse/docker-compose.yml`, `aegis/ch.py`, CH section of `.env.example`, `clickhouse/README.md`)
- Replace stub bodies in `aegis/ch.py` keeping every signature. Config from env: `CLICKHOUSE_HOST`, `CLICKHOUSE_PORT`, `CLICKHOUSE_USER`, `CLICKHOUSE_PASSWORD`, `CLICKHOUSE_SECURE`. `enabled()` = host set and `SELECT 1` works (cache the client, never crash the scanner if CH is down → log and return neutral values).
- `init()` applies `schema.sql` statements one by one (`CREATE DATABASE`, 3 tables).
- `enrich`: one query per call (not per finding): `seen_before` = count of rows in `findings` with same fingerprint across all repos before now; `dismissed_before` = exists row with status='dismissed' for that fingerprint; `repo_mttr_h` = avg hours between `issue_opened` and `issue_closed` actions with the same fingerprint in that repo (0 if none).
- `insights(hours)`: SQL for the 4 lists in the docstring (rising = findings in last `hours` vs previous window; noisy = rules with highest dismissed/filed ratio; reopened = fingerprints with ≥2 `new` rows in a repo; agent_latency = quantiles(0.5,0.95) of `total_ms` from `scans` per agent).
- `recent_events(n)`: union of scans and actions for the dashboard.
- `python -m aegis.ch selftest`: init, insert one of each, enrich a fake finding, insights, print counts.
- `clickhouse/docker-compose.yml`: local fallback on 8123 with `aegis` db.
- Done when: selftest passes against docker; `.env.example` documents both Cloud and docker settings.

### C2 — data (owns `clickhouse/backfill.py`, `fleet/make-targets.sh`, `demo-target/`, demo branches)
- `fleet/make-targets.sh`: creates GitHub repos `aegis-target-01..09` under `andriidrok1` (public): 01–03 Flask (from `demo-target/app.py` with small variations), 04–06 Express (`demo-target/js/` — write a small clean Express app), 07–09 forks/clones of small public OSS apps (pick 3 tiny Flask/Express sample apps on GitHub; clone, push as new repos — note the source in README). Idempotent. Updates `fleet.json` so agents 01/02/03 each own 3 repos. In target-01 also push branches `demo/vuln` (vulnerable `app.py` from `_variants/app_vulnerable.py`) and `demo/clean` (fixed), so a demo push is `git push origin demo/vuln:main --force`-style (document the exact commands in `docs/demo-checklist.md` section "push commands").
- `demo-target/js/_variants/`: vulnerable + fixed Express variants (exec with concatenation, hardcoded token, reflected XSS) matching stream B's JS rules.
- `clickhouse/backfill.py`: for a list of repos (the 9 targets + 3 medium public OSS Python/JS repos with long histories, e.g. pick ones with 300–1500 commits), clone once, iterate commits (`git rev-list --reverse`, cap per repo with `--max-commits`), for each commit `git checkout`, run `scanner.run_semgrep` with bundled rules only (fast; add `--timeout 30`), insert findings with `status="new"` and `commit_ts` = commit author date, `agent="backfill"`, trigger `backfill` into `scans`. Parallelize across repos with a process pool. Print progress and final counts.
- Done when: `SELECT count() FROM aegis.findings` > 10 000; 9 repos exist; demo branches exist; `fleet.json` updated.

### C3 — face (owns `aegis/dashboard.py`, `aegis/static/`, `README.md`, `SUBMISSION.md`, `docs/demo-checklist.md`)
- Move the dashboard out of `server.py` into `aegis/dashboard.py` (APIRouter mounted at `/` and `/api/*`) reading from `ch.py` (fallback to `state.recent_events` when CH disabled). Panels: fleet cards (agents → repos, open/fixed counts from `actions`), live feed (scans + actions, auto-refresh 2 s, highlight WAKE/unsafe/issue_opened/pr_opened/denied), **posture timeline** (findings per week per repo from backfill, stacked by severity — a simple inline SVG or Chart.js from cdnjs), agent latency p50/p95, MTTR per repo. Dark, monospace, readable from 3 m on a projector. Big "WAKE" flash when a new scan arrives.
- `README.md`: rewrite for judges: one-paragraph idea, architecture diagram (from PLAN.md), "sponsor tools and exactly how each is used" table (Guild: hosting, webhook+cron triggers, custom integration, credential policies, skills, sub-agents, evals; Semgrep: baseline diff, bundled rules with fix, registry packs, supply chain; ClickHouse: schema, enrichment, insights, backfill scale; OpenAI via Guild LLM provider), how to run, team.
- `SUBMISSION.md`: text for the form fields (name, one-liner, description ≤ 300 words, tools used, sponsor prizes to tick: Pi, Guild, Semgrep, ClickHouse), team names/emails placeholders.
- `docs/demo-checklist.md`: pre-flight (tunnel up, integration test, triggers active, CH reachable, dashboard on projector), the push commands, the 3-minute script from PLAN.md, fallback steps.
- Done when: dashboard renders timeline from backfill data; README/SUBMISSION ready to paste.

## After subagents finish (you, the session owner)
1. `git pull --rebase`; `uv sync`; run selftest, backfill, open dashboard.
2. Record the 3-minute video at ~15:30 with streams A+B (OBS/screen record, 1080p, mic).
3. Commit after every working step: `git add -A && git commit -m "C: ..." && git pull --rebase && git push`.
