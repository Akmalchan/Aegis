"""Handoff guard: Semgrep validates every artifact one agent hands another (code, test, patch, rule).
Agents don't trust each other either. A patch from /fix, a regression test sent to /verify, or any artifact POSTed to
/guard is written to a temp file and scanned with rules/ + p/security-audit + p/secrets (registry falls back to rules/
only). kind=rule runs `semgrep --validate` instead, plus an optional fixture the rule must fire on. Every call is one
`handoff_ok` / `handoff_rejected` row in ClickHouse and a `handoff` event for the dashboard."""
import hashlib, json, logging, shutil, subprocess, tempfile, time
from pathlib import Path
from typing import Literal
from fastapi import APIRouter
from pydantic import BaseModel, Field
from . import scanner, state, ch

log = logging.getLogger("aegis.guard")
router = APIRouter()
EXT = {"python": ".py", "javascript": ".js", "typescript": ".ts", "yaml": ".yml", "go": ".go", "java": ".java",
       "ruby": ".rb", "php": ".php", "jsx": ".jsx", "tsx": ".tsx"}
BLOCKING = ("ERROR", "WARNING")  # INFO (inventory/audit notes) never blocks a handoff


def language_for_path(path: str) -> str:
    ext = Path(path).suffix.lower()
    for lang, e in EXT.items():
        if e == ext:
            return lang
    return {".mjs": "javascript", ".cjs": "javascript", ".yaml": "yaml"}.get(ext, "")


def _sha(content: str) -> str:
    return hashlib.sha1(content.encode(errors="replace")).hexdigest()[:12]


def _validate_rule(content: str, fixture: str | None, fixture_language: str) -> tuple[list[dict], str, bool]:
    """(findings, reason, clean). Findings here are the rule's own problems, shaped like scanner findings."""
    tmp = Path(tempfile.mkdtemp(prefix="aegis-guard-rule-"))
    try:
        rule = tmp / "rule.yml"
        rule.write_text(content)
        p = subprocess.run([scanner.SEMGREP, "--validate", "--metrics=off", "--config", str(rule)],
                           capture_output=True, text=True, timeout=120)
        if p.returncode != 0:
            err = (p.stderr or p.stdout).strip()[-600:]
            f = {"rule_id": "aegis.guard.invalid-rule", "path": "rule.yml", "start_line": 1, "end_line": 1,
                 "lines": "", "message": f"semgrep --validate exit {p.returncode}: {err}", "severity": "ERROR", "cwe": ""}
            f["fingerprint"] = scanner.fingerprint(f)
            return [f], "rule failed semgrep --validate", False
        if fixture:
            fx = tmp / ("fixture" + EXT.get(fixture_language, ".py"))
            fx.write_text(fixture)
            q = subprocess.run([scanner.SEMGREP, "scan", "--json", "--quiet", "--metrics=off", "--config", str(rule), fx.name],
                               cwd=tmp, capture_output=True, text=True, timeout=120)
            n = len((json.loads(q.stdout or "{}") or {}).get("results", []))
            if n == 0:
                f = {"rule_id": "aegis.guard.rule-does-not-fire", "path": fx.name, "start_line": 1, "end_line": 1,
                     "lines": "", "message": "rule is valid but produced 0 findings on the supplied fixture",
                     "severity": "ERROR", "cwe": ""}
                f["fingerprint"] = scanner.fingerprint(f)
                return [f], "valid YAML, but the rule does not fire on its fixture", False
            return [], f"valid rule; fires {n}x on the fixture", True
        return [], "valid rule (no fixture supplied)", True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def guard_artifact(kind: str, language: str, content: str, from_agent: str, to_agent: str, repo: str = "",
                   ref: str = "", baseline: str | None = None, fixture: str | None = None,
                   fixture_language: str = "python") -> dict:
    """Scan one artifact. With `baseline` (the original file for kind=patch) only findings NEW vs the baseline count:
    same rule_id + same matched text in the original = pre-existing, not the patch's fault."""
    t0 = time.time()
    fp = _sha(content)
    findings, baseline_n, scanned = [], 0, True
    if kind == "rule":
        findings, reason, clean = _validate_rule(content, fixture, fixture_language)
    else:
        ext = EXT.get((language or "").lower()) or (Path(ref).suffix if ref else "")
        if not ext:
            scanned, reason, clean = False, f"language {language!r} not scannable; passed through unscanned", True
        else:
            tmp = Path(tempfile.mkdtemp(prefix="aegis-guard-"))
            try:
                names = ["artifact" + ext]
                (tmp / names[0]).write_text(content)
                if baseline is not None:
                    names.append("baseline" + ext)
                    (tmp / names[1]).write_text(baseline)
                all_f = scanner.run_semgrep(tmp, names)
                pre = {(f["rule_id"], f["lines"].strip()) for f in all_f if f["path"] == names[-1]} if baseline is not None else set()
                baseline_n = len(pre)
                for f in all_f:
                    if f["path"] != names[0]:
                        continue
                    f["path"] = ref or f"{kind}:{from_agent}->{to_agent}"
                    f["fingerprint"] = scanner.fingerprint(f)
                    f["pre_existing"] = (f["rule_id"], f["lines"].strip()) in pre
                    findings.append(f)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            blocking = [f for f in findings if f["severity"] in BLOCKING and not f.get("pre_existing")]
            clean = not blocking
            if clean:
                reason = (f"no new findings ({len(findings)} total, {baseline_n} pre-existing in baseline)" if baseline is not None
                          else f"no ERROR/WARNING findings ({len(findings)} INFO)" if findings else "no findings")
            else:
                reason = ("patch introduces " if baseline is not None else "artifact contains ") + \
                         ", ".join(sorted({f["rule_id"] for f in blocking}))
    ms = int((time.time() - t0) * 1000)
    rule_ids = sorted({f["rule_id"] for f in findings if f["severity"] in BLOCKING and not f.get("pre_existing")})
    out = {"clean": clean, "findings": findings, "rule_ids": rule_ids, "bytes": len(content.encode(errors="replace")),
           "ms": ms, "verdict_reason": reason, "kind": kind, "from_agent": from_agent, "to_agent": to_agent,
           "scanned": scanned, "sha": fp}
    action = "handoff_ok" if clean else "handoff_rejected"
    try:
        ch.insert_action(from_agent, repo or "fleet", action, f"{from_agent}->{to_agent}:{kind}", fp, ms)
    except Exception as e:  # noqa
        state.log_event("error", agent=from_agent, repo=repo, stage="clickhouse", error=str(e)[:300])
    state.log_event("handoff", agent=from_agent, to_agent=to_agent, kind_=kind, repo=repo or "fleet", ref=ref,
                    ok=clean, rule_ids=rule_ids, bytes=out["bytes"], ms=ms, reason=reason, sha=fp)
    return out


