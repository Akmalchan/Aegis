"""aegis — the AEGIS scanner on your own machine. Same engine and rules as the agent fleet, no server, no accounts.

  aegis scan [PATH | owner/repo]   scan a repo (default: current directory), exit 1 if unsafe
      --diff BASE                  only what changed since BASE (a commit / branch), like a push
      --sarif FILE                 also write SARIF (GitHub code scanning format)
      --json                       machine-readable findings
      --fail-on error|warning|never
  aegis fleet [--url URL]          headline numbers of a running AEGIS fleet
  aegis watch [--url URL]          live feed of what the agents are doing
"""
from __future__ import annotations
import argparse, json, os, re, shutil, sys, time
from pathlib import Path

# ------------------------------------------------------------------ output

_COLOR = sys.stdout.isatty() and not os.getenv("NO_COLOR")


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _COLOR else s


def red(s): return _c("31;1", s)
def yel(s): return _c("33;1", s)
def grn(s): return _c("32;1", s)
def dim(s): return _c("2", s)
def bold(s): return _c("1", s)
def cyan(s): return _c("36", s)


SEV = {"ERROR": ("HIGH", red), "WARNING": ("MED ", yel), "INFO": ("LOW ", dim)}
LOGO = "AEGIS" + (" " if _COLOR else "")


def _banner(target: str, mode: str) -> None:
    print(f"{bold('◆ ' + LOGO)} {dim('· semgrep-powered security scan')}")
    print(dim(f"  {mode} {target}"))
    print()


def _finding(f: dict, n: int) -> None:
    label, color = SEV.get(f.get("severity", ""), ("    ", dim))
    rule = f["rule_id"].removeprefix("aegis.") if f["rule_id"].startswith("aegis.") else "semgrep:" + f["rule_id"].split(".")[-1]
    print(f" {color('✗ ' + label)}  {bold(rule)}  {cyan(f['path'] + ':' + str(f['start_line']))}"
          + (dim(f"  {f['cwe']}") if f.get("cwe") else ""))
    code = (f.get("lines") or "").strip().splitlines()
    if code:
        print(dim("        │ ") + code[0][:110])
    if f.get("trace_text"):
        steps = f.get("dataflow_trace") or []
        if steps:
            path = " → ".join(f"{s['kind']} :{s['line']}" for s in steps)
            print(dim("        ⤷ taint ") + path)
    if "exposed_days" in f:
        d = f["exposed_days"]
        clock = grn("new in this change · caught on day 0") if d == 0 else \
            yel(f"exposed {d:,} days") + dim(f" since {f.get('introduced_sha', '')[:7]} ({f.get('introduced_at', '')[:10]})")
        print(dim("        ⏱ ") + clock)
    if f.get("fix"):
        print(dim("        ✓ fix ") + grn(f["fix"].strip().splitlines()[0][:100]))
    elif f.get("fix_hint"):
        print(dim("        ↳ ") + f["fix_hint"].strip().splitlines()[0][:110])
    print()


# ------------------------------------------------------------------ scan

def _need_semgrep() -> bool:
    from . import scanner
    if Path(scanner.SEMGREP).exists() or shutil.which("semgrep"):
        return True
    print(red("semgrep not found.") + " Install it once:  " + bold("pipx install semgrep"), file=sys.stderr)
    return False


