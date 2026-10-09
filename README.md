# AEGIS: an autonomous security fleet for GitHub

> Cyberdefense Hackathon, 2026-10-09. Built on Guild.ai, Semgrep, OpenAI, ClickHouse, GitHub.

AI agents write code faster than any security team can read it. AEGIS is a security team that scales the same way: a fleet of agents hosted on Guild.ai, one sentinel per three GitHub repos. On every push the sentinel asks our Semgrep scanner whether the change made the repo unsafe. If it did, the agent proves the bug with a test, asks for a patch that touches only the flagged lines, re-checks the patch three ways, writes one Issue that tells the whole story, and merges the fix itself when (and only when) the checks pass. Nothing an LLM wrote reaches the repo unverified. ClickHouse remembers every scan, finding and action across the fleet, and that memory changes what the agents do next: a false positive dismissed once is never filed again anywhere.

## The loop, in 8 steps

1. **Push.** A GitHub webhook fires a Guild trigger for that repo; the sentinel that owns the repo wakes up (session spawned 1 to 4 s after the push).
2. **Semgrep finds.** `scan_diff(repo, before, after)` runs `semgrep --baseline-commit` through our scanner, so only findings the push introduced count. The verdict (`safe` / `unsafe`) is the scanner's field; the model cannot override it. Red or green commit status lands on the sha within about 30 s.
3. **Validated.** The `triage` sub-agent reads the one file at the one sha and traces source, sink and consequence. Then the remediator writes a small pytest that must fail on the vulnerable commit.
4. **Fix.** `fix_code` returns a patch for one finding: the Semgrep rule's own `fix:` when the rule has one, otherwise OpenAI `gpt-4.1` writes a replacement for the flagged span only (temperature 0, span-locked, the full file is assembled by the scanner, never by the LLM). The patch itself is Semgrep-scanned before it is handed back.
5. **Re-check.** `verify_fix` runs three layers against base and head: L1 the fingerprint is gone and nothing new appeared; L2 the repo's own test suite still passes; L3 the targeted test fails at base and passes at head.
6. **One Issue tells the story.** Summary, what was found, how it was validated, the diff, each verification layer with its timing, and the decision. Fingerprint in a footer so the fleet can match it later.
7. **Decision.** Verified: the agent opens the PR, labels it `aegis:verified`, and merges it. Not verified: the Issue stays open with the reason, nothing is pushed.
8. **Merge re-triggers the sentinel.** The merge commit is scanned like any push: status goes green, and the Issue is closed with a "re-scanned at `<sha>`" comment after a full scan confirms the finding is gone.

## Architecture

```
push / PR ──▶ GitHub ──▶ Guild webhook trigger ──▶ aegis-sentinel-0N (Guild llmAgent, 3 repos each)
                                                     │  scan_diff ─────────────┐
                                                     │  set_status ❌/✅        │  aegis-scanner (Guild custom integration,
                                                     │  aegis_triage ──────────┤  OpenAPI import, key injected by Guild's proxy)
                                                     │  aegis_remediator       │   FastAPI + Semgrep CLI 1.180
                                                     │    fix_code ────────────┤   /scan/diff /scan/full /fix /verify /status
                                                     │    verify_fix ──────────┤   /actions /insights /rules/propose /guard
                                                     │    Issue → PR → merge   │   OpenAI gpt-4.1 for span-only patches
                                                     │  aegis_verifier (close) │
                                                     └──────────────────────────┘
                                                                  │ every scan, finding, action, handoff
                                                                  ▼
cron ──▶ aegis-warden (scan_full every 30 min, drift report)   ClickHouse Cloud: scans / findings / actions
cron ──▶ aegis-reporter (hourly fleet report with charts)       enrich: seen_before, dismissed_before, repo_mttr_h
issue ──▶ aegis-rulesmith (finding → learned rule → PR)         insights: rising repos, noisy rules, reopened, p50/p95
issue ──▶ aegis-onboarder ("onboard: owner/repo" → fleet.json PR)
```

The scanner runs outside Guild because the Guild sandbox has no shell and no outbound fetch, so Semgrep cannot run inside an agent. Agents reach it as a normal Guild integration generated from [`openapi.yaml`](openapi.yaml).

