"""Clone/fetch a repo at a commit and run Semgrep on a set of files."""
import hashlib, json, shutil, subprocess
from pathlib import Path
from . import config

SEMGREP = shutil.which("semgrep") or str(Path.home() / ".local/bin/semgrep")
REGISTRY_CONFIGS = ["p/security-audit", "p/secrets"]


def checkout(repo: str, sha: str, token: str = "") -> Path:
    dest = config.CACHE_DIR / repo.replace("/", "__")
    url = f"https://{token + '@' if token else ''}github.com/{repo}.git"
    if not dest.exists():
        subprocess.run(["git", "clone", "--quiet", url, str(dest)], check=True)
    subprocess.run(["git", "-C", str(dest), "fetch", "--quiet", "origin"], check=True)
    subprocess.run(["git", "-C", str(dest), "checkout", "--quiet", "--force", sha], check=True)
    return dest


def fingerprint(f: dict) -> str:
    raw = f"{f['rule_id']}|{f['path']}|{f['lines'].strip()}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def run_semgrep(workdir: Path, files: list[str], use_registry: bool = True) -> list[dict]:
    targets = [f for f in files if (workdir / f).is_file()]
    if not targets:
        return []
    cmd = [SEMGREP, "scan", "--json", "--quiet", "--metrics=off", "--config", str(config.RULES_DIR)]
    if use_registry:
        for c in REGISTRY_CONFIGS:
            cmd += ["--config", c]
    cmd += targets
    proc = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, timeout=240)
    if proc.returncode not in (0, 1) and use_registry:
        # registry unreachable -> retry with bundled rules only
        return run_semgrep(workdir, files, use_registry=False)
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return []
    out = []
    for r in data.get("results", []):
        extra = r.get("extra", {})
        f = {
            "rule_id": r.get("check_id", ""),
            "path": r.get("path", ""),
            "start_line": r.get("start", {}).get("line"),
            "end_line": r.get("end", {}).get("line"),
            "lines": extra.get("lines", ""),
            "message": extra.get("message", ""),
            "severity": extra.get("severity", ""),
            "cwe": extra.get("metadata", {}).get("cwe", ""),
        }
        f["fingerprint"] = fingerprint(f)
        out.append(f)
    # dedupe (same line hit by bundled + registry rule)
    seen, uniq = set(), []
    for f in out:
        key = (f["path"], f["start_line"], f["cwe"] or f["rule_id"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(f)
    return uniq


def changed_files_from_push(payload: dict) -> list[str]:
    files: set[str] = set()
    for c in payload.get("commits", []):
        files.update(c.get("added", []))
        files.update(c.get("modified", []))
    if not files and payload.get("head_commit"):
        hc = payload["head_commit"]
        files.update(hc.get("added", []) + hc.get("modified", []))
    return sorted(f for f in files if f.endswith((".py", ".js", ".ts", ".go", ".java", ".rb", ".php", ".yml", ".yaml", ".json", ".env")))


def all_tracked_files(workdir: Path) -> list[str]:
    out = subprocess.run(["git", "-C", str(workdir), "ls-files"], capture_output=True, text=True).stdout
    return [l for l in out.splitlines() if l.endswith((".py", ".js", ".ts", ".go", ".java", ".rb", ".php"))]