def cmd_scan(a: argparse.Namespace) -> int:
    if not _need_semgrep():
        return 2
    from . import config, exposure, scanner
    target = a.target or "."
    is_remote = re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", target) and not Path(target).exists()
    quiet = a.json
    if not quiet:
        _banner(target, "diff since " + a.diff + " ·" if a.diff else "full scan ·")
    t0 = time.time()
    if is_remote:
        workdir = scanner.checkout(target, a.sha or "HEAD", config.GITHUB_TOKEN)
        repo = target
    else:
        workdir = Path(target).resolve()
        if not workdir.is_dir():
            print(red(f"no such directory: {target}"), file=sys.stderr)
            return 2
        repo = ""
    baseline = None
    if a.diff:
        sha = scanner._git(workdir, "rev-parse", a.diff, check=False).stdout.strip()
        if not sha or not scanner.usable_baseline(workdir, sha):
            print(red(f"--diff {a.diff}: not an ancestor of HEAD in {workdir}"), file=sys.stderr)
            return 2
        baseline = sha
    findings, n_files = scanner.scan(workdir, baseline_commit=baseline, use_registry=not a.bundled_only)
    if (workdir / ".git").exists():
        exposure.apply(workdir, repo, findings)
    ms = int((time.time() - t0) * 1000)

    if a.sarif:
        from . import sarif
        Path(a.sarif).write_text(json.dumps(sarif._merge(_sarif_raw(workdir, a.bundled_only), findings), indent=2))
    if a.json:
        print(json.dumps({"target": target, "n_files": n_files, "ms": ms, "findings": findings}, indent=2, default=str))
    else:
        for i, f in enumerate(findings, 1):
            _finding(f, i)
        _summary(findings, n_files, ms, a)
    return _exit_code(findings, a.fail_on)


def _sarif_raw(workdir: Path, bundled_only: bool) -> dict:
    """Semgrep's native SARIF for the same rules (rule docs, tags); merged with our findings by sarif._merge."""
    import subprocess
    from . import config, scanner
    cmd = [scanner.SEMGREP, "scan", "--sarif", "--quiet", "--metrics=off", "--timeout", "30", "--config", str(config.RULES_DIR)]
    if not bundled_only:
        for c in scanner.registry_configs():
            cmd += ["--config", c]
    out = subprocess.run(cmd + ["."], cwd=workdir, capture_output=True, text=True, timeout=300).stdout
    return json.loads(out or '{"version": "2.1.0", "runs": []}')


def _summary(findings: list[dict], n_files: int, ms: int, a) -> None:
    hi = sum(f["severity"] == "ERROR" for f in findings)
    med = sum(f["severity"] == "WARNING" for f in findings)
    fixes = sum(1 for f in findings if f.get("fix"))
    old = [f["exposed_days"] for f in findings if f.get("exposed_days")]
    line = dim("─" * 64)
    print(line)
    if not findings:
        print(f" {grn('✓ SAFE')}  no findings in {n_files} files  {dim(f'{ms/1000:.1f}s')}")
    else:
        verdict = red("✗ UNSAFE") if hi + med else yel("! REVIEW")
        print(f" {verdict}  {bold(str(len(findings)))} findings in {n_files} files"
              f"  {red(str(hi) + ' high')} · {yel(str(med) + ' medium')}  {dim(f'{ms/1000:.1f}s')}")
        if fixes:
            print(f" {grn('✓')} {fixes} with a ready-made Semgrep autofix")
        if old:
            print(f" {yel('⏱')} oldest has been live {max(old):,} days")
    if a.sarif:
        print(f" {dim('→ SARIF written to')} {a.sarif}")
    print(line)


def _exit_code(findings: list[dict], fail_on: str) -> int:
    if fail_on == "never":
        return 0
    bad = ("ERROR",) if fail_on == "error" else ("ERROR", "WARNING")
    return 1 if any(f["severity"] in bad for f in findings) else 0


# ------------------------------------------------------------------ fleet / watch

def _url(a) -> str:
    if a.url:
        return a.url.rstrip("/")
    if os.getenv("AEGIS_URL"):
        return os.environ["AEGIS_URL"].rstrip("/")
    from . import config
    p = config.STATE_DIR / "tunnel_url.txt"
    return p.read_text().strip().rstrip("/") if p.exists() and p.read_text().strip() else "http://127.0.0.1:8787"


def _get(url: str) -> dict:
    import httpx
    r = httpx.get(url, timeout=15, follow_redirects=True)
    r.raise_for_status()
    return r.json()