def handoff_summary(hours: int = 24, last: int = 10) -> dict:
    """For GET /insights: counts in the window + the last N handoffs, from the event log (no ClickHouse needed)."""
    cutoff = time.time() - max(int(hours or 24), 1) * 3600
    ev = [e for e in state.recent_events(2000) if e.get("kind") == "handoff" and e.get("ts", 0) >= cutoff]
    return {"ok": sum(1 for e in ev if e.get("ok")), "rejected": sum(1 for e in ev if not e.get("ok")),
            "last": [{"ts": e["ts"], "from": e.get("agent"), "to": e.get("to_agent"), "kind": e.get("kind_"),
                      "ok": e.get("ok"), "rule_ids": e.get("rule_ids", []), "repo": e.get("repo"), "ref": e.get("ref")}
                     for e in ev[:last]]}


# ---------------------------------------------------------------- endpoint

class GuardIn(BaseModel):
    kind: Literal["code", "test", "patch", "rule"]
    language: str = Field(default="python", description="python | javascript | typescript | yaml | go | java | ruby | php")
    content: str = Field(description="the artifact itself: file source, test source, patched file, or Semgrep rule YAML")
    from_agent: str
    to_agent: str
    repo: str = ""
    ref: str = Field(default="", description="path or label for the artifact, used as `path` in findings")
    baseline: str | None = Field(default=None, description="for kind=patch: the original file; only NEW findings count")
    fixture: str | None = Field(default=None, description="for kind=rule: code the rule must fire on")
    fixture_language: str = "python"


@router.post("/guard", operation_id="guard_artifact",
             summary="Handoff guard: before handing code, a test, a patch or a Semgrep rule to another agent, have Semgrep "
                     "validate it. Returns clean=false with the rule ids when the artifact itself is unsafe (shell=True, "
                     "eval, secrets, injection comments...). kind=rule runs semgrep --validate and, with a fixture, checks "
                     "that the rule fires. Every call is recorded as handoff_ok / handoff_rejected.")
def guard_endpoint(body: GuardIn) -> dict:
    try:
        return guard_artifact(body.kind, body.language, body.content, body.from_agent, body.to_agent, body.repo, body.ref,
                              body.baseline, body.fixture, body.fixture_language)
    except Exception as e:  # noqa
        log.exception("guard failed")
        return {"clean": False, "findings": [], "rule_ids": [], "bytes": len(body.content), "ms": 0,
                "verdict_reason": f"guard error: {type(e).__name__}: {str(e)[:300]}", "kind": body.kind,
                "from_agent": body.from_agent, "to_agent": body.to_agent, "scanned": False, "sha": _sha(body.content)}
