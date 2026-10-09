"""POST /verify: how does the agent know its fix is right? Three layers, each a {name, passed, details, ms} entry.
  L1 static      the finding is gone at head_sha and the fix introduced no new findings (Semgrep, fingerprint diff vs base)
  L2 regression  the repo's own test suite still passes at head (pytest in a per-repo venv / npm test); skipped if none
  L3 targeted    the agent's regression test FAILS at base_sha (vulnerable) and PASSES at head_sha; skipped if not given
verified = every non-skipped layer passed (L1 is always required). Base and head share the scanner's cached checkout, so
runs are sequential under scanner.repo_lock and the written test file is removed between them."""
import hashlib, json, logging, re, shutil, subprocess, sys, time
from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from . import config, scanner, state, ch, guard

log = logging.getLogger("aegis.verify")
router = APIRouter()
SUITE_TIMEOUT_S, TEST_TIMEOUT_S, INSTALL_TIMEOUT_S = 180, 150, 300
_ENV = {"PYTHONDONTWRITEBYTECODE": "1", "CI": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1", "FLASK_ENV": "testing"}


class VerifyIn(BaseModel):
    repo: str = Field(description="owner/name")
    base_sha: str = Field(description="commit with the vulnerability (e.g. main before the fix)")
    head_sha: str = Field(description="commit with the fix (branch head)")
    fingerprint: str = ""
    rule_id: str = ""
    path: str = ""
    agent: str = "unknown"
    test_code: str | None = Field(default=None, description="pytest file that must fail on base_sha and pass on head_sha")
    test_path: str | None = None


class Layer(dict):
    @staticmethod
    def make(name: str, passed: bool | None, details: str, t0: float) -> dict:
        return {"name": name, "passed": passed, "details": details[:1500], "ms": int((time.time() - t0) * 1000)}


def _run(cmd: list[str], cwd: Path, timeout: int, env: dict | None = None) -> tuple[int, str]:
    """(returncode, tail of combined output). -1 = timeout, -2 = could not start."""
    import os
    try:
        p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, **_ENV, **(env or {})})
        return p.returncode, (p.stdout + "\n" + p.stderr)[-4000:]
    except subprocess.TimeoutExpired:
        return -1, f"timeout after {timeout}s"
    except Exception as e:  # noqa
        return -2, str(e)


def _summary(out: str) -> str:
    """pytest's '14 passed, 1 failed in 0.3s' line, or the last non-empty line."""
    m = [x for x in re.findall(r"^=+ (.*?) =+$", out, re.M) if re.search(r"\d+ (passed|failed|error)", x)]
    if m:
        return m[-1]
    lines = [l for l in out.strip().splitlines() if l.strip()]
    return lines[-1] if lines else ""


# ---------------------------------------------------------------- test environment (python venv / npm)

def _runner(workdir: Path) -> str | None:
    if (workdir / "package.json").exists():
        try:
            if json.loads((workdir / "package.json").read_text()).get("scripts", {}).get("test"):
                return "npm"
        except Exception:  # noqa
            pass
    if any((workdir / f).exists() for f in ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini", "requirements.txt")) \
            or (workdir / "tests").is_dir() or list(workdir.glob("test_*.py")):
        return "pytest"
    return None


def _has_py_tests(workdir: Path) -> bool:
    return (workdir / "tests").is_dir() or bool(list(workdir.glob("test_*.py")) or list(workdir.glob("**/test_*.py")))


def _venv(repo: str, workdir: Path) -> tuple[Path, str]:
    """One venv per repo in .cache (outside the working tree so `git clean -x` keeps it). Re-installs when deps change."""
    venv = config.CACHE_DIR / (repo.replace("/", "__").replace(":", "_") + "__venv")
    py = venv / "bin" / "python"
    deps = "".join((workdir / f).read_text() for f in ("requirements.txt", "pyproject.toml", "setup.py")
                   if (workdir / f).exists())
    stamp = hashlib.sha256(deps.encode()).hexdigest()[:12]  # cache key only; sha256 so the audit scan stays clean
    stamp_file = venv / ".aegis-deps"
    if py.exists() and stamp_file.exists() and stamp_file.read_text() == stamp:
        return py, "venv cached"
    if not py.exists():
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, capture_output=True, timeout=120)
    notes = []
    pip = [str(py), "-m", "pip", "install", "-q"]
    rc, out = _run(pip + ["pytest", "pytest-timeout", "httpx"], workdir, INSTALL_TIMEOUT_S)
    notes.append("pytest " + ("ok" if rc == 0 else "FAILED " + out[-200:]))
    if (workdir / "requirements.txt").exists():
        rc, out = _run(pip + ["-r", "requirements.txt"], workdir, INSTALL_TIMEOUT_S)
        notes.append("requirements.txt " + ("ok" if rc == 0 else "FAILED " + out[-200:]))
    if (workdir / "pyproject.toml").exists() or (workdir / "setup.py").exists():
        rc, out = _run(pip + ["-e", "."], workdir, INSTALL_TIMEOUT_S)
        notes.append("pip -e . " + ("ok" if rc == 0 else "failed (ignored) " + out[-200:]))
    stamp_file.write_text(stamp)
    return py, "; ".join(notes)


