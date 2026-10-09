# AEGIS: an autonomous security fleet for GitHub

> Cyberdefense Hackathon · 2026-10-09 · Guild.ai · Semgrep · ClickHouse

AI agents write code faster than any security team can read it, so we built a security team that scales the same way.
AEGIS is a fleet of autonomous security agents hosted on **Guild.ai**. Each agent owns three GitHub repositories.
On every push or pull request the agent wakes up and asks our **Semgrep** scanner one question: did this change make
the repo unsafe? Then it acts on the answer. If the change is **unsafe**, the agent sets a red commit status, files
a GitHub Issue explaining the impact and the fix, and opens a PR with the patch. If it is **safe**, the agent sets a
green status, approves the PR, and closes any Issue whose finding has disappeared. **ClickHouse** records every scan,
finding and action across the fleet, and that history feeds back into detection: a false positive dismissed once is
suppressed everywhere, findings are prioritised by each repo's time to remediate, and a warden agent writes drift
reports. Today the fleet is 3 agents and 10 repos. Going to 100 agents and 300 repos means editing one config file.

## Architecture

```
push / PR ─▶ GitHub ─▶ Guild webhook trigger (repo=R) ─▶ aegis-sentinel-NN (Guild, 3 repos each)
                                                            │ scan_diff(repo, before, after)  ← Guild custom integration → our scanner
                                                            │ safe   → commit status ✅, approve PR, close resolved issues
                                                            │ unsafe → commit status ❌ → triage → Issue + PR with patch
                                                            │ every action → POST /actions (ClickHouse)
cron ─▶ Guild time trigger ─▶ aegis-warden: scan_full all repos + GET /insights → drift report, email on critical
policies: sentinel-NN may only write to its 3 repos (Guild credential proxy enforces it)
skills:   aegis~security-review, aegis~remediation-playbook

scanner (FastAPI, the only non-Guild piece): Semgrep --baseline-commit, bundled rules with fix:, p/security-audit, p/secrets
ClickHouse: scans / findings / actions  → enrichment + insights + dashboard. Backfill = Semgrep over the full git history of the fleet.
```

The scanner runs outside Guild because the Guild sandbox has no shell and no outbound fetch, so Semgrep can't run
inside an agent. Agents reach the scanner through a proper Guild custom integration: the OpenAPI spec is imported, and
Guild's credential proxy injects the API key.

## Sponsor tools and how each one is used

| Tool | Feature | How AEGIS uses it |
|---|---|---|
| **Guild.ai** | Agent hosting | One `aegis-sentinel-NN` agent per three repos (`aegis-sentinel-01..03`), plus `aegis-warden` for fleet-wide reports. All are TypeScript `llmAgent`s published with `guild agent save --publish`. |
| | Webhook + time triggers | Each repo gets two GitHub webhook triggers (`push` and `pull_request`) that pass the raw payload to its sentinel. A CRON time trigger wakes the warden. |
| | Custom integration | Our scanner is imported from [`openapi.yaml`](openapi.yaml) as the `aegis-scanner` integration. Its tools are `scan_diff`, `scan_full`, `record_action` and `fleet_insights`. Guild's credential proxy injects the `X-AEGIS-Key` API key, so agents never see it. |
| | Credential policies | A sentinel may write only to its own three repos. If it tries to touch any other repo, Guild's proxy blocks the call and logs a `decision: deny` security event ([`fleet/policies.sh`](fleet/policies.sh)). |
| | Skills | `aegis~security-review` covers true-positive criteria, a severity rubric by CWE and the Issue template. `aegis~remediation-playbook` covers minimal patches and when to open a PR versus only an Issue. |
| | Sub-agents | The sentinel delegates to `triage` (is this finding real and reachable?), `remediator` (Issue, branch and PR) and `verifier` (re-scan, comment and close). |
| | Evals | [`evals/sentinel.json`](evals/sentinel.json) runs clean and vulnerable push samples. It checks for the `scan_diff`, `issues_create` and `commit_status` tool calls, and an LLM judge confirms that a clean push gets no Issue. |
| | LLM provider | The agents reason with OpenAI models through Guild's LLM provider. |
| **Semgrep** | Diff scanning | `/scan/diff` runs `semgrep --baseline-commit <before>`, so only findings that the push *introduced* count toward the verdict. |
| | Bundled rules with autofix | [`rules/`](rules/) holds high-signal Python and JS rules (SQL concat, hard-coded secrets, `debug=True`, `shell=True`, `yaml.load`, `eval`, `child_process.exec`, reflected XSS, and others). Rules carry `fix:`, and the fix text becomes the agent's PR patch. Fixtures are tested with `semgrep --test rules/`. |
| | Registry packs | `p/security-audit` and `p/secrets` run next to our rules. If the registry can't be reached, the scanner falls back to the bundled rules. |
| | Supply chain (optional) | When `SEMGREP_APP_TOKEN` is set, `/scan/full` also runs `semgrep ci --supply-chain` and maps advisories into the same Finding shape. |
| **ClickHouse** | Schema | [`clickhouse/schema.sql`](clickhouse/schema.sql) defines three MergeTree tables: `scans` (one row per agent run, with latency), `findings` (one row per finding per scan, with fingerprint, severity, CWE and commit time) and `actions` (every status, Issue, PR, dismissal and deny). |
| | Enrichment that changes the verdict | Before an agent sees a finding, `ch.enrich` adds `seen_before` (how often the fleet has seen this fingerprint), `dismissed_before` (triage already called it a false positive, so it's suppressed fleet-wide) and `repo_mttr_h` (this repo's mean time to remediate, used to prioritise). |
| | Insights for the warden | `GET /insights` returns rising repos (findings now vs the previous window), noisy rules (dismiss rate), reopened findings (a fingerprint that came back) and agent latency p50/p95 from `quantiles()`. |
| | Backfill at scale | [`clickhouse/backfill.py`](clickhouse/backfill.py) replays Semgrep over the git history of the 10 fleet repos plus 3 long-lived OSS projects (sqlmap, buildbot, NodeGoat; histories over 8k commits are evenly sampled to 400). That is 1,088 commits from 2005 to 2026 and 22,611 finding rows, each stamped with its real commit time. |
| | Live dashboard | `/` shows the fleet, a live feed, a security posture timeline (findings per week by severity), latency and MTTR. All panels are ClickHouse queries refreshed every 2 to 30 s. |

