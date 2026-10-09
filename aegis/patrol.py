"""Patrol: scan freshly pushed public GitHub repos with the bundled rules and stream what it finds.

Opt-in (AEGIS_PATROL=1). Results stay in memory only (never ClickHouse, never GitHub): repo names are masked
and code is never echoed, because this runs on a projector against strangers' code."""
import hashlib, os, shutil, subprocess, threading, time
from collections import deque
from datetime import datetime, timedelta, timezone
import httpx
from . import config, scanner

LOG: deque = deque(maxlen=500)
STATS = {"repos": 0, "dirty": 0, "findings": 0, "secrets": 0, "injection": 0, "started": 0.0, "rules": {}}
_seq = 0
_lock = threading.Lock()
WORK = config.CACHE_DIR / "patrol"
LANGS = ["python", "javascript"]


def _emit(level: str, text: str, **extra) -> None:
    global _seq
    with _lock:
        _seq += 1
        LOG.append({"seq": _seq, "ts": time.time(), "level": level, "text": text, **extra})


def since(seq: int) -> list[dict]:
    with _lock:
        return [e for e in LOG if e["seq"] > seq]


def mask(full_name: str, lang: str) -> str:
    return f"{'py' if lang == 'python' else 'js'}-repo-{hashlib.sha1(full_name.encode()).hexdigest()[:5]}"


def _search(lang: str) -> list[dict]:
    day = (datetime.now(timezone.utc) - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
    q = f"language:{lang} pushed:>{day} size:<4000 fork:false archived:false"
    headers = {"Accept": "application/vnd.github+json"}
    if config.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"
    r = httpx.get("https://api.github.com/search/repositories", params={"q": q, "sort": "updated", "order": "desc", "per_page": 30},
                  headers=headers, timeout=20)
    if r.status_code in (403, 429):
        raise RuntimeError("rate limited")
    r.raise_for_status()
    return r.json().get("items", [])


def _scan_one(item: dict, lang: str) -> None:
    name = mask(item["full_name"], lang)
    dest = WORK / hashlib.sha1(item["full_name"].encode()).hexdigest()[:12]
    _emit("info", f"clone  {name}  ({item.get('size', 0)} KB)", repo=name)
    try:
        subprocess.run(["git", "clone", "--depth", "1", "--quiet", item["clone_url"], str(dest)],
                       check=True, capture_output=True, timeout=45)
        _emit("muted", f"scan   {name}  semgrep · aegis rules", repo=name)
        t0 = time.time()
        findings, n_files = scanner.scan(dest, use_registry=False)
        ms = int((time.time() - t0) * 1000)
    except Exception as e:  # noqa
        _emit("muted", f"skip   {name}  ({type(e).__name__})", repo=name)
        return
    finally:
        if dest.exists() and dest.parent == WORK:
            shutil.rmtree(dest, ignore_errors=True)
    STATS["repos"] += 1
    real = [f for f in findings if f["severity"] in ("ERROR", "WARNING")]
    if not real:
        _emit("ok", f"clean  {name}  {n_files} files · {ms} ms", repo=name)
        return
    STATS["dirty"] += 1
    _emit("found", f"found  {name}  {len(real)} issue{'s' if len(real) != 1 else ''} in {n_files} files · {ms} ms", repo=name)
    for f in real[:6]:
        rule = f["rule_id"].replace("aegis.", "")
        STATS["findings"] += 1
        STATS["rules"][rule] = STATS["rules"].get(rule, 0) + 1
        if "secret" in rule:
            STATS["secrets"] += 1
        if "agent-directed" in rule:
            STATS["injection"] += 1
        # path + line only: never the matched code (secrets, and it's someone else's repo)
        path = "/".join(f["path"].split("/")[-2:])  # last two segments: enough to read, not enough to identify
        _emit("hit" if f["severity"] == "ERROR" else "warn", f"{rule}  {path}:{f['start_line']}",
              repo=name, rule=rule, cwe=f.get("cwe", ""), sev=f["severity"], path=path, line=f["start_line"])
    if len(real) > 6:
        _emit("muted", f"  … {len(real) - 6} more", repo=name)


def _run() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    STATS["started"] = time.time()
    seen: set[str] = set()
    _emit("info", "patrol online · scanning freshly pushed public repos with Aegis rules")
    i = 0
    while True:
        lang = LANGS[i % len(LANGS)]
        i += 1
        try:
            items = [it for it in _search(lang) if it["full_name"] not in seen]
        except Exception as e:  # noqa
            _emit("muted", f"github search paused ({e}); retrying in 60 s")
            time.sleep(60)
            continue
        _emit("info", f"search {lang} · {len(items)} new repos pushed in the last 6 h")
        for it in items[:8]:
            seen.add(it["full_name"])
            _scan_one(it, lang)
            time.sleep(1.5)
        time.sleep(4)


def start() -> None:
    if os.getenv("AEGIS_PATROL", "") != "1":
        return
    threading.Thread(target=_run, name="aegis-patrol", daemon=True).start()
