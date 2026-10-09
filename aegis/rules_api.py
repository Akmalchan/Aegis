"""Rule gate for the rulesmith. POST /rules/propose runs `semgrep --validate` then `semgrep --test` on a proposed rule
plus its `# ruleid:` / `# ok:` fixture in a temp dir and says yes/no; GET /rules lists bundled + learned rule ids.
Never raises 500: ok=false + errors instead."""
import re, shutil, subprocess, tempfile, time
from pathlib import Path
from fastapi import APIRouter
from pydantic import BaseModel, Field
from . import config, scanner, state

router = APIRouter()
EXT_LANG = {".py": "python", ".js": "javascript", ".ts": "typescript", ".go": "go", ".java": "java", ".rb": "ruby",
            ".php": "php", ".jsx": "javascript", ".tsx": "typescript"}


class ProposeIn(BaseModel):
    rule_yaml: str = Field(description="Semgrep rule file: a `rules:` list with ONE rule")
    fixture_code: str = Field(description="test file with `# ruleid: <id>` above lines that must match and `# ok: <id>` above lines that must not")
    fixture_path: str | None = Field(default=None, description="file name whose extension picks the language, e.g. app.py (default rule.py)")
    fixed_code: str | None = Field(default=None, description="expected file after the rule's `fix` is applied (only for rules with fix)")
    agent: str = "unknown"


class ProposeOut(BaseModel):
    ok: bool
    validate_ok: bool = False
    test_ok: bool = False
    output: str = ""
    rule_id: str = ""
    errors: list[str] = Field(default_factory=list)
    ms: int = 0


def _ids(text: str) -> list[str]:
    return [m.strip().strip("'\"") for m in re.findall(r"^\s*-\s+id:\s*(\S+)", text, re.M)]


def _run(args: list[str], cwd: Path) -> tuple[int, str]:
    try:
        p = subprocess.run([scanner.SEMGREP, "scan", "--metrics=off", *args], cwd=cwd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return 124, "semgrep timed out after 120s"
    return p.returncode, (p.stdout + ("\n" + p.stderr if p.stderr.strip() else "")).strip()


def _gate(body: ProposeIn) -> ProposeOut:
    ids = _ids(body.rule_yaml)
    errors: list[str] = []
    if len(ids) != 1:
        errors.append(f"rule_yaml must contain exactly one rule id, found {len(ids)}")
    if not re.search(r"ruleid:\s*\S+", body.fixture_code):
        errors.append("fixture_code has no `ruleid:` annotation, the test would pass vacuously")
    rid = ids[0] if ids else ""
    if rid and not re.search(rf"(ruleid|ok):\s*{re.escape(rid)}\b", body.fixture_code):
        errors.append(f"fixture annotations never mention rule id {rid}")
    if errors:
        return ProposeOut(ok=False, rule_id=rid, errors=errors)
    ext = Path(body.fixture_path or "rule.py").suffix.lower() or ".py"
    tmp = Path(tempfile.mkdtemp(prefix="aegis-rule-", dir=str(config.CACHE_DIR)))
    try:
        (tmp / "rule.yml").write_text(body.rule_yaml)
        (tmp / f"rule{ext}").write_text(body.fixture_code)
        if body.fixed_code is not None:
            (tmp / f"rule.fixed{ext}").write_text(body.fixed_code)
        rc_v, out_v = _run(["--validate", "--config", "rule.yml"], tmp)
        validate_ok = rc_v == 0 and "0 configuration error" in out_v
        if not validate_ok:
            errors += [l.strip() for l in out_v.splitlines() if re.search(r"error|invalid", l, re.I)][:10] or ["semgrep --validate failed"]
            return ProposeOut(ok=False, validate_ok=False, output=out_v, rule_id=rid, errors=errors)
        rc_t, out_t = _run(["--test", "--config", "rule.yml", f"rule{ext}"], tmp)
        test_ok = rc_t == 0 and "All tests passed" in out_t and "✖" not in out_t
        if not test_ok:
            errors += [l.strip() for l in out_t.splitlines()
                       if re.search(r"✖|missed|incorrect|failed|error", l, re.I)][:10] or [f"semgrep --test exit {rc_t}"]
        return ProposeOut(ok=validate_ok and test_ok, validate_ok=True, test_ok=test_ok,
                          output=(out_v + "\n\n" + out_t).strip(), rule_id=rid, errors=errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@router.post("/rules/propose", operation_id="propose_rule", response_model=ProposeOut,
             summary="Rule gate: validate a proposed Semgrep rule and run its ruleid:/ok: fixture. ok=false means do not open the PR.")
def propose_rule(body: ProposeIn) -> ProposeOut:
    t0 = time.time()
    try:
        res = _gate(body)
    except Exception as e:  # noqa
        res = ProposeOut(ok=False, errors=[f"{type(e).__name__}: {str(e)[:300]}"])
    res.ms = int((time.time() - t0) * 1000)
    state.log_event("rule_proposed", agent=body.agent, rule_id=res.rule_id, ok=res.ok, validate_ok=res.validate_ok,
                    test_ok=res.test_ok, errors=res.errors[:3], ms=res.ms)
    return res


def _rule_entries(files: list[Path]) -> list[dict]:
    out = []
    for p in files:
        text = p.read_text(errors="replace")
        for block in re.split(r"^\s*-\s+id:\s*", text, flags=re.M)[1:]:
            rid = block.split("\n", 1)[0].strip().strip("'\"")
            out.append({"id": rid, "file": str(p.relative_to(config.RULES_DIR)),
                        "mode": "taint" if re.search(r"^\s*mode:\s*taint\s*$", block, re.M) else "search",
                        "has_fix": bool(re.search(r"^\s*fix(-regex)?:", block, re.M))})
    return out


@router.get("/rules", operation_id="list_rules",
            summary="Rule ids the fleet scans with: bundled (rules/*.yml) and learned (rules/learned/*.yml).")
def list_rules():
    bundled = _rule_entries(sorted(config.RULES_DIR.glob("*.yml")))
    learned_dir = config.RULES_DIR / "learned"
    learned = _rule_entries(sorted(learned_dir.glob("*.yml"))) if learned_dir.is_dir() else []
    return {"bundled": bundled, "learned": learned, "n_bundled": len(bundled), "n_learned": len(learned),
            "taint_ids": sorted(scanner.taint_rule_ids())}
