# AEGIS demo checklist

Run this from top to bottom 15 minutes before rehearsing or recording, and again before the live finals.

## Pre-flight

| # | Check | Command / where | Expected |
|---|---|---|---|
| 1 | Scanner + tunnel up | `./run.sh` (leave the terminal open) | the tunnel URL is printed |
| 2 | Tunnel URL matches the integration | `cat state/tunnel_url.txt` and compare with the `aegis-scanner` integration base URL in Guild | same URL (if it changed, see the fallbacks below) |
| 3 | Scanner reachable from outside | `curl -s "$(cat state/tunnel_url.txt)/insights" -H "X-AEGIS-Key: $SCANNER_KEY"` | JSON with `rising_repos`, `noisy_rules`, … |
| 4 | Guild integration works end to end | `guild integration version test <owner>~aegis-scanner --operation scan_diff --input-body '{"repo":"vincivv/aegis-target-01","base_sha":"<clean>","head_sha":"<vuln>","agent":"preflight"}'` | findings come back through Guild |
| 5 | Triggers active | `guild trigger list` | 2 webhook triggers (push + pull_request) per repo + 1 CRON for `aegis-warden`, all enabled |
| 6 | Sentinels published | `guild agent list` | `aegis-sentinel-01..03` + `aegis-warden` at their latest version |
| 7 | Credential policies in place | app.guild.ai → Credentials → GitHub → Policies | each sentinel: ALLOW own repos, DENY everything else |
| 8 | ClickHouse reachable and loaded | `uv run python -m aegis.ch stats` | findings in the tens of thousands (backfill done) |
| 9 | Remove test rows | `uv run python -m aegis.ch purge-selftest` | `selftest/repo` gone from the dashboard |
| 10 | Dashboard on the projector | open `http://localhost:8787/` full-screen (Cmd+Ctrl+F), zoom so the fleet cards and feed fill the screen (usually 100–125 % at 1080p) | header shows *ClickHouse: online*, the timeline draws, 3 agent cards are green |
| 11 | Demo repos reset to clean | see **Push commands** → reset | target-01 `main` = clean, no open AEGIS Issues/PRs from rehearsal (close them) |
| 12 | Browser tabs ready | tab 1 dashboard · tab 2 target-01 commits · tab 3 target-01 Issues · tab 4 Guild session feed · tab 5 Guild Security events | all logged in |
| 13 | Notifications off | macOS Focus mode on, Slack/mail closed | — |
| 14 | Fallback video on disk | `<path to recording>` | plays |

## Push commands

_Owned by C2 (data stream): exact `git push` commands for `demo/vuln`, `demo/clean`, and resetting to `demo/base`. To be filled in._

## 3-minute script

| Time | On screen | Say |
|---|---|---|
| 0:00–0:20 | Dashboard: 3 agents, 10 repos, all green. ClickHouse posture timeline over the full commit history, tens of thousands of findings | "AI agents write code faster than any security team can read it. We built the security team that scales the same way: one agent per three repos, on Guild, never sleeps." |
| 0:20–1:20 | **Live push** of vulnerable code to target-01. Within seconds the dashboard flashes "⚡ aegis-sentinel-01 WAKE". The Guild session feed shows `scan_diff → get_content → issues_create → commit_status → pulls_create`. Red ❌ on the commit, an Issue with impact and fix, and **a PR from the agent with the patch** | "Nobody touched anything. Semgrep found it, the agent read the code, confirmed it's reachable, explained it and opened the fix." |
| 1:20–1:50 | Merge the agent's PR → push → the sentinel wakes → ✅ green, Issue auto-closed "re-scanned at <sha>". Dashboard MTTR panel shows the fix time | "Loop closed: detect, fix, verify, close. And remembered: that fix time is in ClickHouse now." |
| 1:50–2:20 | **Governance.** Policies screen: sentinel-01 can only touch its 3 repos. Fire it at a foreign repo → Guild Security events show `decision: deny` (the dashboard feed shows `DENIED` in orange) | "100 agents with write access to GitHub is itself a threat. Each one is fenced by Guild's proxy, not by a prompt." |
| 2:20–2:50 | **Intelligence.** Warden drift report from ClickHouse: rising repos, noisy rules, reopened findings (also in the dashboard's Fleet intelligence panel). A false positive dismissed once is never filed again anywhere in the fleet | "The fleet learns. ClickHouse remembers every finding and decision, and those numbers change what agents do tomorrow." |
| 2:50–3:00 | Closing slide: Guild · Semgrep · ClickHouse · GitHub | "3 agents and 10 repos today. 100 and 300 is one config file." |

## Fallbacks

| Failure | Symptom | Do this |
|---|---|---|
| ClickHouse down | header shows *ClickHouse: offline*, the timeline says "ClickHouse offline" | Keep going. Fleet cards and the live feed fall back to local state (`state/*.json`, `state/events.jsonl`), and the scanner keeps scanning (ch.py never blocks it). Restart with `docker compose -f clickhouse/docker-compose.yml up -d`, and the dashboard recovers on its next refresh. Narrate the timeline from a screenshot if needed. |
| Guild down / agent not waking | no session in the Guild feed after a push | Switch to the built-in path. Add the GitHub webhook `https://<tunnel>/webhook/github` (content type JSON, event push) on target-01. `server.py` then runs Semgrep and the OpenAI analyst itself (`analyst_openai.py`, needs `OPENAI_API_KEY`), so Issues still open and close and the dashboard still shows WAKE / ISSUE / CLOSED. Manual kick: `curl -X POST "localhost:8787/scan?repo=vincivv/aegis-target-01"`. |
| Tunnel down / URL changed | Guild calls to `aegis-scanner` fail or time out | Restart `./run.sh`, read the new URL from `state/tunnel_url.txt`, and update the integration base URL (`fleet/integration.sh` with the new `TUNNEL_URL`; the base URL is frozen per published version, so publish a new version). Re-run pre-flight check 4. |
| Push doesn't trigger anything | no WAKE on the dashboard | Check `guild trigger list` for that repo, then try the fallback above. As a last resort, play the recording. |
| Anything else live | — | Switch to the **pre-recorded video** (`<path / URL>`) and narrate over it. |
