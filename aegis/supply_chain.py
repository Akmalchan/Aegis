"""Semgrep Supply Chain (SCA) for /scan/full: vulnerable dependencies in lockfiles, as Finding dicts.

Only runs when SEMGREP_APP_TOKEN is set (SCA rules come from semgrep.dev). Uses `semgrep ci --dry-run`,
so nothing is uploaded to semgrep.dev; results are read from a local --json-output file.
Never raises: any failure is logged to stderr and yields [].
"""
import hashlib, json, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path

TIMEOUT_S = 180
SEMGREP = os.getenv("SEMGREP_BIN") or shutil.which("semgrep") or str(Path.home() / ".local/bin/semgrep")
SEVERITY = {"CRITICAL": "ERROR", "HIGH": "ERROR", "ERROR": "ERROR", "MEDIUM": "WARNING", "MODERATE": "WARNING",
            "WARNING": "WARNING", "LOW": "INFO", "INFO": "INFO"}


def _log(msg: str) -> None:
    print(f"[supply_chain] {msg}", file=sys.stderr, flush=True)


def _cwe(meta: dict) -> str:
    cwe = meta.get("cwe", "")
    if isinstance(cwe, list):
        cwe = cwe[0] if cwe else ""
    m = re.match(r"\s*(CWE-\d+)", str(cwe))
    return m.group(1) if m else str(cwe)


def _fingerprint(rule_id: str, path: str, lines: str) -> str:
    return hashlib.sha1(f"{rule_id}|{path}|{lines.strip()}".encode()).hexdigest()[:12]


def _source_lines(workdir: Path, path: str, start: int, end: int) -> str:
    try:
        text = (workdir / path).read_text(errors="replace").splitlines()
        return "\n".join(text[start - 1:end])
    except Exception:  # noqa
        return ""


def _to_finding(workdir: Path, r: dict) -> dict | None:
    extra = r.get("extra") or {}
    meta = extra.get("metadata") or {}
    sca = extra.get("sca_info") or {}
    dep = (sca.get("dependency_match") or {}).get("found_dependency") or {}
    advisory = meta.get("sca-vuln-database-identifier") or meta.get("ghsa") or meta.get("cve")
    if isinstance(advisory, list):
        advisory = advisory[0] if advisory else None
    rule_id = str(advisory or r.get("check_id") or "semgrep-supply-chain")
    lockfile = dep.get("lockfile_path") or ""
    if lockfile and os.path.isabs(lockfile):
        try:
            lockfile = str(Path(lockfile).resolve().relative_to(workdir.resolve()))
        except ValueError:
            pass
    reachable = bool(sca.get("reachable"))
    # reachable findings point at the calling code; unreachable ones at the lockfile entry
    path = r.get("path", "") if reachable or not lockfile else lockfile
    start = int((r.get("start") or {}).get("line") or dep.get("line_number") or 0)
    end = int((r.get("end") or {}).get("line") or start)
    if path == lockfile and dep.get("line_number"):
        start = end = int(dep["line_number"])
    lines = extra.get("lines") or ""
    if not lines or lines.strip() == "requires login":
        lines = _source_lines(workdir, path, start, end) if start else ""
    pkg, ver = dep.get("package", ""), dep.get("version", "")
    if not lines and pkg:
        lines = f"{pkg}=={ver}" if ver else pkg
    fixes = meta.get("sca-fix-versions") or []
    fix_txt = ", ".join(f"{k} {v}" for d in fixes if isinstance(d, dict) for k, v in d.items())
    message = (extra.get("message") or meta.get("sca-schema") or rule_id).strip()
    if pkg:
        message = f"{pkg} {ver}: {message}" + (f" (upgrade to: {fix_txt})" if fix_txt else "")
    f = {
        "rule_id": rule_id,
        "path": path,
        "start_line": start,
        "end_line": end,
        "lines": lines,
        "message": message,
        "severity": SEVERITY.get(str(meta.get("sca-severity") or extra.get("severity") or "").upper(), "INFO"),
        "cwe": _cwe(meta),
        "supply_chain": True,
        "package": pkg,
        "version": ver,
        "reachable": reachable,
    }
    f["fingerprint"] = _fingerprint(f["rule_id"], f["path"], f["lines"])
    return f


def scan(workdir: Path) -> list[dict]:
    """Run Semgrep Supply Chain in `workdir` (a git checkout). [] when no token or on any failure."""
    if not os.getenv("SEMGREP_APP_TOKEN"):
        return []
    try:
        workdir = Path(workdir)
        with tempfile.TemporaryDirectory(prefix="aegis-sca-") as tmp:
            out = Path(tmp) / "sca.json"
            cmd = [SEMGREP, "ci", "--supply-chain", "--dry-run", "--json", f"--json-output={out}",
                   "--metrics=off", "--disable-version-check", "--suppress-errors"]
            try:
                p = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, timeout=TIMEOUT_S,
                                   env={**os.environ, "SEMGREP_ENABLE_VERSION_CHECK": "0"})
            except subprocess.TimeoutExpired:
                _log(f"timed out after {TIMEOUT_S}s in {workdir}")
                return []
            raw = out.read_text() if out.exists() and out.stat().st_size else p.stdout
            # semgrep ci exits 1 when blocking findings exist; anything else with no JSON is a real failure
            try:
                data = json.loads(raw)
            except Exception:  # noqa
                _log(f"no JSON output (rc={p.returncode}): {(p.stderr or p.stdout)[-500:]}")
                return []
            if p.returncode not in (0, 1):
                _log(f"rc={p.returncode}, using partial results: {(p.stderr or '')[-300:]}")
            findings, seen = [], set()
            for r in data.get("results", []):
                try:
                    f = _to_finding(workdir, r)
                except Exception as e:  # noqa
                    _log(f"skipping unparseable result {r.get('check_id')}: {e}")
                    continue
                if f and f["fingerprint"] not in seen:
                    seen.add(f["fingerprint"])
                    findings.append(f)
            return findings
    except Exception as e:  # noqa
        _log(f"failed: {e!r}")
        return []