def _pytest(py: Path, workdir: Path, targets: list[str], timeout: int) -> tuple[int, str]:
    cmd = [str(py), "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "--timeout", "120", *targets]
    return _run(cmd, workdir, timeout, env={"PYTHONPATH": str(workdir)})


# ---------------------------------------------------------------- layers

def _scan(workdir: Path) -> list[dict]:
    return scanner.run_semgrep(workdir)


def _touched(workdir: Path, base: str, head: str) -> list[str]:
    r = scanner._git(workdir, "diff", "--name-only", "--diff-filter=AMR", base, head, check=False)
    return [l.strip() for l in (r.stdout or "").splitlines() if l.strip()]


def _unparsed(workdir: Path, errors: list[dict], touched: list[str]) -> list[str]:
    """Touched files at head that Semgrep could not parse, or (.py) that do not even compile.
    0 findings in a file nobody could parse is not clean."""
    out = [f"Semgrep could not parse {e['path']}: {e['type']}: {e['message'][:120]}"
           for e in scanner.parse_errors(errors, touched)]
    bad = {e["path"] for e in scanner.parse_errors(errors, touched)}
    for rel in touched:
        if rel.endswith(".py") and rel not in bad and (workdir / rel).is_file():
            rc, out_ = _run([sys.executable, "-c", "import sys; compile(open(sys.argv[1], 'rb').read(), sys.argv[1], 'exec')",
                             str(workdir / rel)], workdir, 60)
            if rc != 0:
                last = [l for l in out_.strip().splitlines() if l.strip()]
                out.append(f"py_compile failed on {rel}: {last[-1][:160] if last else 'exit ' + str(rc)}")
    return out


def _present(findings: list[dict], body: VerifyIn) -> list[dict]:
    return [f for f in findings if (body.fingerprint and f["fingerprint"] == body.fingerprint)
            or (body.rule_id and body.path and f["rule_id"] == body.rule_id and f["path"] == body.path)]


def layer_static(base: list[dict], head: list[dict], body: VerifyIn, t0: float, unparsed: list[str] | None = None) -> dict:
    if unparsed:
        return Layer.make("static", False, "; ".join(unparsed) + f" — 0 findings is not clean ({len(base)} at base, "
                          f"{len(head)} at head)", t0)
    still = _present(head, body)
    base_fps = {f["fingerprint"] for f in base}
    new = [f for f in head if f["fingerprint"] not in base_fps]
    parts = []
    if still:
        parts.append(f"finding still present at head: {still[0]['rule_id']} {still[0]['path']}:{still[0]['start_line']}")
    else:
        parts.append(f"finding gone at {body.head_sha[:7]}" if _present(base, body) or body.fingerprint or body.rule_id
                     else "no target finding given; checked for regressions only")
    if new:
        parts.append("NEW findings introduced by the fix: " + ", ".join(f"{f['rule_id']} {f['path']}:{f['start_line']}" for f in new[:5]))
    else:
        parts.append(f"no new findings vs {body.base_sha[:7]} ({len(base)} at base, {len(head)} at head)")
    return Layer.make("static", not still and not new, "; ".join(parts), t0)


def layer_regression(repo: str, workdir: Path, t0: float) -> dict:
    runner = _runner(workdir)
    if runner == "npm":
        lock = (workdir / "package-lock.json").exists()
        rc, out = _run(["npm", "ci" if lock else "install", "--silent", "--no-audit", "--no-fund"], workdir, INSTALL_TIMEOUT_S)
        if rc != 0:
            return Layer.make("regression", False, "npm install failed: " + out[-300:], t0)
        rc, out = _run(["npm", "test", "--silent"], workdir, SUITE_TIMEOUT_S)
        return Layer.make("regression", rc == 0, f"npm test exit {rc}: {_summary(out)}", t0)
    if runner == "pytest" and _has_py_tests(workdir):
        py, note = _venv(repo, workdir)
        rc, out = _pytest(py, workdir, [], SUITE_TIMEOUT_S)
        if rc == 5:
            return Layer.make("regression", None, "skipped: no tests collected", t0)
        return Layer.make("regression", rc == 0, f"pytest exit {rc}: {_summary(out)} [{note}]", t0)
    return Layer.make("regression", None, "skipped: no test suite found in repo", t0)


def _targeted_run(py: Path, workdir: Path, rel: str, code: str) -> tuple[int, str]:
    p = workdir / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(code)
    try:
        return _pytest(py, workdir, [rel], TEST_TIMEOUT_S)
    finally:
        p.unlink(missing_ok=True)
        scanner._git(workdir, "clean", "-fdxq", "--", str(Path(rel).parts[0]), check=False)
        scanner._git(workdir, "checkout", "--force", "--", ".", check=False)


# ---------------------------------------------------------------- endpoint

def _verify(body: VerifyIn) -> dict:
    t_all = time.time()
    layers, token = [], config.GITHUB_TOKEN
    rel = body.test_path or f"tests/test_aegis_{(body.fingerprint or 'fix')[:12]}.py"
    if ".." in Path(rel).parts or Path(rel).is_absolute():
        raise ValueError("test_path must be relative and inside the repo")

    # --- base: scan + (L3 first half) the regression test must FAIL here
    t0 = time.time()
    base_dir = scanner.checkout(body.repo, body.base_sha, token)
    base_findings = _scan(base_dir)
    base_rc, base_out = None, ""
    test_guard = None  # handoff guard: an agent-authored test is scanned by Semgrep BEFORE it is ever executed
    if body.test_code:
        test_guard = guard.guard_artifact("test", "python", body.test_code, body.agent, "scanner", repo=body.repo, ref=rel)
    if body.test_code and test_guard["clean"]:
        py, _ = _venv(body.repo, base_dir)
        base_rc, base_out = _targeted_run(py, base_dir, rel, body.test_code)

    # --- head: scan (L1), suite (L2), regression test must PASS (L3 second half)
    head_dir = scanner.checkout(body.repo, body.head_sha, token)
    head_findings, head_errors = scanner.run_semgrep_with_errors(head_dir)
    unparsed = _unparsed(head_dir, head_errors, _touched(head_dir, body.base_sha, body.head_sha))
    layers.append(layer_static(base_findings, head_findings, body, t0, unparsed))
    layers.append(layer_regression(body.repo, head_dir, time.time()))
    t3 = time.time()
    if body.test_code and not test_guard["clean"]:
        rejected = Layer.make("targeted_test", False, "test rejected by Semgrep: " + ", ".join(test_guard["rule_ids"]) +
                              " (not executed); " + test_guard["verdict_reason"], t3)
        rejected["guard"] = test_guard
        layers.append(rejected)
    elif body.test_code:
        py, _ = _venv(body.repo, head_dir)
        head_rc, head_out = _targeted_run(py, head_dir, rel, body.test_code)
        fails_on_base, passes_on_head = base_rc == 1, head_rc == 0
        det = (f"{rel}: at base {body.base_sha[:7]} -> {'FAILS (good)' if fails_on_base else 'did not fail (exit ' + str(base_rc) + ') => proves nothing'}"
               f" [{_summary(base_out)}]; at head {body.head_sha[:7]} -> {'PASSES' if passes_on_head else 'FAILS (exit ' + str(head_rc) + ')'}"
               f" [{_summary(head_out)}]")
        layers.append(Layer.make("targeted_test", fails_on_base and passes_on_head, det, t3))
    else:
        layers.append(Layer.make("targeted_test", None, "skipped: no test_code given", t3))

    verified = all(l["passed"] for l in layers if l["passed"] is not None)
    ms = int((time.time() - t_all) * 1000)
    summary = "; ".join(l["details"].split(";")[0] if l["name"] == "static" else
                        ("tests: " + l["details"] if l["name"] == "regression" else "regression test: " + l["details"])
                        for l in layers)
    kind = "verified" if verified else "verify_failed"
    try:
        ch.insert_action(body.agent, body.repo, kind, body.head_sha, body.fingerprint, ms)
    except Exception as e:  # noqa
        state.log_event("error", agent=body.agent, repo=body.repo, stage="clickhouse", error=str(e)[:300])
    state.log_event(kind, agent=body.agent, repo=body.repo, sha=body.head_sha[:7], base=body.base_sha[:7],
                    fingerprint=body.fingerprint, layers={l["name"]: l["passed"] for l in layers}, ms=ms)
    return {"verified": verified, "repo": body.repo, "base_sha": body.base_sha, "head_sha": body.head_sha,
            "fingerprint": body.fingerprint, "layers": layers, "summary": summary, "ms": ms}


@router.post("/verify", operation_id="verify_fix",
             summary="Prove a fix: finding gone + no new findings (Semgrep), repo tests pass, and the agent's regression "
                     "test fails on base_sha and passes on head_sha.")
def verify_fix(body: VerifyIn):
    try:
        with scanner.repo_lock(body.repo):
            return _verify(body)
    except subprocess.CalledProcessError as e:
        err = config.redact(e.stderr or str(e))  # str(e) carries argv, i.e. the token-bearing clone URL
        return JSONResponse(404, {"verified": False, "error": f"cannot check out {body.repo}: {err[-300:]}"})
    except Exception as e:  # noqa
        log.exception("verify failed")
        state.log_event("error", agent=body.agent, repo=body.repo, stage="verify", error=config.redact(str(e))[:300])
        return JSONResponse(500, {"verified": False, "error": config.redact(str(e))[:300]})
