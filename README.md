# AEGIS — autonomous security network for GitHub repositories

> Cyberdefense Hackathon · SF Tech Week · 2026-10-09 · AWS Builder Loft

A fleet of autonomous security agents. Each agent owns a few repositories, wakes up on every push, scans the change with
**Semgrep**, investigates confirmed findings with an LLM, **files a real GitHub Issue** with impact + fix, and when the developer
pushes a fix it re-scans and **closes the issue itself**. Nobody clicks anything.

**Sponsor tools:** Guild.ai (hosts and runs the analyst agents) · Semgrep (detection) · OpenAI (investigation) · GitHub API (action).

## How it flows

```
developer push ──▶ GitHub webhook ──▶ AEGIS scanner (FastAPI)
                                        │ git checkout @ sha
                                        │ semgrep on changed files (bundled rules + p/security-audit + p/secrets)
                                        │ diff vs. remembered open findings
                                        ├─ NEW finding  ──▶ Guild.ai analyst agent ──▶ reads code via GitHub integration ──▶ opens Issue
                                        └─ GONE finding ──▶ Guild.ai analyst agent ──▶ comments + closes Issue
                                        ▼
                                   dashboard  http://localhost:8787
```

`fleet.json` assigns repos to agents (`aegis-01` … `aegis-NN`). Same agent code, N deployments on Guild. 3 agents × 3 repos for the demo, 100 × 300 is a config change.

If Guild isn't configured the scanner falls back to OpenAI + PyGithub and does the same lifecycle locally (`analyst_openai.py`).

## Run it (scanner)

```bash
uv sync
cp .env.example .env            # fill GITHUB_TOKEN, OPENAI_API_KEY, (GUILD_* when the agent is published)
./run.sh                        # starts uvicorn :8787 + cloudflared tunnel, prints the public URL
```

GitHub → target repo → Settings → Webhooks → Add: `https://<tunnel>.trycloudflare.com/webhook/github`, content type `application/json`, event `push`.

Manual trigger (no webhook needed): `curl -X POST 'localhost:8787/scan?repo=andriidrok1/aegis-demo-target'`

## Run it (Guild agent)

See [`guild-agent/README.md`](guild-agent/README.md).

## Demo script (3 min)

1. Dashboard shows 3 agents, 9 repos, all green.
2. Push `demo-target/_variants/app_vulnerable.py` as `app.py` to `aegis-demo-target` (adds SQL concat + hard-coded key + debug=True).
3. Watch: `wake → scan (3 findings, ~1s) → investigate → issue_opened ×3`. Open the Issues on GitHub: full report with fix.
4. Push `app_fixed.py` as `app.py`.
5. Watch: `wake → scan (0) → issue_closed ×3` with "re-scanned at <sha>" comments. Back to green.

## Layout

```
aegis/            scanner service (FastAPI)
  server.py       webhook, lifecycle, dashboard
  scanner.py      git checkout + semgrep JSON
  analyst_guild.py   Guild.ai API-trigger client (preferred)
  analyst_openai.py  OpenAI + PyGithub fallback
  state.py / gh.py / config.py
rules/aegis.yml   bundled high-signal Semgrep rules (SQLi concat, hard-coded secret, debug=True, shell=True, yaml.load, eval)
guild-agent/      the analyst agent deployed on Guild.ai
demo-target/      sample Flask app + vulnerable/fixed variants for the demo
fleet.json        agent → repos assignment
```

## Team

Andrii Drok · (add yourselves here)
