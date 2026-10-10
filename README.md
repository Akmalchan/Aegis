<p align="center">
  <img src="docs/banner.png" alt="Aegis" width="760" />
</p>

<p align="center">
  <b>Security agents for every repo. Nobody clicks.</b><br />
  On every push, an agent on Guild.ai asks Semgrep whether the change made the repo unsafe.<br />
  If it did, the agent proves the bug, patches only the flagged lines, verifies the fix three ways and merges it.<br />
  ClickHouse remembers every decision, so the fleet gets smarter with every push.
</p>

<p align="center">
  <a href="https://tokensand.com/p/aegis-2"><img alt="Cyberdefense Hackathon" src="https://img.shields.io/badge/Cyberdefense_Hackathon-SF_Tech_Week_2026-0a0a0a?style=for-the-badge&labelColor=ffe03d" /></a>
  <a href="https://youtu.be/qWBtfWlOsAU"><img alt="Demo video" src="https://img.shields.io/badge/demo-video-ff4a3d?style=for-the-badge&labelColor=0a0a0a" /></a>
</p>

<p align="center">
  <img alt="Guild.ai" src="https://img.shields.io/badge/Guild.ai-11_hosted_agents-0a0a0a?style=for-the-badge" />
  <img alt="Semgrep" src="https://img.shields.io/badge/Semgrep-22_custom_rules-0a0a0a?style=for-the-badge" />
  <img alt="ClickHouse" src="https://img.shields.io/badge/ClickHouse-39k_findings-0a0a0a?style=for-the-badge" />
  <img alt="OpenAI" src="https://img.shields.io/badge/OpenAI-span--only_patches-0a0a0a?style=for-the-badge" />
</p>

<p align="center">
  <a href="#why">Why</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="#results">Results</a> ·
  <a href="#what-it-does">What it does</a> ·
  <a href="#run-it-yourself-the-aegis-cli">CLI</a> ·
  <a href="#sponsor-tools">Sponsor tools</a> ·
  <a href="#how-we-know">How we know</a> ·
  <a href="#run-it">Run it</a> ·
  <a href="#team">Team</a>
</p>

---

## Why

AI agents write code faster than any security team can read it. Real-world numbers from the git history of the open-source projects we scanned: a vulnerability, once committed, took a **median of 170 days** to be fixed, and **328 are still open today**, live for a median of **8.7 years**. The oldest has been sitting in a popular codebase for **15.5 years**.

**AEGIS** is a security team that scales the same way the code does: a fleet of agents hosted on Guild.ai, one sentinel per three GitHub repos, catching the bug **on day 0**, at the push that introduces it, and fixing it before anyone has to triage a ticket.

## How it works

The model never looks for bugs. Semgrep does, deterministically. The model only explains and repairs what Semgrep found, and every repair has to prove itself before it ships.

```
git push ──► Guild webhook ──► sentinel agent ──► Semgrep diff scan ──► unsafe?
                                                                        │ yes
      triage ◄──────────────────────────────────────────────────────────┘
      │
      ▼
      failing test ──► span-only patch ──► 3-layer check ──► verified PR ──► merged
                                                                             │
      ClickHouse remembers every scan, finding and action ◄──────────────────┘
```

1. **Push.** A GitHub webhook fires a Guild trigger; the sentinel that owns the repo wakes up within 1 to 4 s.
2. **Semgrep finds.** `scan_diff` runs `semgrep --baseline-commit`, so only findings the push introduced count. The verdict comes from the scanner, not the model; a red or green commit status lands in about 25 s.
3. **Proven real.** The `triage` sub-agent traces source → sink → consequence and writes a test that **fails** on the vulnerable commit.
4. **Fix.** The Semgrep rule's own `fix:` when it has one, otherwise OpenAI `gpt-4.1` rewrites **only the flagged span** (temperature 0; the scanner assembles the file, never the LLM). The patch is Semgrep-scanned before it is handed on.
5. **Re-checked, three ways.** L1 the finding is gone and nothing new appeared · L2 the repo's own tests pass · L3 the new test fails at base and passes at head.
6. **One Issue tells the story.** What was found, how it was proven, the diff, every check with its timing, and the decision.
7. **Decision.** Verified: the agent opens the PR, labels it `aegis:verified` and merges it. Not verified: nothing is pushed and the Issue says why.
8. **Merge re-triggers the sentinel.** The merge commit is scanned like any push and goes green.

Each sentinel can only act on its own three repos. That fence is enforced by Guild's credential proxy, not by a prompt: a call on a foreign repo is refused before it reaches GitHub.

## Results

Measured on real GitHub webhooks through Guild, real Semgrep scans and the live ClickHouse database ([`docs/LIVE-RUN.md`](docs/LIVE-RUN.md)).

