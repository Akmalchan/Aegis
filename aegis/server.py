"""AEGIS scanner. Contract 1 (openapi.yaml): /scan/diff, /scan/full, /actions, /insights — called by Guild agents.
Legacy v1 path kept as fallback: GitHub webhook -> Semgrep -> analyst (Guild.ai agent or OpenAI) -> issue lifecycle."""
import hashlib, hmac, json, os, secrets, subprocess, threading, time
from typing import Literal
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks, Depends, Header
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from . import config, scanner, state, enrich, ch, analyst_guild, analyst_openai

app = FastAPI(title="AEGIS Scanner", version="1.0.0")
_lock = threading.Lock()
SCANNER_KEY = os.getenv("SCANNER_KEY", "")


def analyst():
    return analyst_guild if config.GUILD_ENABLED else analyst_openai


# ---------------------------------------------------------------- Contract 1

def require_key(x_aegis_key: str | None = Header(default=None)) -> None:
    if not SCANNER_KEY:
        raise HTTPException(503, "SCANNER_KEY not configured on the scanner")
    if not x_aegis_key or not secrets.compare_digest(x_aegis_key, SCANNER_KEY):
        raise HTTPException(401, "missing or bad X-AEGIS-Key")


class ScanDiffIn(BaseModel):
    repo: str = Field(description="owner/name")
    base_sha: str = Field(description="commit before the push")
    head_sha: str = Field(description="commit after the push")
    agent: str = Field(description="calling agent name, e.g. aegis-sentinel-01")


class ScanFullIn(BaseModel):
    repo: str
    sha: str
    agent: str


class ActionIn(BaseModel):
    agent: str
    repo: str
    kind: Literal["status_set", "issue_opened", "issue_closed", "pr_opened", "pr_reviewed", "dismissed", "denied", "email"]
    ref: str
    fingerprint: str = ""
    latency_ms: int = 0
    session_url: str = ""


def _scan(repo: str, sha: str, base_sha: str, agent: str, trigger: str) -> dict:
    t0 = time.time()
    state.log_event("wake", agent=agent, repo=repo, sha=sha[:7], base=base_sha[:7], trigger=trigger)
    with scanner.repo_lock(repo):
        try:
            workdir = scanner.checkout(repo, sha, config.GITHUB_TOKEN)
        except subprocess.CalledProcessError as e:
            state.log_event("error", agent=agent, repo=repo, stage="checkout", error=(e.stderr or str(e))[-300:])
            raise HTTPException(404, f"cannot check out {repo}@{sha}: {(e.stderr or '').strip()[-200:]}")
        real_sha = scanner.head_sha(workdir)
        baseline = base_sha if trigger != "full" and scanner.usable_baseline(workdir, base_sha) else None
        if trigger != "full" and not baseline:
            trigger = "push-full"  # new branch / force push / unknown base: scan everything
        t1 = time.time()
        findings, n_files = scanner.scan(workdir, baseline_commit=baseline)
        semgrep_ms = int((time.time() - t1) * 1000)
    enrich.apply(repo, findings)
    verdict = enrich.verdict(findings)
    ms = int((time.time() - t0) * 1000)
    ch_trigger = {"diff": "push", "push-full": "push", "full": "cron"}.get(trigger, trigger)
    try:
        ch.insert_scan(agent, repo, real_sha, baseline or "", ch_trigger, n_files, len(findings), verdict, semgrep_ms, ms)
        ch.insert_findings(agent, repo, real_sha, findings, status="new")
    except Exception as e:  # noqa — ClickHouse must never break a scan
        state.log_event("error", agent=agent, repo=repo, stage="clickhouse", error=str(e)[:300])
    state.log_event("scan", agent=agent, repo=repo, sha=real_sha[:7], verdict=verdict, n_findings=len(findings),
                    baseline=bool(baseline), ms=ms, rules=[f["rule_id"] for f in findings])
    return {"repo": repo, "sha": real_sha, "base_sha": baseline or "", "verdict": verdict, "findings": findings,
            "n_files": n_files, "ms": ms}


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True, "semgrep": os.path.exists(scanner.SEMGREP), "clickhouse": ch.enabled(), "auth": bool(SCANNER_KEY)}


@app.post("/scan/diff", operation_id="scan_diff", dependencies=[Depends(require_key)],
          summary='Scan only what a push introduced (Semgrep --baseline-commit). Answers "did this change make the repo unsafe?"')
def scan_diff(body: ScanDiffIn):
    return _scan(body.repo, body.head_sha, body.base_sha, body.agent, "diff")


@app.post("/scan/full", operation_id="scan_full", dependencies=[Depends(require_key)],
          summary="Scan every tracked file of a repo at a commit.")
