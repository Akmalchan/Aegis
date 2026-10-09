"""Clone/fetch a repo at a commit and run Semgrep on it (whole repo, a file list, or only what changed since a baseline)."""
import hashlib, json, os, re, shutil, subprocess, sys, threading, time
from pathlib import Path
import httpx
from . import config

SEMGREP = shutil.which("semgrep") or str(Path.home() / ".local/bin/semgrep")
REGISTRY_CONFIGS = ["p/security-audit", "p/secrets"]
REGISTRY_CACHE = config.CACHE_DIR / "registry"
REGISTRY_TTL_S = 6 * 3600
CODE_EXT = (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".java", ".rb", ".php")
ZERO_SHA = "0" * 40
os.environ["GIT_TERMINAL_PROMPT"] = "0"  # a missing/private repo must fail fast, not wait for a password
SEVERITY = {"CRITICAL": "ERROR", "HIGH": "ERROR", "ERROR": "ERROR", "MEDIUM": "WARNING", "WARNING": "WARNING",
            "LOW": "INFO", "INFO": "INFO", "INVENTORY": "INFO", "EXPERIMENT": "INFO"}

_repo_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def repo_lock(repo: str) -> threading.Lock:
    """One checkout per repo at a time: the cache dir is a single working tree."""
    with _locks_guard:
        return _repo_locks.setdefault(repo, threading.Lock())


def _git(workdir: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(workdir), *args], capture_output=True, text=True, check=check)


def checkout(repo: str, sha: str, token: str = "") -> Path:
    """`repo` is owner/name on GitHub, or `local:/path/to/git/repo` (tests). Returns a clean working tree at `sha`."""
    if repo.startswith("local:"):
        src = repo[len("local:"):]
        dest = config.CACHE_DIR / ("local__" + hashlib.sha1(src.encode()).hexdigest()[:10])
        url = src
    else:
        dest = config.CACHE_DIR / repo.replace("/", "__")
        url = f"https://{token + '@' if token else ''}github.com/{repo}.git"
    if not dest.exists():
        subprocess.run(["git", "clone", "--quiet", url, str(dest)], check=True, capture_output=True, text=True)
    _git(dest, "fetch", "--quiet", "--force", "origin", "+refs/heads/*:refs/remotes/origin/*")
    if sha in ("", "HEAD"):
        sha = _git(dest, "rev-parse", "origin/HEAD", check=False).stdout.strip() or "origin/main"
    _git(dest, "checkout", "--quiet", "--force", sha)
    _git(dest, "clean", "-fdxq")  # --baseline-commit needs a clean tree
    return dest


def head_sha(workdir: Path) -> str:
    return _git(workdir, "rev-parse", "HEAD").stdout.strip()


def usable_baseline(workdir: Path, base_sha: str) -> bool:
    """A baseline only works if git has it and it is an ancestor of HEAD (not a new branch / force push)."""
    if not base_sha or base_sha == ZERO_SHA:
        return False
    if _git(workdir, "cat-file", "-e", f"{base_sha}^{{commit}}", check=False).returncode != 0:
        return False
    return _git(workdir, "merge-base", "--is-ancestor", base_sha, "HEAD", check=False).returncode == 0


def fingerprint(f: dict) -> str:
    raw = f"{f['rule_id']}|{f['path']}|{f['lines'].strip()}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def registry_configs() -> list[str]:
    """Registry packs cached as local YAML so a scan doesn't re-download ~0.5 MB of rules each time."""
    REGISTRY_CACHE.mkdir(parents=True, exist_ok=True)
    out = []
    for name in REGISTRY_CONFIGS:
        p = REGISTRY_CACHE / (name.replace("/", "_") + ".yml")
        if not p.exists() or time.time() - p.stat().st_mtime > REGISTRY_TTL_S:
            try:
                r = httpx.get(f"https://semgrep.dev/c/{name}", timeout=20, follow_redirects=True)
                r.raise_for_status()
                p.write_text(r.text)
            except Exception as e:  # noqa
                print(f"[aegis] registry {name} unavailable: {e}", file=sys.stderr)
        if p.exists():
            out.append(str(p))
    return out


def _rule_id(check_id: str) -> str:
    # Semgrep prefixes rules loaded from a path with that path ("Users.me.Aegis.rules.aegis.x").
    for d in (REGISTRY_CACHE, config.RULES_DIR):
        prefix = str(d).lstrip("/").replace("/", ".") + "."
        if check_id.startswith(prefix):
            check_id = check_id[len(prefix):]
    m = re.search(r"(?:^|\.)(aegis\..+)$", check_id)
    return m.group(1) if m else check_id