**One push, end to end** (`andriidrok1/aegis-demo-target`, [PR #77](https://github.com/andriidrok1/aegis-demo-target/pull/77)):

| After `git push` | |
|---|---|
| Guild session spawned | **1 to 4 s** |
| Red commit status on the vulnerable commit | **~25 s** |
| Story Issue opened | **~2 min** |
| Fix PR opened, verified and **merged by the agent** | **2 min 34 s** |
| Size of the fix | **+2 / −1 lines, 1 file** |
| Scan time | **2.5 s** median · 5.0 s p95 (Semgrep alone 1.6 s) |

**The exposure clock** (Semgrep over the full git history of 10 open-source projects, 2005 to 2026):

| | |
|---|---|
| Vulnerabilities tracked through history | **1,234** |
| Median time until a human fixed them | **170 days** (p90 718 days) |
| Still open today | **328** (27%), live a median of **8.7 years** |
| Oldest still open | **15.5 years** |
| With AEGIS | **caught on day 0** |

**The fleet**

| | |
|---|---|
| Agents on Guild · repos guarded | **11 · 9** |
| Scans · findings remembered in ClickHouse | **2,157 · 39,090** (1,255 unique, 21 rules) |
| Issues and PRs opened by the agents | **62 Issues · 18 PRs** |
| AI patches rejected by the verify gate | **48 rejected, 27 passed**: nothing ships without proof |
| Agent-to-agent handoffs rejected by the Semgrep guard | **11 of 31** |
| Patrol: fresh public repos scanned live in one evening | **1,207**, 21% with real issues, **349 hardcoded secrets**, 14 prompt-injection strings |

## What it does

- **Fleet of agents on Guild:** sentinels (one per 3 repos), sub-agents `triage`, `remediator`, `verifier`, plus `warden` (cron fleet scans), `reporter` (hourly report), `rulesmith` (turns a confirmed bug into a new tested Semgrep rule for the whole fleet) and `onboarder` (an Issue titled `onboard: owner/repo` adds a repo)
- **Can't be talked into green:** a code comment telling the agent to approve the file is itself a Semgrep finding (CWE-1427); repo content is data, never instructions
- **Memory that changes behaviour:** a false positive dismissed once is never filed again anywhere in the fleet; every finding gets a priority from fleet history; every Issue says how often the bug was seen and how fast this repo fixes things
- **Live control room:** fleet map, the 8-step timeline of the latest push, agent roster, handoff guard, Patrol terminal and a live chat of every agent action
- **Patrol:** scans freshly pushed public repos with the AEGIS rules and streams what it finds. Repo names are masked, code is never shown, results stay in memory
- **GitHub code scanning:** findings uploaded as SARIF to the repo's Security tab
- **The `aegis` CLI:** the same engine on your laptop or in CI

## Run it yourself: the `aegis` CLI

Same engine and rules as the fleet. No server, no Guild, no accounts. Needs `semgrep` on PATH (`pipx install semgrep`).

```bash
pipx install git+https://github.com/Akmalchan/Aegis
aegis scan                                # this repo; exit 1 if unsafe, so CI fails the build
aegis scan --diff main                    # only what this branch introduced, like a push
aegis scan owner/repo --sarif out.sarif   # any GitHub repo + SARIF for code scanning
aegis fleet                               # fleet numbers + exposure clock (needs a running scanner)
aegis watch                               # live feed of the agents
```

```
 ✗ HIGH  sql-string-concat  app.py:28  CWE-89
        │ cur.execute("SELECT id, name, email FROM users WHERE name LIKE '%" + q + "%'")
        ⤷ taint source :26 → sink :28
 ✗ HIGH  hardcoded-secret  app.py:7  CWE-798
        ✓ fix import os; ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "")
────────────────────────────────────────────────────────────────
 ✗ UNSAFE  3 findings in 1 files  2 high · 1 medium  4.0s
 ✓ 2 with a ready-made Semgrep autofix
```

Every finding shows severity, CWE, the matched line, the taint path, the exposure clock and Semgrep's autofix or fix hint. `--json` for machines, `--fail-on error|warning|never` for CI.

## Sponsor tools

| Tool | How AEGIS uses it |
|---|---|
| **Guild.ai** | 11 hosted TypeScript `llmAgent`s ([`guild-agent/`](guild-agent/)); 21 webhook + 2 cron triggers ([`fleet/triggers.json`](fleet/triggers.json)); the scanner as a custom integration imported from [`openapi.yaml`](openapi.yaml), with the API key injected by Guild's proxy; least-privilege credential policies per agent ([`fleet/policies.sh`](fleet/policies.sh)); skills and evals ([`skills/`](skills/), [`evals/`](evals/)) |
| **Semgrep** | Diff scans with `--baseline-commit`; 22 bundled rules (Python, JS/TS) with CWE metadata and autofixes ([`rules/`](rules/)); taint rules for AI-generated code and the prompt-injection rule ([`docs/SEMGREP-DEEP.md`](docs/SEMGREP-DEEP.md)); a handoff guard that scans every patch, test and rule one agent hands another ([`aegis/guard.py`](aegis/guard.py)); `--validate` + `--test` on every learned rule, in CI too; a self-audit of AEGIS itself ([`docs/SELF-AUDIT.md`](docs/SELF-AUDIT.md)) |
| **ClickHouse** | `scans`, `findings`, `actions` tables plus a backfill over 2,007 commits of history ([`clickhouse/`](clickhouse/)); enrichment inside the verdict path (`seen_before`, `dismissed_before`, `repo_mttr_h`, priority); materialized views for the 20-year posture timeline (~45 ms), a fix funnel and an anomaly watch over the agents ([`clickhouse/README.md`](clickhouse/README.md)) |
| **OpenAI** | `gpt-4.1` writes the replacement for the flagged span when the rule has no `fix:`; JSON output, temperature 0, guarded and verified before use ([`aegis/fix.py`](aegis/fix.py)) |
| **GitHub** | Commit statuses, Issues, branches, PRs, labels, merges and SARIF uploads, all through Guild's GitHub credential |

## How we know

The model never decides what is vulnerable; Semgrep does, over the whole checkout, with no context window. Every repair must survive the static re-scan, the repo's own tests and a targeted test that fails on the vulnerable commit; a no-op fix is rejected. While building this we caught our own AI fixer "fixing" a bug by breaking the file's syntax and still getting a green check, because the gate ignored Semgrep's parse errors. The gate now fails on any file Semgrep cannot parse ([`docs/SEMGREP-FINDING.md`](docs/SEMGREP-FINDING.md)). Full write-up: [`docs/HOW-WE-KNOW.md`](docs/HOW-WE-KNOW.md).

## Run it

```bash
uv sync && cp .env.example .env        # GITHUB_TOKEN, SCANNER_KEY, OPENAI_API_KEY, CLICKHOUSE_*, GUILD_*
uv run python -m aegis.ch selftest     # schema + one round-trip row per table
uv run python clickhouse/backfill.py   # optional: Semgrep over git history → ClickHouse
./run.sh                               # scanner on :8787 + cloudflared tunnel (URL in state/tunnel_url.txt)
open http://localhost:8787/            # the control room
tests/self_audit.sh                    # Semgrep on AEGIS itself
semgrep scan --metrics=off --test --config rules/ rules/tests/
```

Fleet setup (needs `gh` and `guild` logged in): `fleet/integration.sh` publishes the scanner integration, `fleet/deploy.sh` publishes the agents and creates the triggers from [`fleet.json`](fleet.json), `fleet/policies.sh` applies the per-agent policies. Demo kit for a live push: [`demo/snipbox/README.md`](demo/snipbox/README.md).

<details>
<summary><b>Repo layout</b></summary>

```
aegis/          scanner service: server.py, scanner.py (git + semgrep + traces), fix.py (span-only patch),
                verify.py (L1/L2/L3), guard.py (handoff guard), rules_api.py (rule gate), ch.py (ClickHouse),
                patrol.py, cli.py, dashboard.py + static/ (control room)
openapi.yaml    the scanner API Guild imports
rules/          bundled Semgrep rules + tests
guild-agent/    sentinel, triage, remediator, verifier, warden, reporter, rulesmith, onboarder
fleet/          deploy.sh, integration.sh, policies.sh, make-targets.sh;  fleet.json = agent → repos
clickhouse/     schema.sql, views.sql, backfill.py
docs/           LIVE-RUN.md, HOW-WE-KNOW.md, SEMGREP-DEEP.md, SEMGREP-FINDING.md, SELF-AUDIT.md
```
</details>

## Team

Built in one day at the **Cyberdefense Hackathon**, SF Tech Week 2026 ([project page](https://tokensand.com/p/aegis-2) · [demo video](https://youtu.be/qWBtfWlOsAU)).

| | |
|---|---|
| **Dias Almat** · [@vincivv](https://github.com/vincivv) | ClickHouse, data, demo targets |
| **Akmal Shovkatov** · [@Akmalchan](https://github.com/Akmalchan) | Scanner API, Semgrep rules, control room, Patrol, CLI |
| **Andrii Drok** · [@andriidrok1](https://github.com/andriidrok1) | Guild agents, fleet, scanner |