def cmd_fleet(a) -> int:
    base = _url(a)
    try:
        s = _get(base + "/api/stats")
    except Exception as ex:  # noqa
        print(red(f"cannot reach the fleet at {base}: {ex}"), file=sys.stderr)
        return 2
    try:
        e = _get(base + "/api/exposure?top=3")
    except Exception:  # noqa — older fleet without the exposure clock
        e = {}
    print(f"{bold('◆ AEGIS fleet')} {dim(base)}\n")
    n_find, n_scans = f"{int(s.get('findings', 0)):,}", f"{int(s.get('scans', 0)):,}"
    print(f"  agents {bold(str(s.get('agents', '?')))}   repos {bold(str(s.get('repos', '?')))}   "
          f"findings remembered {bold(n_find)}   scans {bold(n_scans)}")
    if e.get("fixed"):
        print()
        print(f"  {yel('⏱ exposure clock')}  real commit history, {int(e['fixed']) + int(e['open']):,} vulnerabilities tracked")
        print(f"    without AEGIS  a fix took a median of {bold(str(int(e['median_days_to_fix'])) + ' days')}"
              f" {dim('(p90 ' + str(int(e['p90_days_to_fix'])) + ')')}")
        print(f"    still open     {int(e['open'])} bugs, live a median of {int(e['median_open_days']):,} days")
        print(f"    with AEGIS     {grn('caught on day 0')}, at the push that introduces it")
        for r in e.get("longest_open", [])[:3]:
            print(dim(f"      · {r['exposed_days']:,} days  {r['rule_id'].removeprefix('aegis.')}  {r['repo']}  since {r['since']}"))
    return 0


KIND = {"scan": cyan, "wake": cyan, "issue_opened": red, "issue_closed": grn, "pr_opened": grn, "verified": grn,
        "status_set": dim, "denied": red, "error": red, "sarif_uploaded": cyan}


def cmd_watch(a) -> int:
    base = _url(a)
    sys.stdout.reconfigure(line_buffering=True)  # stream lines even when piped
    print(f"{bold('◆ AEGIS watch')} {dim(base + '  · ctrl-c to stop')}\n")
    last = 0.0
    try:
        while True:
            try:
                ev = _get(base + "/api/events?n=40").get("events", [])
            except Exception as ex:  # noqa
                print(red(f"  fleet unreachable: {ex}"))
                time.sleep(5)
                continue
            for e in sorted(ev, key=lambda x: x.get("ts", 0)):
                if e.get("ts", 0) <= last:
                    continue
                last = e["ts"]
                k = e.get("kind", "")
                color = KIND.get(k, lambda s: s)
                detail = " ".join(f"{dim(x)}={str(e[x])[:12] if x in ('ref', 'sha') else e[x]}" for x in ("verdict", "n_findings", "ref", "sha")
                                  if e.get(x) not in (None, "", 0))
                t = time.strftime("%H:%M:%S", time.localtime(e.get("ts", 0)))
                print(f"  {dim(t)}  {color(k.ljust(14))} {bold(str(e.get('agent', '')).ljust(18))} "
                      f"{str(e.get('repo', '')).split('/')[-1].ljust(20)} {detail}")
            time.sleep(2)
    except KeyboardInterrupt:
        return 0


# ------------------------------------------------------------------ main

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="aegis", description="AEGIS: autonomous security for every repo (Semgrep-powered).")
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("scan", help="scan a repo (path or owner/repo); exit 1 if unsafe")
    s.add_argument("target", nargs="?", help="directory (default .) or GitHub owner/repo")
    s.add_argument("--diff", metavar="BASE", help="only findings introduced since BASE (commit or branch)")
    s.add_argument("--sha", help="commit to scan for owner/repo targets (default HEAD)")
    s.add_argument("--sarif", metavar="FILE", help="also write SARIF for GitHub code scanning")
    s.add_argument("--json", action="store_true", help="print findings as JSON")
    s.add_argument("--bundled-only", action="store_true", help="AEGIS rules only, skip the Semgrep registry packs")
    s.add_argument("--fail-on", choices=["error", "warning", "never"], default="warning")
    s.set_defaults(fn=cmd_scan)
    f = sub.add_parser("fleet", help="headline numbers from a running AEGIS fleet")
    f.add_argument("--url", help="fleet URL (default $AEGIS_URL, then the local tunnel, then localhost:8787)")
    f.set_defaults(fn=cmd_fleet)
    w = sub.add_parser("watch", help="live feed of the agents")
    w.add_argument("--url")
    w.set_defaults(fn=cmd_watch)
    a = p.parse_args(argv)
    if not getattr(a, "fn", None):
        p.print_help()
        return 0
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