def scan_full(body: ScanFullIn):
    return _scan(body.repo, body.sha, "", body.agent, "full")


@app.post("/actions", operation_id="record_action", dependencies=[Depends(require_key)],
          summary="Agent reports an action it took (stored in ClickHouse).")
def record_action(body: ActionIn):
    try:
        ch.insert_action(body.agent, body.repo, body.kind, body.ref, body.fingerprint, body.latency_ms, body.session_url)
    except Exception as e:  # noqa
        state.log_event("error", agent=body.agent, repo=body.repo, stage="clickhouse", error=str(e)[:300])
    state.log_event(body.kind, **body.model_dump(exclude={"kind"}, exclude_defaults=True))
    return {"ok": True}


@app.get("/insights", operation_id="fleet_insights", dependencies=[Depends(require_key)],
         summary="Fleet-wide analytics from ClickHouse for the warden agent.")
def fleet_insights(hours: int = 24):
    try:
        return ch.insights(hours)
    except Exception as e:  # noqa
        state.log_event("error", stage="insights", error=str(e)[:300])
        return {"rising_repos": [], "noisy_rules": [], "reopened": [], "agent_latency": []}


# ---------------------------------------------------------------- legacy v1 (webhook -> analyst)


def _verify(sig: str | None, body: bytes) -> None:
    if not config.GITHUB_WEBHOOK_SECRET:
        return
    mac = "sha256=" + hmac.new(config.GITHUB_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    if not sig or not hmac.compare_digest(mac, sig):
        raise HTTPException(401, "bad signature")


def process_push(repo: str, sha: str, files: list[str], pusher: str = "") -> dict:
    agent = config.agent_for_repo(repo)
    if not agent:
        state.log_event("ignored", repo=repo, reason="repo not assigned to any agent")
        return {"ignored": True}
    with _lock, scanner.repo_lock(repo):
        t0 = time.time()
        state.log_event("wake", agent=agent, repo=repo, sha=sha[:7], files=files, pusher=pusher)
        workdir = scanner.checkout(repo, sha, config.GITHUB_TOKEN)
        # scan changed files now, but also re-check files that have open findings (for verification)
        st = state.load(repo)
        tracked = set(files) | {v["finding"]["path"] for v in st["open"].values()}
        findings = scanner.run_semgrep(workdir, sorted(tracked))
        state.log_event("scan", agent=agent, repo=repo, sha=sha[:7], n_findings=len(findings),
                        ms=int((time.time() - t0) * 1000),
                        rules=[f["rule_id"] for f in findings])
        current = {f["fingerprint"]: f for f in findings}
        result = {"new": [], "resolved": [], "still_open": []}

        # 1) new findings -> investigate -> issue
        for fp, f in current.items():
            if fp in st["open"]:
                result["still_open"].append(fp)
                continue
            state.log_event("investigate", agent=agent, repo=repo, rule=f["rule_id"], path=f["path"], line=f["start_line"],
                            analyst="guild" if config.GUILD_ENABLED else "openai")
            try:
                r = analyst().investigate(repo, sha, workdir, f, agent)
            except Exception as e:  # noqa
                state.log_event("error", agent=agent, repo=repo, stage="investigate", error=str(e)[:300])
                continue
            if r.get("filed"):
                st["open"][fp] = {"issue": r["issue"], "finding": f, "sha": sha, "opened_at": time.time()}
                state.log_event("issue_opened", agent=agent, repo=repo, issue=r["issue"], rule=f["rule_id"],
                                path=f["path"], line=f["start_line"], session=r.get("session"))
                result["new"].append({"fingerprint": fp, "issue": r["issue"]})
            else:
                state.log_event("dismissed", agent=agent, repo=repo, rule=f["rule_id"], path=f["path"],
                                reason=(r.get("verdict") or {}).get("explanation", "")[:200])

        # 2) previously open findings that disappeared -> verify -> close
        for fp in list(st["open"].keys()):
            if fp in current:
                continue
            rec = st["open"][fp]
            if rec["finding"]["path"] not in tracked:
                continue
            try:
                analyst().verify_close(repo, sha, rec["issue"], rec["finding"], agent)
                state.log_event("issue_closed", agent=agent, repo=repo, issue=rec["issue"], rule=rec["finding"]["rule_id"],
                                sha=sha[:7], ttr_s=int(time.time() - rec["opened_at"]))
                st["history"].append({**rec, "closed_at": time.time(), "closed_sha": sha})
                del st["open"][fp]
                result["resolved"].append({"fingerprint": fp, "issue": rec["issue"]})
            except Exception as e:  # noqa
                state.log_event("error", agent=agent, repo=repo, stage="verify", error=str(e)[:300])
        state.save(repo, st)
        return result


@app.post("/webhook/github")
async def github_webhook(request: Request, bg: BackgroundTasks):
    body = await request.body()
    _verify(request.headers.get("X-Hub-Signature-256"), body)
    event = request.headers.get("X-GitHub-Event", "")
    payload = json.loads(body or b"{}")
    if event == "ping":
        return {"pong": True}
    if event != "push":
        return {"ignored": event}
    repo = payload["repository"]["full_name"]
    sha = payload["after"]
    if sha.startswith("0000000"):
        return {"ignored": "branch deleted"}
    files = scanner.changed_files_from_push(payload)
    pusher = payload.get("pusher", {}).get("name", "")
    bg.add_task(process_push, repo, sha, files, pusher)
    return {"queued": True, "repo": repo, "sha": sha[:7], "files": files}


@app.post("/scan")
def manual_scan(repo: str, sha: str = "HEAD", full: bool = True):
    """Manual trigger for demos/tests: scan a repo at a sha (all tracked files by default)."""
    workdir = scanner.checkout(repo, sha, config.GITHUB_TOKEN)
    files = scanner.all_tracked_files(workdir) if full else []
    import subprocess
    real_sha = subprocess.run(["git", "-C", str(workdir), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    return process_push(repo, real_sha, files, pusher="manual")


@app.get("/api/fleet")
def fleet():
    out = []
    for name, repos in config.load_fleet().items():
        rows = []
        for r in repos:
            st = state.load(r)
            rows.append({"repo": r, "open": len(st["open"]), "resolved": len(st["history"])})
        out.append({"agent": name, "repos": rows})
    return {"analyst": "guild" if config.GUILD_ENABLED else "openai", "agents": out, "events": state.recent_events(40)}


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return DASH


DASH = """<!doctype html><html><head><meta charset=utf-8><title>AEGIS</title>
<style>
body{font:14px/1.4 ui-monospace,Menlo,monospace;background:#0b0f14;color:#d6e2ef;margin:0;padding:24px}
h1{font-size:18px;margin:0 0 4px}.sub{color:#7f93a8;margin-bottom:20px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px;margin-bottom:24px}
.card{background:#121923;border:1px solid #1f2a38;border-radius:8px;padding:12px}
.card h2{font-size:13px;margin:0 0 8px;color:#8fd3ff}.repo{display:flex;justify-content:space-between;padding:3px 0;border-top:1px solid #1a2431}
.badge{padding:1px 6px;border-radius:4px;font-size:12px}.open{background:#3b1d1d;color:#ff8a8a}.ok{background:#15301f;color:#7fe0a0}
table{width:100%;border-collapse:collapse}td{padding:4px 8px;border-top:1px solid #1a2431;vertical-align:top}
.k-wake{color:#ffd479}.k-scan{color:#8fd3ff}.k-investigate{color:#c9a7ff}.k-issue_opened{color:#ff8a8a}.k-issue_closed{color:#7fe0a0}.k-error{color:#ff5555}.k-dismissed{color:#7f93a8}
</style></head><body>
<h1>AEGIS · autonomous security network</h1><div class=sub id=sub>loading…</div>
<div class=grid id=fleet></div>
<table id=ev></table>
<script>
async function tick(){const d=await (await fetch('/api/fleet')).json();
document.getElementById('sub').textContent='analyst: '+d.analyst+' · agents: '+d.agents.length+' · repos: '+d.agents.reduce((a,x)=>a+x.repos.length,0);
document.getElementById('fleet').innerHTML=d.agents.map(a=>`<div class=card><h2>${a.agent}</h2>${a.repos.length?a.repos.map(r=>`<div class=repo><span>${r.repo}</span><span><span class="badge ${r.open?'open':'ok'}">${r.open} open</span> <span class="badge ok">${r.resolved} fixed</span></span></div>`).join(''):'<div class=repo><span style="color:#55667a">idle</span></div>'}</div>`).join('');
document.getElementById('ev').innerHTML=d.events.map(e=>{const t=new Date(e.ts*1000).toLocaleTimeString();const {ts,kind,...rest}=e;return `<tr><td style="color:#55667a">${t}</td><td class="k-${kind}">${kind}</td><td>${Object.entries(rest).map(([k,v])=>`<b style="color:#7f93a8">${k}</b>=${typeof v==='object'?JSON.stringify(v):v}`).join(' &nbsp; ')}</td></tr>`}).join('');}
tick();setInterval(tick,2000);
</script></body></html>"""
