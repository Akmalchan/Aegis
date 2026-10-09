"""AEGIS scanner. Contract 1 (openapi.yaml): /scan/diff, /scan/full, /actions, /insights — called by Guild agents.
Legacy v1 path kept as fallback: GitHub webhook -> Semgrep -> analyst (Guild.ai agent or OpenAI) -> issue lifecycle."""
import hashlib, hmac, json, os, secrets, subprocess, threading, time
from typing import Literal
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks, Depends, Header
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from . import config, scanner, state, enrich, ch, supply_chain, analyst_guild, analyst_openai
from . import dashboard
from . import github_status, verify, fix, rules_api, guard  # stream V: commit status setter + fix verification + span patcher
from . import sarif  # GitHub code scanning: findings in the repo's Security tab
from . import exposure  # exposure clock: how long each vulnerability has been live

app = FastAPI(title="AEGIS Scanner", version="1.0.0")
dashboard.mount(app)
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


app.include_router(verify.router, dependencies=[Depends(require_key)])
app.include_router(github_status.router, dependencies=[Depends(require_key)])
app.include_router(fix.router, dependencies=[Depends(require_key)])
app.include_router(guard.router, dependencies=[Depends(require_key)])  # handoff guard: Semgrep validates agent-to-agent artifacts
app.include_router(sarif.router, dependencies=[Depends(require_key)])
app.include_router(exposure.router, dependencies=[Depends(require_key)])
app.include_router(exposure.public)


# repo and sha become git argv (clone URL, checkout target): no leading "-", no whitespace, owner/name only
REPO_RE = r"^(local:/\S+|[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)$"
REF_RE = r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$"


class ScanDiffIn(BaseModel):
    repo: str = Field(description="owner/name", pattern=REPO_RE)
    base_sha: str = Field(description="commit before the push", pattern=r"^$|" + REF_RE)
    head_sha: str = Field(description="commit after the push", pattern=REF_RE)
    agent: str = Field(description="calling agent name, e.g. aegis-sentinel-01")


class ScanFullIn(BaseModel):
    repo: str = Field(pattern=REPO_RE)
    sha: str = Field(pattern=REF_RE)
    agent: str


class ActionIn(BaseModel):
    model_config = ConfigDict(extra="allow")  # lenient: status_set may carry state/description
    agent: str
    repo: str
    kind: Literal["status_set", "issue_opened", "issue_closed", "pr_opened", "pr_reviewed", "dismissed", "denied", "email", "verified", "verify_failed", "handoff_ok", "handoff_rejected"]
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
            err = config.redact(e.stderr or str(e))  # str(e) carries argv, i.e. the token-bearing clone URL
            state.log_event("error", agent=agent, repo=repo, stage="checkout", error=err[-300:])
            raise HTTPException(404, f"cannot check out {repo}@{sha}: {config.redact(e.stderr or '').strip()[-200:]}")
        real_sha = scanner.head_sha(workdir)
        baseline = base_sha if trigger != "full" and scanner.usable_baseline(workdir, base_sha) else None
        if trigger != "full" and not baseline:
            trigger = "push-full"  # new branch / force push / unknown base: scan everything
        t1 = time.time()
        findings, n_files = scanner.scan(workdir, baseline_commit=baseline)
        if trigger == "full":
            findings += supply_chain.scan(workdir)  # vulnerable dependencies; [] without SEMGREP_APP_TOKEN
            try:  # Round 2: bare `nosemgrep` suppressions (second pass with --disable-nosem)
                from . import nosem
                findings += nosem.audit(workdir)
            except Exception as e:  # noqa
                state.log_event("error", agent=agent, repo=repo, stage="nosem_audit", error=str(e)[:300])
        semgrep_ms = int((time.time() - t1) * 1000)
        exposure.apply(workdir, repo, findings)  # git blame + ClickHouse history, needs the checkout
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
    sarif.upload_async(repo, real_sha, agent)  # AEGIS_SARIF=1: same findings appear in the repo's Security tab
    return {"repo": repo, "sha": real_sha, "base_sha": baseline or "", "verdict": verdict, "findings": ch.rank(findings),
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
    if body.kind == "status_set":
        return {"ok": True, "status_set": github_status.on_action(body)}
    return {"ok": True}


@app.get("/insights", operation_id="fleet_insights", dependencies=[Depends(require_key)],
         summary="Fleet-wide analytics from ClickHouse for the warden agent.")
def fleet_insights(hours: int = 24):
    try:
        out = ch.insights(hours)
    except Exception as e:  # noqa
        state.log_event("error", stage="insights", error=str(e)[:300])
        out = {"rising_repos": [], "noisy_rules": [], "reopened": [], "agent_latency": []}
    try:
        out["handoffs"] = guard.handoff_summary(hours)  # ok/rejected counts + last 10 agent-to-agent handoffs
    except Exception as e:  # noqa
        out["handoffs"] = {"ok": 0, "rejected": 0, "last": [], "error": str(e)[:200]}
    return out


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


@app.post("/scan", dependencies=[Depends(require_key)])
def manual_scan(repo: str, sha: str = "HEAD", full: bool = True):
    """Manual trigger for demos/tests: scan a repo at a sha (all tracked files by default). Needs X-AEGIS-Key like
    every other endpoint that spends GITHUB_TOKEN / files issues."""
    workdir = scanner.checkout(repo, sha, config.GITHUB_TOKEN)
    files = scanner.all_tracked_files(workdir) if full else []
    import subprocess
    real_sha = subprocess.run(["git", "-C", str(workdir), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    return process_push(repo, real_sha, files, pusher="manual")

app.include_router(rules_api.router, dependencies=[Depends(require_key)])  # rule gate: /rules/propose, /rules
from . import precision, rollout, second_opinion  # noqa: E402  Round 2 Semgrep features
app.include_router(precision.router, dependencies=[Depends(require_key)])  # GET /rules/precision
app.include_router(rollout.router, dependencies=[Depends(require_key)])  # POST /rules/rollout
app.include_router(second_opinion.router, dependencies=[Depends(require_key)])  # POST /second-opinion