Fleet today: 10 Guild agents (`guild agent list --owner andriidrok1`), 23 triggers (21 webhook + 2 cron, `guild trigger list`), 9 monitored repos in [`fleet.json`](fleet.json). Adding a repo is an Issue titled `onboard: owner/repo`; the onboarder files the `fleet.json` PR.

## Sponsor tools and exactly how each is used

| Tool | What | Where |
|---|---|---|
| **Guild.ai** | 10 hosted TypeScript `llmAgent`s: `aegis-sentinel-01..03` (3 repos each), sub-agents `triage`, `remediator`, `verifier` wired as tools of the sentinel, `warden` and `reporter` on cron, `rulesmith` and `onboarder` on `issues` webhooks | [`guild-agent/`](guild-agent/), [`fleet/deploy.sh`](fleet/deploy.sh) |
| | 21 GitHub webhook triggers (push + pull_request per repo, issues for rulesmith/onboarder) and 2 time triggers | `guild trigger list`, [`fleet/triggers.json`](fleet/triggers.json) |
| | Custom integration `aegis-scanner` imported from OpenAPI; Guild's credential proxy injects `X-AEGIS-Key`, the agents never see it | [`openapi.yaml`](openapi.yaml), [`fleet/integration.sh`](fleet/integration.sh) |
| | Credential policies: each sentinel may write only to its 3 repos; a call on a foreign repo is refused by the proxy before it reaches GitHub (proven, see [`docs/LIVE-RUN.md`](docs/LIVE-RUN.md) "Deny proof") | [`fleet/policies.sh`](fleet/policies.sh) |
| | Skills `aegis~security-review`, `aegis~remediation-playbook`; evals for the sentinel | [`skills/`](skills/), [`evals/`](evals/) |
| **Semgrep** | Diff scans with `--baseline-commit`; 19 bundled rules (Python, JS/TS) with CWE metadata, 5 with `fix:` that become the patch; registry packs `p/security-audit`, `p/secrets` | [`aegis/scanner.py`](aegis/scanner.py), [`rules/`](rules/) |
| | Rules for AI-generated code: `aegis.agent-directed-instruction-in-comment` (a comment that tells the reviewing agent what to do is itself a finding, CWE-1427) and taint rules `aegis.taint-llm-output-to-exec` (LLM response → `eval/exec/subprocess/execute/requests/open`, `json.loads` sanitizes) and `aegis.taint-request-to-sql`; dataflow traces attached to the finding and quoted in the Issue | [`rules/aegis-agent-injection.yml`](rules/aegis-agent-injection.yml), [`rules/aegis-taint.yml`](rules/aegis-taint.yml), [`docs/SEMGREP-DEEP.md`](docs/SEMGREP-DEEP.md) |
| | Handoff guard: every artifact one agent hands another (patch, test, rule) is Semgrep-scanned first; a patch that introduces a finding is rejected before any PR exists | [`aegis/guard.py`](aegis/guard.py), `POST /guard` |
| | Learned-rule gate: `semgrep --validate` + `semgrep --test` with `ruleid:`/`ok:` fixtures on every proposed rule, and in CI | [`aegis/rules_api.py`](aegis/rules_api.py), [`.github/workflows/semgrep-rules.yml`](.github/workflows/semgrep-rules.yml) |
| | Self-audit of AEGIS itself: 127 files, 15 → 4 findings, 0 ERROR | [`tests/self_audit.sh`](tests/self_audit.sh), [`docs/SELF-AUDIT.md`](docs/SELF-AUDIT.md) |
| **OpenAI** | `gpt-4.1` writes the replacement for the flagged span when the rule carries no `fix:`; output is JSON, temperature 0, scanned by the guard, then verified L1 to L3. Guild's LLM provider runs the agents' own reasoning | [`aegis/fix.py`](aegis/fix.py) |
| **ClickHouse Cloud** | Tables `scans`, `findings`, `actions` (MergeTree); backfill of Semgrep over the git history of the fleet plus sqlmap, buildbot and NodeGoat: 1,088 commits, 22,611 finding rows | [`clickhouse/schema.sql`](clickhouse/schema.sql), [`clickhouse/backfill.py`](clickhouse/backfill.py) |
| | Enrichment inside the verdict path: `seen_before`, `dismissed_before` (suppressed fleet-wide), `repo_mttr_h`; `GET /insights` for rising repos, noisy rules, reopened findings, agent latency p50/p95, handoff stats; the dashboard is ClickHouse queries only | [`aegis/ch.py`](aegis/ch.py), [`aegis/dashboard.py`](aegis/dashboard.py) |
| **GitHub** | Commit statuses (`AEGIS / security-check`), Issues, branches via the Git Data API (plain text, no base64), PRs, labels, merges, all through Guild's GitHub credential | [`guild-agent/remediator/README.md`](guild-agent/remediator/README.md) |