def _cwe(meta: dict) -> str:
    cwe = meta.get("cwe", "")
    if isinstance(cwe, list):
        cwe = cwe[0] if cwe else ""
    m = re.match(r"\s*(CWE-\d+)", str(cwe))
    return m.group(1) if m else str(cwe)


def _source_lines(workdir: Path, path: str, start: int, end: int) -> str:
    # Semgrep OSS returns "requires login" in extra.lines, so read the matched lines ourselves.
    try:
        text = (workdir / path).read_text(errors="replace").splitlines()
        return "\n".join(text[start - 1:end])
    except Exception:  # noqa
        return ""


def run_semgrep(workdir: Path, files: list[str] | None = None, baseline_commit: str | None = None,
                use_registry: bool = True) -> list[dict]:
    """Scan `files` (default: the whole repo). With `baseline_commit`, only findings absent at that commit are returned."""
    findings, _ = scan(workdir, files, baseline_commit, use_registry)
    return findings


def scan(workdir: Path, files: list[str] | None = None, baseline_commit: str | None = None,
         use_registry: bool = True) -> tuple[list[dict], int]:
    """Like run_semgrep, also returns the number of files Semgrep scanned."""
    if files is not None:
        targets = [f for f in files if (workdir / f).is_file()]
        if not targets:
            return [], 0
    else:
        targets = ["."]
    cmd = [SEMGREP, "scan", "--json", "--quiet", "--metrics=off", "--timeout", "30", "--config", str(config.RULES_DIR)]
    if use_registry:
        for c in registry_configs():
            cmd += ["--config", c]
    if baseline_commit:
        cmd += ["--baseline-commit", baseline_commit]
    cmd += targets
    proc = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, timeout=240)
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        data = {}
    if not data.get("results") and proc.returncode not in (0, 1) and use_registry:
        print(f"[aegis] semgrep exit {proc.returncode}, retrying with bundled rules: {proc.stderr[-300:]}", file=sys.stderr)
        return scan(workdir, files, baseline_commit, use_registry=False)
    out = []
    for r in data.get("results", []):
        extra = r.get("extra", {})
        meta = extra.get("metadata", {}) or {}
        start, end = r.get("start", {}).get("line"), r.get("end", {}).get("line")
        f = {
            "rule_id": _rule_id(r.get("check_id", "")),
            "path": r.get("path", "").removeprefix("./"),
            "start_line": start,
            "end_line": end,
            "lines": _source_lines(workdir, r.get("path", ""), start, end) if start else "",
            "message": extra.get("message", "").strip(),
            "severity": SEVERITY.get(str(extra.get("severity", "")).upper(), "INFO"),
            "cwe": _cwe(meta),
        }
        if extra.get("fix") is not None:
            f["fix"] = extra["fix"]
        if meta.get("fix_hint"):
            f["fix_hint"] = meta["fix_hint"]
        f["fingerprint"] = fingerprint(f)
        out.append(f)
    # dedupe (same line hit by a bundled and a registry rule): prefer ours, they carry fixes
    out.sort(key=lambda f: (not f["rule_id"].startswith("aegis."), "fix" not in f))
    seen, uniq = set(), []
    for f in out:
        key = (f["path"], f["start_line"], f["cwe"] or f["rule_id"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(f)
    rank = {"ERROR": 0, "WARNING": 1, "INFO": 2}
    uniq.sort(key=lambda f: (rank.get(f["severity"], 3), f["path"], f["start_line"] or 0))
    return uniq, len(data.get("paths", {}).get("scanned", []))


def changed_files_from_push(payload: dict) -> list[str]:
    files: set[str] = set()
    for c in payload.get("commits", []):
        files.update(c.get("added", []))
        files.update(c.get("modified", []))
    if not files and payload.get("head_commit"):
        hc = payload["head_commit"]
        files.update(hc.get("added", []) + hc.get("modified", []))
    return sorted(f for f in files if f.endswith(CODE_EXT + (".yml", ".yaml", ".json", ".env")))


def all_tracked_files(workdir: Path) -> list[str]:
    out = _git(workdir, "ls-files").stdout
    return [l for l in out.splitlines() if l.endswith(CODE_EXT)]
