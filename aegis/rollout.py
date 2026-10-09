"""Fix once, prevent everywhere. POST /rules/rollout takes a (learned) rule, by id from rules/ or as raw YAML, and runs
ONLY that rule over every fleet repo at HEAD. Returns per-repo hits and records one ClickHouse action (kind `rollout`)
per repo. Optional: publishes the rule to the Semgrep Registry as unlisted, but only when a Semgrep login exists."""
import json, os, re, shutil, subprocess, tempfile, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from fastapi import APIRouter
from pydantic import BaseModel, Field
from . import ch, config, scanner, state

router = APIRouter()


class RolloutIn(BaseModel):
    rule_id: str | None = Field(default=None, description="id of a bundled or learned rule in rules/")
    rule_yaml: str | None = Field(default=None, description="Semgrep rule file (rules: list) to roll out instead of rule_id")
    repos: list[str] | None = Field(default=None, description="owner/name list; default every repo in fleet.json")
    agent: str = "aegis-rulesmith"
    publish: bool = Field(default=False, description="also `semgrep publish --visibility=unlisted` if logged in")


def _ids(text: str) -> list[str]:
    return [m.strip().strip("'\"") for m in re.findall(r"^\s*-\s+id:\s*(\S+)", text, re.M)]


def _rule_file(rule_id: str) -> Path | None:
    for p in sorted(config.RULES_DIR.rglob("*.y*ml")):
        if "tests" in p.parts:
            continue
        try:
            if rule_id in _ids(p.read_text(errors="replace")):
                return p
        except OSError:
            continue
    return None


def semgrep_logged_in() -> bool:
    if os.getenv("SEMGREP_APP_TOKEN"):
        return True
    s = Path.home() / ".semgrep" / "settings.yml"
    return s.exists() and "api_token" in s.read_text(errors="replace")


def _scan_repo(repo: str, cfg: Path, ids: set[str]) -> dict:
    t0 = time.time()
    try:
        with scanner.repo_lock(repo):
            wd = scanner.checkout(repo, "HEAD", config.GITHUB_TOKEN)
            sha = scanner.head_sha(wd)
            p = subprocess.run([scanner.SEMGREP, "scan", "--json", "--quiet", "--metrics=off", "--timeout", "30",
                                "--config", str(cfg), "."], cwd=wd, capture_output=True, text=True, timeout=180)
            data = json.loads(p.stdout or "{}")
            hits = []
            for r in data.get("results", []):
                rid = scanner._rule_id(r.get("check_id", ""))
                if ids and rid not in ids and not any(rid.endswith("." + i) for i in ids):
                    continue
                path, line = r.get("path", "").removeprefix("./"), r.get("start", {}).get("line")
                f = {"rule_id": rid, "path": path, "start_line": line,
                     "lines": scanner._source_lines(wd, path, line, r.get("end", {}).get("line") or line)}
                f["fingerprint"] = scanner.fingerprint(f)
                hits.append(f)
        return {"repo": repo, "sha": sha, "n_hits": len(hits), "hits": hits, "ms": int((time.time() - t0) * 1000)}
    except subprocess.CalledProcessError as e:
        return {"repo": repo, "error": "checkout failed: " + config.redact(e.stderr or "")[-200:], "n_hits": 0, "hits": []}
    except Exception as e:  # noqa
        return {"repo": repo, "error": f"{type(e).__name__}: {str(e)[:200]}", "n_hits": 0, "hits": []}


@router.post("/rules/rollout", operation_id="rollout_rule",
             summary="Fix once, prevent everywhere: run ONE rule (learned rule id or raw YAML) over every fleet repo at "
                     "HEAD and return per-repo hits. Records ClickHouse action kind `rollout` per repo.")
def rollout_rule(body: RolloutIn):
    t0 = time.time()
    if not body.rule_id and not body.rule_yaml:
        return {"ok": False, "error": "pass rule_id or rule_yaml"}
    tmp = Path(tempfile.mkdtemp(prefix="aegis-rollout-", dir=str(config.CACHE_DIR)))
    try:
        if body.rule_yaml:
            cfg = tmp / "rule.yml"
            cfg.write_text(body.rule_yaml)
            ids = set(_ids(body.rule_yaml))
            rule_id = body.rule_id or ",".join(sorted(ids))
        else:
            src = _rule_file(body.rule_id)
            if not src:
                return {"ok": False, "error": f"rule {body.rule_id} not found under rules/"}
            cfg, ids, rule_id = src, {body.rule_id}, body.rule_id
        fleet = config.load_fleet()
        owner = {r: a for a, rs in fleet.items() for r in rs}
        repos = [r for r in (body.repos or list(owner)) if re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", r)]
        with ThreadPoolExecutor(max_workers=6) as ex:
            results = list(ex.map(lambda r: _scan_repo(r, cfg, ids), repos))
        for res in results:
            if "error" not in res:
                ch.insert_action(body.agent, res["repo"], "rollout", rule_id,
                                 res["hits"][0]["fingerprint"] if res["hits"] else "", res.get("ms", 0))
        published = {"attempted": False}
        if body.publish:
            if semgrep_logged_in():
                p = subprocess.run([scanner.SEMGREP, "publish", "--visibility=unlisted", str(cfg)],
                                   capture_output=True, text=True, timeout=60)
                published = {"attempted": True, "ok": p.returncode == 0, "output": (p.stdout + p.stderr)[-400:]}
            else:
                published = {"attempted": False, "reason": "no Semgrep login (semgrep login / SEMGREP_APP_TOKEN); skipped"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    out = {"ok": True, "rule_id": rule_id, "n_repos": len(results),
           "repos_hit": sum(1 for r in results if r["n_hits"]), "total_hits": sum(r["n_hits"] for r in results),
           "results": results, "published": published, "ms": int((time.time() - t0) * 1000)}
    state.log_event("rollout", agent=body.agent, rule_id=rule_id, repos_hit=out["repos_hit"],
                    total_hits=out["total_hits"], ms=out["ms"])
    return out
