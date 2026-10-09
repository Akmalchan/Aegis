"""GitHub code scanning: put AEGIS/Semgrep findings into the repo's own Security tab.

Semgrep writes SARIF (the standard format GitHub code scanning reads); we clean it up (rule ids, real source snippets,
stable fingerprints, GitHub severity scores) and upload it with POST /repos/{o}/{r}/code-scanning/sarifs.
GitHub then shows each finding as a code-scanning alert on the exact line, and closes the alert by itself when a later
upload for the same branch no longer contains it — so every upload must be a FULL scan of the commit, never a diff.

Endpoint: POST /sarif/upload (operationId upload_sarif). Auto mode: AEGIS_SARIF=1 uploads after every scan of a GitHub
repo. CLI: `python -m aegis.sarif build <dir>` (print SARIF) | `python -m aegis.sarif upload <owner/repo> [sha]`."""
from __future__ import annotations
import base64, gzip, json, os, subprocess, sys, threading, time
from pathlib import Path
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from . import ch, config, exposure, scanner, state

router = APIRouter()
API = "https://api.github.com"
CATEGORY = "aegis"  # one analysis per repo+branch; each upload replaces the previous one
TOOL_NAME = "AEGIS (Semgrep)"
# GitHub turns properties."security-severity" into Critical/High/Medium/Low on the alert
SECURITY_SEVERITY = {"error": "8.0", "warning": "5.5", "note": "2.0", "none": "0.0"}
REPO_RE = r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"


def enabled() -> bool:
    return os.getenv("AEGIS_SARIF", "").lower() in ("1", "true", "yes") and bool(config.GITHUB_TOKEN)


# ------------------------------------------------------------------ build

def build(workdir: Path, use_registry: bool = True, repo: str = "") -> dict:
    """Full Semgrep scan of `workdir` as SARIF 2.1.0 holding exactly the findings the agents act on.

    Two inputs, merged: Semgrep's native SARIF (rule docs, CWE/OWASP tags, help links) and scanner.scan() (our
    deduped findings with fingerprints, fix hints and taint traces). Results keep only what scanner.scan() kept,
    so a GitHub alert, an AEGIS Issue and a ClickHouse row are the same finding with the same fingerprint."""
    cmd = [scanner.SEMGREP, "scan", "--sarif", "--quiet", "--metrics=off", "--timeout", "30",
           "--config", str(config.RULES_DIR)]
    if use_registry:
        for c in scanner.registry_configs():
            cmd += ["--config", c]
    proc = subprocess.run(cmd + ["."], cwd=workdir, capture_output=True, text=True, timeout=300)
    try:
        sarif = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        sarif = {}
    if not sarif.get("runs"):
        if use_registry:
            return build(workdir, use_registry=False, repo=repo)
        raise RuntimeError(f"semgrep produced no SARIF (exit {proc.returncode}): {proc.stderr[-300:]}")
    findings, _ = scanner.scan(workdir, use_registry=use_registry)
    if repo:
        exposure.apply(workdir, repo, findings)  # 'Exposed for N days' in the alert text
    return _merge(sarif, findings)


def _key(rule_id: str, path: str, line) -> tuple:
    return rule_id, path.removeprefix("./"), line


def _code_flow(trace: list[dict]) -> list[dict]:
    """Taint trace -> SARIF codeFlows; GitHub renders it as 'Show paths': source -> ... -> sink, each step clickable."""
    locs = [{"location": {"physicalLocation": {"artifactLocation": {"uri": s["path"]}, "region": {"startLine": s["line"]}},
                          "message": {"text": f"{s['kind']}: {s.get('code', '')}"[:200]}}} for s in trace]
    return [{"threadFlows": [{"locations": locs}]}]


