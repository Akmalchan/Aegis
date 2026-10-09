"""Backfill ClickHouse with Semgrep results over the real git history of the fleet (+ a few long-history OSS repos).

    uv run python -m clickhouse.backfill                 # default repo set, 400 commits each, skip repos already loaded
    uv run python clickhouse/backfill.py --dry-run       # scan, print counts, insert nothing
    uv run python -m clickhouse.backfill --repos OWASP/NodeGoat --max-commits 800 --replace

What it records is what the scanner would have recorded had AEGIS watched every push:
for each commit (oldest -> newest along `git rev-list --first-parent HEAD`) it inserts the full snapshot of open
findings into aegis.findings (`new` = fingerprint first seen at this commit, `still_open` = carried over, `resolved` =
disappeared at this commit) with commit_ts = the commit's author date, plus one aegis.scans row (trigger=backfill).

Sampling: when the first-parent history is longer than --max-commits, commits are sampled EVENLY across the whole
history (first and last always included), and each sampled commit is diffed against the previous sampled one.
Scanning is incremental: the first sampled commit scans every tracked code file, later ones only re-scan files changed
since the previous sampled commit (findings in changed/deleted files are dropped and replaced by the new results).
Bundled rules only (rules/), Semgrep --timeout 30 per file.
"""
from __future__ import annotations

import argparse, json, os, subprocess, sys, time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aegis import ch, config, scanner  # noqa: E402

AGENT = "backfill"
CACHE = config.CACHE_DIR / "backfill"
# medium public OSS repos with long histories that our bundled rules actually hit (checked at HEAD, 2026-10-09)
OSS_REPOS = ["sqlmapproject/sqlmap", "buildbot/buildbot", "OWASP/NodeGoat"]
CHUNK = 400  # files per semgrep invocation (keeps argv and the 240 s scanner timeout in check)


def default_repos() -> list[str]:
    fleet = json.loads((ROOT / "fleet.json").read_text())["agents"]
    return [r for repos in fleet.values() for r in repos] + OSS_REPOS


def git(workdir: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(workdir), *args], capture_output=True, text=True, check=True).stdout


def clone(repo: str) -> Path:
    dest = CACHE / repo.replace("/", "__")
    if not dest.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        # gh handles auth (private repos like vincivv/snipbox) without the token ever touching argv or disk
        subprocess.run(["gh", "repo", "clone", repo, str(dest), "--", "--quiet"], check=True, capture_output=True,
                       text=True)
    else:
        subprocess.run(["git", "-C", str(dest), "fetch", "--quiet", "origin"], capture_output=True, text=True)
    ref = subprocess.run(["git", "-C", str(dest), "rev-parse", "--verify", "--quiet", "origin/HEAD"],
                         capture_output=True, text=True).stdout.strip()
    if not ref:
        subprocess.run(["git", "-C", str(dest), "remote", "set-head", "origin", "--auto"], capture_output=True)
        ref = git(dest, "rev-parse", "origin/HEAD").strip()
    return dest


def sample(commits: list, n: int) -> list:
    if len(commits) <= n:
        return commits
    idx = sorted({round(i * (len(commits) - 1) / (n - 1)) for i in range(n)})
    return [commits[i] for i in idx]


def code_files(files: list[str]) -> list[str]:
    return [f for f in files if f.endswith(scanner.CODE_EXT)]


def semgrep(workdir: Path, files: list[str]) -> list[dict]:
    out = []
    for i in range(0, len(files), CHUNK):
        try:
            out += scanner.run_semgrep(workdir, files[i:i + CHUNK], use_registry=False)
        except subprocess.TimeoutExpired:  # one pathological chunk: retry file by file rather than lose them all
            for f in files[i:i + CHUNK]:
                try:
                    out += scanner.run_semgrep(workdir, [f], use_registry=False)
                except subprocess.TimeoutExpired:
                    print(f"  [timeout] {f}", flush=True)
    return out


def existing_rows(repo: str) -> tuple[int, int]:
    """(findings rows, scans rows) already loaded by a previous backfill of `repo`."""
    if not ch.enabled():
        return 0, 0
    p = {"a": AGENT, "r": repo}
    q = lambda t: int(ch._client().query(f"SELECT count() FROM aegis.{t} WHERE agent = {{a:String}} AND repo = {{r:String}}",
                                         parameters=p).result_rows[0][0])
    return q("findings"), q("scans")


def delete_rows(repo: str) -> None:
    for t in ("findings", "scans"):
        ch._client().command(f"ALTER TABLE aegis.{t} DELETE WHERE agent = {{a:String}} AND repo = {{r:String}}",
                             parameters={"a": AGENT, "r": repo}, settings={"mutations_sync": 1})


