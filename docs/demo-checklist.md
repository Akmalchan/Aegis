# AEGIS demo checklist

Run top to bottom 15 minutes before rehearsing or recording, and again before the live finals. The script itself is in `docs/STRATEGY.md`, section "Demo script (final)".

## Pre-flight

| # | Check | Command / where | Expected |
|---|---|---|---|
| 1 | Scanner + tunnel up | `./run.sh status` (or `./run.sh` in a terminal you leave open) | API running, tunnel URL printed |
| 2 | Tunnel URL matches the integration | `cat state/tunnel_url.txt` vs the `aegis-scanner` integration base URL in app.guild.ai | same URL (if it changed, see Fallbacks) |
| 3 | Scanner healthy | `curl -s localhost:8787/healthz` | `{"ok":true,"semgrep":true,"clickhouse":true,"auth":true}` |
| 4 | Scanner reachable through the tunnel | `curl -s "$(cat state/tunnel_url.txt)/healthz"` | same JSON |
| 5 | Guild reaches the scanner | `guild integration version test andriidrok1~aegis-scanner --operation scan_diff --input-body '{"repo":"andriidrok1/aegis-demo-target","base_sha":"<clean>","head_sha":"<vuln>","agent":"preflight"}'` | findings come back through Guild |
| 6 | Triggers | `guild trigger list` (and `--offset 20`) | 23 triggers: 21 webhook + 2 cron; the 6 `aegis-sentinel-01--andriidrok1--snipbox--*` / `aegis-demo-target` / `aegis-target-01` rows active |
| 7 | Agents published | `guild agent list --owner andriidrok1` | 10 rows: sentinel-01..03, triage, remediator, verifier, warden, reporter, rulesmith, onboarder |
| 8 | Policies | app.guild.ai → Credentials → GitHub → Policies | per sentinel ALLOW own 3 repos, DENY the rest; delete the unscoped ALLOW-all row if the policies screen is shown (`guild credentials policy delete 01a1225e-d131-02e7-0000-c3da7083897e`) |
| 9 | ClickHouse loaded | `uv run python -m aegis.ch stats` | findings ≈ 39k, scans and actions non-zero |
| 10 | Test rows gone | `uv run python -m aegis.ch purge-selftest` | no `selftest/repo` on the dashboard |
| 11 | Dashboard on the projector | `http://localhost:8787/` full screen, zoom until Agents + Latest push + Handoff guard fill the screen | header ClickHouse online, 3 sentinel cards green, timeline drawn |
| 12 | snipbox fork clean | `cd ~/PycharmProjects/snipbox && git fetch fork && git log --oneline -1 fork/main` | `168f7e8 Initial commit: snipbox 0.4.2`; `gh issue list -R andriidrok1/snipbox` and `gh pr list -R andriidrok1/snipbox` empty; no `aegis/*` branches |
| 13 | `fork` remote exists | `git remote -v` in snipbox | `fork  git@github.com:andriidrok1/snipbox.git` (add: `git remote add fork git@github.com:andriidrok1/snipbox.git`). The trigger is on **andriidrok1/snipbox**, not vincivv/snipbox |
| 14 | Patches ready | `ls demo/snipbox/` | `vuln.patch`, `fix.patch`, `test_search_regression.py`; `git apply --check demo/snipbox/vuln.patch` from the snipbox clone passes |
| 15 | Self-audit runs | `tests/self_audit.sh` | exits 0, "0 ERROR" |
| 16 | Rehearsal run already merged | one earlier vulnerable push on `aegis-demo-target` with its PR merged and Issue closed, tabs bookmarked | step 7 of the script has something to show while the live run catches up |
| 17 | Browser tabs | 1 dashboard · 2 snipbox commits · 3 snipbox Issues · 4 Guild session feed · 5 rehearsal Issue/PR · 6 app.guild.ai policies | all logged in |
| 18 | OBS / recording | screen + mic test, 1080p, dashboard window captured | 10 s test clip plays |
| 19 | Notifications off | Focus mode on, Slack and mail closed | |
| 20 | Fallback video on disk | `<path to recording>` | plays from the start |

## Push commands (the demo)

```bash
cd ~/PycharmProjects/snipbox && git checkout main && git reset --hard fork/main
git am ~/PycharmProjects/aegis/demo/snipbox/vuln.patch     # "feat: snippet search endpoint"
git push fork main                                          # ← the only human action in the demo
```

If `git am` complains: `git apply ~/PycharmProjects/aegis/demo/snipbox/vuln.patch && git commit -am "feat: snippet search endpoint"`.

Then watch: `guild trigger sessions 01a12266-19dd-6639-0000-0ea59dc42e54` (snipbox push trigger) or the Guild session feed. Expected: session in 1 to 4 s, ❌ status ≤ 36 s, triage, `fix_code`, `verify_fix`, one story Issue by ~1.5 min, PR + `aegis:verified` + merge by ~3.5 min, then the merge session turns the status green and closes the Issue.

Optional close-the-loop by hand (if the agent did not merge): `git am ~/PycharmProjects/aegis/demo/snipbox/fix.patch && git push fork main`.

Backup target (same flow, Flask app): `cd ~/PycharmProjects/aegis-demo-target && git checkout origin/verify-test/vuln -- app.py && git commit -m "feat: add /search" && git push origin main`.

## Reset for a second take

```bash
R=andriidrok1/snipbox
gh pr list -R $R --state open --json number --jq '.[].number' | xargs -I{} gh pr close {} -R $R --delete-branch
gh issue list -R $R --state open --limit 200 --json number --jq '.[].number' | xargs -I{} gh issue close {} -R $R -c "reset for rehearsal"
gh api repos/$R/branches --jq '.[].name' | grep '^aegis/' | xargs -I{} gh api -X DELETE repos/$R/git/refs/heads/{}
cd ~/PycharmProjects/snipbox && git reset --hard 168f7e8 && git push --force fork main
```

Wait for the reset push's session to finish before pushing again (`guild trigger sessions <trigger id>`), so the verifier is not racing the next scan. Closing a PR fires `pull_request:closed`, which the sentinel ignores. Close leftovers first or the ClickHouse dedup (`seen_before`, open fingerprint) may keep the agent quiet on the re-push.

Same reset for `aegis-demo-target`: see the block in `docs/LIVE-RUN.md`.

## Fallbacks

| Failure | Symptom | Do this |
|---|---|---|
| ClickHouse down | header shows ClickHouse offline | Keep going. Fleet cards and feed fall back to `state/*.json`; the scanner never blocks on ch.py. Narrate the timeline from a screenshot. |
| Guild not waking | no session after the push | Add the GitHub webhook `https://<tunnel>/webhook/github` (JSON, push) on the repo; `server.py` runs Semgrep and the OpenAI analyst itself. Manual kick: `curl -X POST "localhost:8787/scan?repo=andriidrok1/snipbox" -H "X-AEGIS-Key: $SCANNER_KEY"`. |
| Tunnel URL changed | Guild calls to `aegis-scanner` fail | `./run.sh tunnel`, read `state/tunnel_url.txt`, re-run `fleet/integration.sh` with the new `TUNNEL_URL` (base URL is frozen per published version), redo check 5. |
| Session slow | red status is there, Issue not yet | Narrate over the rehearsal's Issue and PR (tab 5), switch back when the live Issue appears. |
| Anything else | | Play the recording (`<path / URL>`) and narrate over it. |
