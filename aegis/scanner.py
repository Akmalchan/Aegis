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
    taint_ids = taint_rule_ids()
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
        if extra.get("fix") is not None and extra["fix"].strip() != f["lines"].strip():
            f["fix"] = extra["fix"]  # a fix that rewrites nothing (e.g. self.x = "...") is no fix
        if meta.get("fix_hint"):
            f["fix_hint"] = meta["fix_hint"]
        f["fingerprint"] = fingerprint(f)
        if f["rule_id"] in taint_ids or extra.get("dataflow_trace"):
            f["_taint"] = True  # trace attached below (second pass); flag removed there
        out.append(f)
    attach_dataflow_traces(workdir, out)
    # dedupe (same line hit by a bundled and a registry rule): prefer ours, they carry fixes
    out.sort(key=lambda f: (not f["rule_id"].startswith("aegis."), "fix" not in f))
    seen, uniq = {}, []
    for f in out:
        key = (f["path"], f["start_line"], f["cwe"] or f["rule_id"])
        if key in seen:
            # a taint rule hit the same line as a syntactic rule: keep the survivor's id/fingerprint, carry the trace
            if f.get("dataflow_trace") and "dataflow_trace" not in seen[key]:
                seen[key]["dataflow_trace"], seen[key]["trace_text"] = f["dataflow_trace"], f["trace_text"]
                seen[key]["taint_rule_id"] = f["rule_id"]
            continue
        seen[key] = f
        uniq.append(f)
    rank = {"ERROR": 0, "WARNING": 1, "INFO": 2}
    uniq.sort(key=lambda f: (rank.get(f["severity"], 3), f["path"], f["start_line"] or 0))
    return uniq, len(data.get("paths", {}).get("scanned", []))


# ---------------------------------------------------------------- taint rules + dataflow traces

def taint_rule_files() -> list[Path]:
    """Bundled/learned rule files that contain `mode: taint` rules (traces only exist for those)."""
    out = []
    for p in sorted(config.RULES_DIR.rglob("*.yml")) + sorted(config.RULES_DIR.rglob("*.yaml")):
        try:
            if re.search(r"^\s*mode:\s*taint\s*$", p.read_text(), re.M):
                out.append(p)
        except OSError:
            continue
    return out


def taint_rule_ids() -> set[str]:
    ids: set[str] = set()
    for p in taint_rule_files():
        text = p.read_text()
        # only ids of rules whose block carries `mode: taint` (a file can mix syntactic and taint rules)
        for block in re.split(r"^\s*-\s+id:\s*", text, flags=re.M)[1:]:
            rid = block.split("\n", 1)[0].strip().strip("'\"")
            if re.search(r"^\s*mode:\s*taint\s*$", block, re.M):
                ids.add(rid)
    return ids


_TRACE_SECTIONS = (("Taint comes from", "source"), ("Taint flows through", "propagator"),
                   ("This is how taint reaches the sink", "sink"))


def parse_dataflow_traces(text: str) -> dict[tuple[str, str, int], list[dict]]:
    """Parse `semgrep --dataflow-traces` text output (CE 1.180 puts traces in text only, never in --json).
    Returns {(path, rule_id, match_line): [{kind, path, line, code}, ...]}."""
    traces: dict[tuple[str, str, int], list[dict]] = {}
    path = rule = ""
    section = None       # None | "match" | "source" | "propagator" | "sink"
    steps: list[dict] | None = None
    last: dict | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        s = line.strip()
        if not s:
            continue
        if "\u276f\u276f\u2771" in line:  # ❯❯❱ rule id
            rule = _rule_id(s.split("\u2771", 1)[1].strip())
            section, steps, last = None, None, None
            continue
        if "\u2770\u2770" in line or s.startswith("\u22ee"):  # ❰❰ Blocking ❱❱ / ⋮┆---- separator
            if s.startswith("\u22ee"):
                section, last = None, None
            continue
        m = re.match(r"^\s+(\d+)\u2506 ?(.*)$", line)  # "   28┆ code"
        if m:
            n, code = int(m.group(1)), m.group(2).rstrip()
            if section is None:
                steps = traces.setdefault((path, rule, n), [])
                section, last = "match", None
            elif section in ("source", "propagator", "sink") and steps is not None:
                if section == "propagator" and any(st["kind"] == "source" and st["line"] == n for st in steps):
                    last = None  # Semgrep repeats the source under "flows through"
                    continue
                last = {"kind": section, "path": path, "line": n, "code": code}
                steps.append(last)
                if section == "sink":
                    section = None
            continue
        hit = next((kind for title, kind in _TRACE_SECTIONS if s.startswith(title)), None)
        if hit:
            section, last = hit, None
            continue
        if re.match(r"^ {2,6}\S", line) and "/" in s or re.match(r"^ {2,6}[\w.-]+\.\w+$", line):
            path, section, steps, last = s.removeprefix("./"), None, None, None  # file header line
            continue
        if last is not None and len(line) - len(line.lstrip()) >= 12:
            last["code"] = (last["code"] + " " + s).strip()  # wrapped continuation of a long code line
    return traces


def trace_text(steps: list[dict]) -> str:
    src = [st for st in steps if st["kind"] == "source"]
    mid = [st for st in steps if st["kind"] == "propagator"]
    snk = [st for st in steps if st["kind"] == "sink"]
    if not src or not snk:
        return ""
    loc = lambda st: f"`{st['code']}` ({st['path']}:{st['line']})"  # noqa: E731
    txt = "Semgrep taint trace: taint comes from " + ", ".join(loc(st) for st in src)
    if mid:
        txt += ", flows through " + ", ".join(loc(st) for st in mid)
    txt += ", and reaches the sink " + ", ".join(loc(st) for st in snk) + "."
    return txt


def attach_dataflow_traces(workdir: Path, findings: list[dict]) -> None:
    """Second Semgrep pass (text, --dataflow-traces) restricted to taint rule files and the files with taint findings;
    adds `dataflow_trace` + `trace_text` to those findings. No taint findings => no second pass."""
    taint = [f for f in findings if f.pop("_taint", False)]
    if not taint:
        return
    files = taint_rule_files()
    targets = sorted({f["path"] for f in taint if (workdir / f["path"]).is_file()})
    if not files or not targets:
        return
    cmd = [SEMGREP, "scan", "--quiet", "--metrics=off", "--timeout", "30", "--dataflow-traces"]
    for p in files:
        cmd += ["--config", str(p)]
    try:
        proc = subprocess.run(cmd + targets, cwd=workdir, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return
    traces = parse_dataflow_traces(proc.stdout)
    for f in taint:
        steps = traces.get((f["path"], f["rule_id"], f["start_line"]))
        if steps:
            f["dataflow_trace"] = steps
            f["trace_text"] = trace_text(steps)


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