def backfill_repo(repo: str, max_commits: int, dry_run: bool, skip_existing: bool, replace: bool) -> dict:
    t0 = time.time()
    res = {"repo": repo, "commits": 0, "history": 0, "rows": 0, "head_findings": 0, "skipped": "", "secs": 0.0}
    try:
        if not dry_run:
            have, scans = existing_rows(repo)  # clean repos have scans rows but no findings rows
            if (have or scans) and replace:
                delete_rows(repo)
            elif (have or scans) and skip_existing:
                res.update(skipped=f"already backfilled ({have} findings rows, {scans} scans)", rows=have)
                return res
        wd = clone(repo)
        log = git(wd, "log", "--first-parent", "--reverse", "--format=%H %aI", "origin/HEAD").split("\n")
        history = [tuple(l.split(" ", 1)) for l in log if l.strip()]
        commits = sample(history, max_commits)
        res["history"] = len(history)
        open_: dict[str, dict] = {}  # fingerprint -> finding
        prev = ""
        for i, (sha, author_ts) in enumerate(commits, 1):
            t1 = time.time()
            git(wd, "checkout", "--quiet", "--force", sha)
            if not prev:
                changed = scanner.all_tracked_files(wd)
                stale = set()
            else:
                diff = git(wd, "diff", "--name-only", "--no-renames", prev, sha).splitlines()
                stale = set(diff)                               # anything touched: drop its old findings
                changed = code_files([f for f in diff if (wd / f).is_file()])  # re-scan what still exists
            ts = time.time()
            fresh = semgrep(wd, changed) if changed else []
            semgrep_ms = int((time.time() - ts) * 1000)
            now: dict[str, dict] = {fp: f for fp, f in open_.items() if f["path"] not in stale}
            for f in fresh:
                now.setdefault(f["fingerprint"], f)
            new = [f for fp, f in now.items() if fp not in open_]
            still = [f for fp, f in now.items() if fp in open_]
            gone = [f for fp, f in open_.items() if fp not in now]
            if not dry_run:
                for status, group in (("new", new), ("still_open", still), ("resolved", gone)):
                    ch.insert_findings(AGENT, repo, sha, group, status=status, commit_ts=author_ts)
                ch.insert_scan(AGENT, repo, sha, prev, "backfill", len(changed), len(now),
                               "unsafe" if new else "safe", semgrep_ms, int((time.time() - t1) * 1000))
            res["rows"] += len(new) + len(still) + len(gone)
            open_, prev = now, sha
            res["commits"] = i
            if i % 25 == 0 or i == len(commits):
                print(f"[{repo}] {i}/{len(commits)} commits, {len(open_)} open, {res['rows']} rows", flush=True)
        res["head_findings"] = len(open_)
        git(wd, "checkout", "--quiet", "--force", "origin/HEAD")
    except Exception as e:  # noqa: one bad repo must not kill the run
        res["skipped"] = f"ERROR {type(e).__name__}: {str(e)[:200]}"
    finally:
        res["secs"] = round(time.time() - t0, 1)
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repos", nargs="+", default=None, help="owner/name ... (default: fleet.json repos + OSS set)")
    ap.add_argument("--max-commits", type=int, default=400)
    ap.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 4))
    ap.add_argument("--dry-run", action="store_true", help="scan and count, insert nothing")
    ap.add_argument("--no-skip-existing", dest="skip_existing", action="store_false",
                    help="insert even if the repo already has backfill rows (default: skip such repos)")
    ap.add_argument("--replace", action="store_true", help="delete the repo's existing backfill rows, then reload")
    a = ap.parse_args(argv)
    repos = a.repos or default_repos()

    if not a.dry_run:
        if not ch.enabled():
            print("ClickHouse is not reachable (check CLICKHOUSE_* in .env); use --dry-run to scan only.")
            return 1
        ch.init()
    t0 = time.time()
    print(f"backfill: {len(repos)} repos, max {a.max_commits} commits, {a.workers} workers"
          f"{' (dry run)' if a.dry_run else ''}", flush=True)
    results = []
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        futs = [pool.submit(backfill_repo, r, a.max_commits, a.dry_run, a.skip_existing, a.replace) for r in repos]
        for fut in as_completed(futs):
            r = fut.result()
            results.append(r)
            print(f"[{r['repo']}] done in {r['secs']} s {r['skipped']}", flush=True)
    order = {r: i for i, r in enumerate(repos)}
    results.sort(key=lambda r: order[r["repo"]])
    w = max(len(r) for r in repos)
    print(f"\n{'repo':<{w}}  {'commits':>11}  {'rows':>7}  {'@HEAD':>5}  {'secs':>6}  note")
    for r in results:
        print(f"{r['repo']:<{w}}  {r['commits']:>4}/{r['history']:<6}  {r['rows']:>7}  {r['head_findings']:>5}  "
              f"{r['secs']:>6}  {r['skipped']}")
    print(f"total rows {'counted' if a.dry_run else 'inserted/present'}: {sum(r['rows'] for r in results)}"
          f"   elapsed {time.time() - t0:.1f} s")
    if not a.dry_run:
        print("aegis.findings count():", ch._client().query("SELECT count() FROM aegis.findings").result_rows[0][0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
