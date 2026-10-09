# AEGIS — build plan (Cyberdefense Hackathon, 2026-10-09)

**Deadline:** submit by **4:30 PM** at tokensand.com/cyberhack/submit (repo + 3-min demo video + description + names/emails). Demos 5:00 PM.
**Judging:** Autonomy · Idea · Technical implementation · Tool use (3+ sponsors) · 3-min demo.
**Sponsor prizes we target:** Pi (overall), Guild.ai (hosting agents), Semgrep (best vulnerability found), ClickHouse (real-time analytics).

## The idea in one paragraph

A fleet of autonomous security agents on **Guild.ai**. Each agent owns 3 GitHub repos. On every push / PR the agent wakes up, asks our **Semgrep** scanner "did this change make the repo unsafe?", and acts: **unsafe** → red commit status + GitHub Issue with impact and fix + PR with the patch; **safe** → green commit status (+ approves the PR) and closes any Issue whose finding disappeared. **ClickHouse** remembers every scan, finding and action across the fleet, and that memory feeds back into detection (suppress known false positives, prioritise by repo MTTR, drift reports). 3 agents × 9 repos today; 100 × 300 is a config file.

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
ClickHouse: scans / findings / actions  → enrichment + insights + dashboard. Backfill = Semgrep over full git history of 12 repos.
```

Why the scanner is outside Guild: the Guild sandbox has no shell and no outbound fetch, so Semgrep cannot run inside an agent. The scanner is exposed to agents as a proper Guild integration (OpenAPI import, API key injected by Guild's proxy).

## Team split — 3 streams, 3 Claude subagents each

Everyone works in this repo, pushes straight to `main`, pulls often. Each stream owns its own directories, so there is nothing to merge. Open your brief in Claude Code and paste it as the first message: it tells Claude to spawn 3 subagents in parallel.

| Stream | Owner | Brief | Owns | Done when |
|---|---|---|---|---|
| **A · Guild agents + fleet** | Andrii | [`docs/streams/A.md`](docs/streams/A.md) | `guild-agent/`, `fleet/deploy.sh`, `fleet/policies.sh`, `skills/`, `evals/` | `guild agent test` on a vulnerable push → commit status + Issue; `guild trigger list` = 18; policy deny visible |
| **B · Scanner + Semgrep** | person 2 | [`docs/streams/B.md`](docs/streams/B.md) | `aegis/server.py`, `aegis/scanner.py`, `aegis/enrich.py`, `rules/`, `openapi.yaml`, `fleet/integration.sh`, `run.sh`, `tests/` | `curl /scan/diff` clean→vuln = 3 findings with `fix`; `guild integration version test` works through Guild |
| **C · ClickHouse + data + demo** | person 3 | [`docs/streams/C.md`](docs/streams/C.md) | `clickhouse/`, `aegis/ch.py`, `aegis/dashboard.py`, `aegis/static/`, `fleet/make-targets.sh`, `demo-target/`, `SUBMISSION.md`, `README.md` | `SELECT count() FROM findings` > 10k; dashboard draws timeline; 9 target repos exist. **✅ Done (13:45):** 22,611 findings on ClickHouse Cloud (1,088 commits, 2005–2026), full-history timeline on the dashboard, `vincivv/aegis-target-01..09` + demo branches, fleet.json/policies.json/context.md on vincivv names |

**Contracts (frozen, do not change without telling the team):**
- `openapi.yaml` — the scanner API that Guild imports. Endpoints: `POST /scan/diff`, `POST /scan/full`, `POST /actions`, `GET /insights`. Header `X-AEGIS-Key`.
- `clickhouse/schema.sql` + `aegis/ch.py` — tables `scans`, `findings`, `actions`; functions `insert_scan`, `insert_findings`, `insert_action`, `enrich`, `insights`.

Dependencies: B needs `ch.py` from C (works with the stub until then). A needs `openapi.yaml` + a live tunnel from B (writes agents against the contract until then). **Integration point: ~1:30 PM, full loop on target-01.**

## Timeline

| When | What |
|---|---|
| 11:00–13:00 | P0: scanner v2, integration published, sentinel deployed with triggers, 9 target repos, ClickHouse schema + ch.py |
| 13:00–13:30 | Integration: full loop clean → vuln → fix on target-01 |
| 13:30–15:00 | P1: sub-agents (triage/remediator/verifier), policies, skills, warden, backfill, dashboard from ClickHouse |
| 15:00–15:30 | Rehearse demo twice, fix what breaks |
| 15:30–16:00 | Record the 3-min video (script below), write SUBMISSION.md |
| 16:00–16:30 | Submit. Make repo public or add judges (Akmal). |

## Demo script (3 min, all live, nobody clicks anything but `git push`)

| Time | On screen | Say |
|---|---|---|
| 0:00–0:20 | Dashboard: 3 agents, 9 repos, green. ClickHouse timeline: security posture over the full commit history, tens of thousands of findings | "AI agents write code faster than any security team can read it. We built the security team that scales the same way: one agent per three repos, on Guild, never sleeps." |
| 0:20–1:20 | **Live push** of vulnerable code to target-01. Within seconds: dashboard "sentinel-01 WAKE", Guild session feed shows `scan_diff → get_content → issues_create → commit_status → pulls_create`. Red ❌ on the commit, Issue with impact + fix, **PR from the agent with the patch** | "Nobody touched anything. Semgrep found it, the agent read the code, confirmed it's reachable, explained it and opened the fix." |
| 1:20–1:50 | Merge the agent's PR → push → sentinel wakes → ✅ green, Issue auto-closed "re-scanned at <sha>". Dashboard: MTTR 47 s | "Loop closed: detect, fix, verify, close. And remembered: that fix time is in ClickHouse now." |
| 1:50–2:20 | **Governance.** Policies screen: sentinel-01 can only touch its 3 repos. Fire it at a foreign repo → Guild Security events `decision: deny` | "100 agents with write access to GitHub is itself a threat. Each one is fenced by Guild's proxy, not by a prompt." |
| 2:20–2:50 | **Intelligence.** Warden drift report from ClickHouse: rising repos, noisy rules, reopened findings. A false positive dismissed once is never filed again anywhere in the fleet | "The fleet learns. ClickHouse remembers every finding and decision, and those numbers change what agents do tomorrow." |
| 2:50–3:00 | Closing slide: Guild · Semgrep · ClickHouse · GitHub | "3 agents and 9 repos today. 100 and 300 is one config file." |

Backup: rehearse twice before recording; `demo/vuln` and `demo/clean` branches ready so a push is one command; keep the recording as fallback for the live finals.

## Things only humans can do (do them first)

- **Andrii:** `guild auth login`, `guild workspace select`; app.guild.ai → Credentials → GitHub (install Guild app on all `aegis-target-*`) + Email credential.
- **Person 3:** ClickHouse Cloud service (sponsor credits) → host/password into `.env` (never commit `.env`). Fallback: `docker compose -f clickhouse/docker-compose.yml up -d`.
- **Anyone:** ask the Semgrep table for a `SEMGREP_APP_TOKEN` (Supply Chain + Secrets scanning) — optional.
- **Akmal:** before 16:30 make the repo public or add the judges.