## Run it

```bash
uv sync
cp .env.example .env                                   # GITHUB_TOKEN, SCANNER_KEY, CLICKHOUSE_*, OPENAI_API_KEY, GUILD_*
docker compose -f clickhouse/docker-compose.yml up -d  # local ClickHouse (or point CLICKHOUSE_HOST at ClickHouse Cloud)
uv run python -m aegis.ch selftest                     # creates the schema, round-trips one row of each table
uv run python clickhouse/backfill.py                   # Semgrep over the git history of the fleet → ClickHouse
uv run python -m aegis.ch stats                        # row counts
./run.sh                                               # scanner on :8787 + cloudflared tunnel (URL in state/tunnel_url.txt)
open http://localhost:8787/                            # dashboard
```

Fleet setup (needs `gh` and `guild` logged in):

```bash
fleet/make-targets.sh        # creates vincivv/aegis-target-01..09 and writes fleet.json (DRY=1 to build locally only)
fleet/integration.sh         # publishes the scanner as the aegis-scanner Guild integration (needs the tunnel URL)
fleet/deploy.sh              # deploys aegis-sentinel-NN per fleet.json and creates push + pull_request triggers per repo
fleet/policies.sh            # per-sentinel credential policies (allow its own repos, deny everything else)
```

`fleet.json` is the whole fleet definition:

| Agent | Repos |
|---|---|
| `aegis-sentinel-01` | `vincivv/aegis-target-01`, `-02`, `-03` |
| `aegis-sentinel-02` | `vincivv/aegis-target-04`, `-05`, `-06` |
| `aegis-sentinel-03` | `vincivv/aegis-target-07`, `-08`, `-09`, `vincivv/snipbox` |

Dashboard API (read-only JSON, all endpoints return 200 with empty data when ClickHouse is down):
`/api/stats`, `/api/fleet`, `/api/events?n=60`, `/api/timeline?weeks=52`, `/api/latency`, `/api/mttr`, `/api/insights?hours=24`.

## Repo layout

```
aegis/                 scanner service (FastAPI)
  server.py            Contract 1 endpoints (/scan/diff, /scan/full, /actions, /insights) + /webhook/github fallback
  scanner.py           git checkout + semgrep JSON (+ fix)
  ch.py                Contract 2: ClickHouse access layer (insert, enrich, insights, dashboard queries)
  dashboard.py         dashboard router: / and /api/*
  static/              dashboard page (index.html, app.js, style.css; no build step, no external JS)
  analyst_guild.py     Guild API-trigger client;  analyst_openai.py: OpenAI fallback analyst
openapi.yaml           Contract 1: the scanner API that Guild imports as a custom integration
clickhouse/            schema.sql (Contract 2), docker-compose.yml, backfill.py
rules/                 bundled Semgrep rules with fix: + test fixtures
guild-agent/           sentinel, triage, remediator, verifier, warden agents for Guild
skills/                aegis~security-review, aegis~remediation-playbook
evals/                 Guild evals for the sentinel
fleet/                 make-targets.sh, integration.sh, deploy.sh, policies.sh
demo-target/           sample Flask + Express apps with vulnerable/fixed variants
fleet.json             agent → repos assignment
docs/                  demo-checklist.md, stream briefs
```

## Team

- **Andrii**: Guild agents and fleet (`<email>`)
- **Akmal**: `<role>` (`<email>`)
- **`<person 3>`**: `<role>` (`<email>`)
