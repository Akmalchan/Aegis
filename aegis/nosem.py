"""nosemgrep audit (scan_full only). A `# nosemgrep` comment hides a finding forever and says nothing about why.
Second Semgrep pass with --disable-nosem over only the files that contain `nosemgrep`; every finding that the normal
pass hid and whose suppression comment has no written reason (draft docs/round2-drafts/nosem.yml: comment ends right
after `nosemgrep` or `nosemgrep: <rule-id>`, no `-- why`) becomes an extra finding `aegis.nosemgrep-without-reason`."""
import json, re, subprocess
from pathlib import Path
from . import config, scanner

RULE_ID = "aegis.nosemgrep-without-reason"
# same regex as the draft rule: suppression with no reason after it
BARE = re.compile(r"(#|//)\s*nosemgrep(:\s*([\w.\-, ]+?))?[ \t]*$")
ANY = re.compile(r"(#|//)\s*nosemgrep\b")


def _files_with_nosem(workdir: Path) -> list[str]:
    p = subprocess.run(["git", "-C", str(workdir), "grep", "-l", "-I", "nosemgrep"], capture_output=True, text=True)
    return [f for f in p.stdout.splitlines() if f.endswith(scanner.CODE_EXT)]


def audit(workdir: Path) -> list[dict]:
    files = _files_with_nosem(workdir)
    if not files:
        return []
    cmd = [scanner.SEMGREP, "scan", "--json", "--quiet", "--metrics=off", "--timeout", "30", "--disable-nosem",
           "--config", str(config.RULES_DIR)]
    for c in scanner.registry_configs():
        cmd += ["--config", c]
    try:
        p = subprocess.run(cmd + files, cwd=workdir, capture_output=True, text=True, timeout=180)
        data = json.loads(p.stdout or "{}")
    except Exception:  # noqa
        return []
    out, seen = [], {}
    for r in data.get("results", []):
        path = r.get("path", "").removeprefix("./")
        line = r.get("start", {}).get("line") or 0
        rid = scanner._rule_id(r.get("check_id", ""))
        try:
            src = (workdir / path).read_text(errors="replace").splitlines()
        except OSError:
            continue
        for ln in (line, line - 1):  # Semgrep honours nosemgrep on the matched line or the line above
            if ln < 1 or ln > len(src) or not ANY.search(src[ln - 1]):
                continue
            m = BARE.search(src[ln - 1])
            if not m:
                break  # suppression has a reason: fine
            named = [x.strip() for x in (m.group(3) or "").split(",") if x.strip()]
            if named and not any(rid == n or rid.endswith("." + n) or n.endswith(rid) for n in named):
                break  # comment names other rules; this finding is not suppressed by it
            key = (path, ln)
            if key in seen:  # one finding per comment; list every rule it hides
                prev = seen[key]
                if rid not in prev["suppressed_rule_ids"]:
                    prev["suppressed_rule_ids"].append(rid)
                break
            f = {"rule_id": RULE_ID, "path": path, "start_line": ln, "end_line": ln, "lines": src[ln - 1].strip(),
                 "message": f"`nosemgrep` without a written reason hides {rid} (line {line}). "
                            "Say why after '--' or remove the suppression.",
                 "severity": "WARNING", "cwe": "", "suppressed_rule_ids": [rid], "suppressed_line": line,
                 "fix_hint": "append ' -- <reason>' to the nosemgrep comment, or fix the finding and drop the comment"}
            f["fingerprint"] = scanner.fingerprint(f)
            out.append(f)
            seen[key] = f
            break
    return out