def _merge(sarif: dict, findings: list[dict]) -> dict:
    keep = {_key(f["rule_id"], f["path"], f["start_line"]): f for f in findings}
    for run in sarif.get("runs", []):
        driver = run.setdefault("tool", {}).setdefault("driver", {})
        driver["name"] = TOOL_NAME
        driver.setdefault("informationUri", "https://semgrep.dev")
        run["automationDetails"] = {"id": f"{CATEGORY}/"}
        results, seen = [], set()
        for res in run.get("results", []):
            res["ruleId"] = scanner._rule_id(res.get("ruleId", ""))
            loc = (res.get("locations") or [{}])[0].get("physicalLocation", {})
            path = loc.get("artifactLocation", {}).get("uri", "").removeprefix("./")
            region = loc.get("region", {})
            k = _key(res["ruleId"], path, region.get("startLine"))
            f = keep.get(k)
            if not f or k in seen:  # dropped by the scanner's dedupe (same line, same CWE, weaker rule)
                continue
            seen.add(k)
            if "snippet" in region:  # Semgrep OSS writes "requires login" here
                region["snippet"] = {"text": f["lines"]}
            # same id the agents put in Issues (<!-- AEGIS-FP: ... -->), so alert and Issue are the same finding
            res["partialFingerprints"] = {"aegisFingerprint/v1": f["fingerprint"]}
            text = res.get("message", {}).get("text") or f["message"]
            if f.get("trace_text"):
                text += "\n\n" + f["trace_text"]
            if exposure.label(f):
                text += "\n\nAEGIS exposure clock: " + exposure.label(f)
            if f.get("fix_hint"):
                text += "\n\nAEGIS fix: " + f["fix_hint"]
            res["message"] = {"text": text}
            if f.get("dataflow_trace"):
                res["codeFlows"] = _code_flow(f["dataflow_trace"])
            res.setdefault("properties", {}).update(aegis_fingerprint=f["fingerprint"], cwe=f.get("cwe", ""),
                                                   **{k: f[k] for k in ("introduced_sha", "introduced_at", "exposed_days") if k in f})
            results.append(res)
        run["results"] = results
        used = {r["ruleId"] for r in results}
        rules = []
        for rule in driver.get("rules", []):
            rule["id"] = scanner._rule_id(rule.get("id", ""))
            if rule["id"] not in used:  # ship docs only for rules that fired (registry packs define ~300)
                continue
            rule["name"] = rule["id"]
            level = rule.get("defaultConfiguration", {}).get("level", "warning")
            props = rule.setdefault("properties", {})
            props.setdefault("security-severity", SECURITY_SEVERITY.get(level, "5.5"))
            tags = props.setdefault("tags", [])
            if "security" not in tags:
                tags.append("security")
            rules.append(rule)
        driver["rules"] = rules
        # rule indexes in results point into the old (untrimmed) list: drop them, ruleId is enough
        for r in results:
            r.pop("ruleIndex", None)
            r.get("rule", {}).pop("index", None)
    return sarif


def count_results(sarif: dict) -> int:
    return sum(len(r.get("results", [])) for r in sarif.get("runs", []))


# ------------------------------------------------------------------ upload