## How we know

The model never looks for bugs; Semgrep does, over the whole checkout, with no context window. The model only explains and repairs what Semgrep found, and every repair must survive L1 static re-scan, L2 the repo's own tests and L3 a targeted test that fails on the vulnerable commit. A no-op fix is rejected (negative control: head = base gives `verify_failed`). Everything from the repo (code, comments, commit messages, PR bodies) is data, never instructions; the demo repo carries a comment that asks the agent to pass the file, and Semgrep reports that comment as a finding. Full write-up: [`docs/HOW-WE-KNOW.md`](docs/HOW-WE-KNOW.md).

## Live-run numbers

From real GitHub webhooks through Guild on `andriidrok1/aegis-demo-target` ([`docs/LIVE-RUN.md`](docs/LIVE-RUN.md)):

| Measured | Time after `git push` |
|---|---|
| Guild session spawned | 1 to 4 s |
| Red commit status on a vulnerable push | ≤ 26 s (36 s in the final flow) |
| Green status on a harmless push | 35 s |
| Issue opened | 74 s |
| Fix PR opened | 96 s (3 min 00 s in the final one-fix-per-push flow, which includes the verify gate) |
| PR labelled `aegis:verified` | 112 s |
| PR merged by the agent | 3 min 23 s |
| `verify_fix` | static 3.8 to 8 s, targeted test ~0.2 s |
| Policy deny on a foreign repo | refused by the proxy, `http_status_code: null` |

## Run it

```bash
uv sync && cp .env.example .env        # GITHUB_TOKEN, SCANNER_KEY, OPENAI_API_KEY, CLICKHOUSE_*, GUILD_*
uv run python -m aegis.ch selftest     # schema + one round-trip row per table
uv run python clickhouse/backfill.py   # optional: Semgrep over the fleet's git history → ClickHouse
./run.sh                               # scanner on :8787 + cloudflared tunnel (URL in state/tunnel_url.txt)
curl -s localhost:8787/healthz         # {"ok":true,"semgrep":true,"clickhouse":true,"auth":true}
open http://localhost:8787/            # dashboard
tests/self_audit.sh                    # Semgrep on AEGIS itself, exit 1 on any ERROR
semgrep scan --metrics=off --test --config rules/ rules/tests/
```

Fleet (needs `gh` and `guild` logged in): `fleet/integration.sh` publishes the scanner integration from the tunnel URL, `fleet/deploy.sh` publishes the agents and creates the triggers from `fleet.json`, `fleet/policies.sh` applies the per-sentinel allow/deny policies. Demo kit for the live push: [`demo/snipbox/README.md`](demo/snipbox/README.md).

## Repo layout

```
aegis/          scanner service: server.py (scan/status/actions/insights), scanner.py (git + semgrep + traces),
                fix.py (span-only patch), verify.py (L1/L2/L3), guard.py (handoff guard), rules_api.py (rule gate),
                ch.py (ClickHouse), dashboard.py + static/ (control room)
openapi.yaml    the scanner API Guild imports (scan_diff, scan_full, record_action, set_status, verify_fix,
                fix_code, fleet_insights, propose_rule, list_rules, guard_artifact)
rules/          bundled Semgrep rules + tests;  tests/self_audit.sh, tests/test_scanner.sh
guild-agent/    sentinel, triage, remediator, verifier, warden, reporter, rulesmith, onboarder
fleet/          deploy.sh, integration.sh, policies.sh, make-targets.sh;  fleet.json = agent → repos
clickhouse/     schema.sql, backfill.py;  demo/snipbox/ = vuln.patch, fix.patch, regression test
docs/           LIVE-RUN.md, HOW-WE-KNOW.md, SEMGREP-DEEP.md, SELF-AUDIT.md, STRATEGY.md, demo-checklist.md
```

## Team

- Andrii Drok, Guild agents, fleet, scanner (`<email>`)
- Akmal Shovkatov, `<role>` (`<email>`)
- vincivv, ClickHouse, data, demo targets (`<email>`)