def _gh(method: str, url: str, **kw) -> httpx.Response:
    headers = {"Authorization": f"Bearer {config.GITHUB_TOKEN}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    return httpx.request(method, url, headers=headers, timeout=30, **kw)


def default_ref(repo: str) -> str:
    r = _gh("GET", f"{API}/repos/{repo}")
    r.raise_for_status()
    return "refs/heads/" + r.json().get("default_branch", "main")


def upload(repo: str, sha: str = "HEAD", ref: str = "", agent: str = "aegis", wait_s: int = 30) -> dict:
    """Full-scan `repo` at `sha` and upload to GitHub code scanning. Raises on failure (caller decides)."""
    if not config.GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN is not set on the scanner")
    t0 = time.time()
    with scanner.repo_lock(repo):
        workdir = scanner.checkout(repo, sha, config.GITHUB_TOKEN)
        real_sha = scanner.head_sha(workdir)
        sarif = build(workdir, repo=repo)
    n = count_results(sarif)
    ref = ref or default_ref(repo)
    payload = {"commit_sha": real_sha, "ref": ref, "tool_name": TOOL_NAME,
               "sarif": base64.b64encode(gzip.compress(json.dumps(sarif).encode())).decode()}
    r = _gh("POST", f"{API}/repos/{repo}/code-scanning/sarifs", json=payload)
    if r.status_code >= 300:
        msg = r.json().get("message", r.text[:200]) if r.headers.get("content-type", "").startswith("application/json") else r.text[:200]
        state.log_event("error", agent=agent, repo=repo, stage="sarif_upload", error=f"{r.status_code} {msg}")
        raise RuntimeError(f"GitHub rejected the SARIF upload ({r.status_code}): {msg}")
    sarif_id = r.json().get("id", "")
    status, errors = "pending", None
    deadline = time.time() + wait_s
    while sarif_id and time.time() < deadline:  # GitHub processes uploads asynchronously (usually a few seconds)
        time.sleep(2)
        s = _gh("GET", f"{API}/repos/{repo}/code-scanning/sarifs/{sarif_id}")
        if s.status_code == 200:
            status, errors = s.json().get("processing_status", status), s.json().get("errors")
            if status in ("complete", "failed"):
                break
    ms = int((time.time() - t0) * 1000)
    out = {"repo": repo, "sha": real_sha, "ref": ref, "sarif_id": sarif_id, "status": status, "n_results": n,
           "errors": errors, "url": f"https://github.com/{repo}/security/code-scanning", "ms": ms}
    state.log_event("sarif_uploaded", agent=agent, repo=repo, sha=real_sha[:7], n_results=n, status=status, ms=ms,
                    url=out["url"])
    try:
        ch.insert_action(agent, repo, "sarif_uploaded", sarif_id or real_sha, latency_ms=ms, session_url=out["url"])
    except Exception:  # noqa — ClickHouse must never break an upload
        pass
    return out


def upload_async(repo: str, sha: str, agent: str = "aegis") -> None:
    """Fire-and-forget upload after a scan (AEGIS_SARIF=1). GitHub repos only."""
    if not enabled() or repo.startswith("local:"):
        return

    def run():
        try:
            upload(repo, sha, agent=agent)
        except Exception as e:  # noqa
            print(f"[aegis] sarif upload {repo}@{sha[:7]} failed: {config.redact(str(e))}", file=sys.stderr)

    threading.Thread(target=run, daemon=True, name=f"sarif-{repo}").start()


# ------------------------------------------------------------------ API

class SarifUploadIn(BaseModel):
    repo: str = Field(description="owner/name", pattern=REPO_RE)
    sha: str = Field(default="HEAD", description="commit to scan (full scan, not a diff)", pattern=r"^(HEAD|[0-9a-fA-F]{7,40})$")
    ref: str = Field(default="", description="branch ref the commit belongs to, e.g. refs/heads/main (default branch if empty)",
                     pattern=r"^$|^refs/(heads|pull|tags)/[A-Za-z0-9_./-]+$")
    agent: str = Field(default="aegis", description="calling agent name")


@router.post("/sarif/upload", operation_id="upload_sarif", tags=["github"],
             summary="Full Semgrep scan of a commit, uploaded to the repo's GitHub Security tab (code scanning alerts).")
def upload_sarif(body: SarifUploadIn):
    try:
        return upload(body.repo, body.sha, body.ref, body.agent)
    except subprocess.CalledProcessError as e:
        raise HTTPException(404, f"cannot check out {body.repo}@{body.sha}")
    except RuntimeError as e:
        raise HTTPException(502, str(e))


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "build":
        print(json.dumps(build(Path(sys.argv[2]).resolve()), indent=2))
    elif len(sys.argv) >= 3 and sys.argv[1] == "upload":
        print(json.dumps(upload(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "HEAD", agent="cli"), indent=2))
    else:
        print(__doc__)
