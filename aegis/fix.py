"""POST /fix: turn one finding into a minimal, span-only patch the agent can commit.
Deterministic when the Semgrep rule shipped a `fix` (model = "semgrep-rule-fix"); otherwise OpenAI returns ONLY the
replacement for the matched span. The splice is done here in Python, and the code asserts that everything outside the
span (plus at most one `import X` line) is byte-identical to the original. Never raises 500: ok=false + error instead."""
import difflib, json, logging, re, subprocess, time
from pathlib import Path
from fastapi import APIRouter
from pydantic import BaseModel, Field
from . import config, scanner, state, ch, guard

log = logging.getLogger("aegis.fix")
router = APIRouter()
CONTEXT_RADIUS = 30

SYSTEM = """You are AEGIS, an application-security remediation engine. You receive one Semgrep finding and the exact
source span it matched. Return ONLY the replacement text for that span. Rules (hard):
- Same indentation as the original span (count the leading spaces/tabs of each line and keep them).
- Same number of statements, same line count where possible, so the diff reads as a substitution.
- No refactors, no renames, no reordering, no quote-style changes, no formatter, no comments added or removed except
  comments inside the span that themselves are the finding.
- Change only what makes the finding disappear.
Patterns:
- SQL built by concatenation / f-string / % / .format (sqlite3): parameterised query with `?` placeholders and a params
  tuple, e.g. cur.execute("... WHERE title LIKE ?", (f"%{q}%",)). psycopg uses %s. Identifiers cannot be parameters.
- Hardcoded secret: NAME = os.environ.get("<ENV_NAME>", "") where ENV_NAME is an upper-snake name derived from the
  variable (e.g. ADMIN_API_KEY -> SNIPBOX_ADMIN_KEY or ADMIN_API_KEY). Drop a comment line only if it is inside the span.
- debug=True -> debug=False.
- subprocess with shell=True -> list of args, shell removed; use shlex.split only for a constant string.
- yaml.load(x) -> yaml.safe_load(x); pickle.loads on untrusted input -> leave and explain.
Respond ONLY with JSON: {"replacement": "<text for the span, no code fences>", "explanation": "<one or two sentences>",
"needs_import": "<module name the replacement requires but the file does not import, e.g. os>" or null}"""


class FixIn(BaseModel):
    repo: str = Field(description="owner/name, or local:/path for tests")
    sha: str = Field(description="commit to patch against (usually the head of the push)")
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    lines: str = Field(default="", description="exact matched source; used to locate the span verbatim when present")
    rule_id: str = ""
    message: str = ""
    fix: str | None = Field(default=None, description="Semgrep rule fix text; when present no model call is made")
    fix_hint: str | None = None
    cwe: str | None = None
    agent: str = "unknown"


class FixOut(BaseModel):
    ok: bool
    replacement: str = ""
    new_content: str = ""
    diff: str = ""
    explanation: str = ""
    model: str = ""
    span: dict = Field(default_factory=lambda: {"start_line": 0, "end_line": 0})
    ms: int = 0
    error: str | None = None
    guard: dict | None = Field(default=None, description="handoff guard: Semgrep scan of new_content vs the original; "
                                                       "ok=false + error when the patch introduces a new finding")


# ---------------------------------------------------------------- span location / splicing

def _locate(src_lines: list[str], body: FixIn) -> tuple[int, int]:
    """0-based inclusive (a, b). Prefer the verbatim `lines` text; fall back to start_line..end_line."""
    want = [l.rstrip("\r") for l in body.lines.strip("\n").splitlines()] if body.lines.strip() else []
    if want:
        n = len(want)
        # exact match first, then whitespace-insensitive, nearest to the reported start_line
        for strict in (True, False):
            hits = []
            for i in range(0, len(src_lines) - n + 1):
                seg = src_lines[i:i + n]
                same = seg == want if strict else [s.strip() for s in seg] == [w.strip() for w in want]
                if same:
                    hits.append(i)
            if hits:
                i = min(hits, key=lambda h: abs(h - (body.start_line - 1)))
                return i, i + n - 1
    a, b = body.start_line - 1, max(body.end_line, body.start_line) - 1
    if a >= len(src_lines):
        raise ValueError(f"start_line {body.start_line} beyond end of file ({len(src_lines)} lines)")
    return a, min(b, len(src_lines) - 1)


def _indent(s: str) -> str:
    return s[:len(s) - len(s.lstrip(" \t"))]


def _reindent(replacement: str, original_first: str) -> str:
    """Playbook 3.2: if the model/rule text came back flush-left, prepend the original first line's indentation."""
    rep_lines = replacement.strip("\n").splitlines()
    if not rep_lines:
        return replacement
    ind = _indent(original_first)
    if ind and not _indent(rep_lines[0]):
        rep_lines = [ind + l if l.strip() else l for l in rep_lines]
    return "\n".join(rep_lines)


def _strip_fences(s: str) -> str:
    m = re.match(r"^\s*```[a-zA-Z0-9_-]*\n(.*?)\n?```\s*$", s, re.S)
    return m.group(1) if m else s


def _last_top_level_import(src_lines: list[str]) -> int:
    """Index of the last top-level `import x` / `from x import y` line (ignores continuation lines); -1 if none."""
    last = -1
    for i, l in enumerate(src_lines):
        if re.match(r"^(import |from \S+ import )", l):
            last = i
        elif last >= 0 and l and not l[0].isspace() and not l.startswith(("#", ")", "import", "from")) \
                and not l.startswith('"""') and last < i - 1:
            break
    return last


def _after_header(src_lines: list[str]) -> int:
    """Index just past a shebang / encoding line / module docstring, for files with no imports at all."""
    i = 0
    while i < len(src_lines) and (src_lines[i].startswith("#!") or re.match(r"^#.*coding[:=]", src_lines[i])):
        i += 1
    if i < len(src_lines) and re.match(r'^\s*[rRuU]?("""|\'\'\')', src_lines[i]):
        q = '"""' if '"""' in src_lines[i] else "\'\'\'"
        if src_lines[i].count(q) >= 2:
            return i + 1
        for j in range(i + 1, len(src_lines)):
            if q in src_lines[j]:
                return j + 1
    return i


def _has_import(src_lines: list[str], mod: str) -> bool:
    pat = re.compile(rf"^\s*(import\s+{re.escape(mod)}(\s|,|$|\s+as\s)|from\s+{re.escape(mod)}\s+import\s)")
    return any(pat.match(l) for l in src_lines)


def _splice(src_lines: list[str], a: int, b: int, replacement: str, needs_import: str | None) -> tuple[list[str], int | None]:
    rep_lines = replacement.strip("\n").splitlines() if replacement.strip() else []
    out = src_lines[:a] + rep_lines + src_lines[b + 1:]
    imp_idx = None
    if needs_import and not _has_import(src_lines, needs_import):
        li = _last_top_level_import(src_lines)
        imp_idx = li + 1 if li >= 0 else _after_header(src_lines)
        if imp_idx > a:  # imports after the span would shift the span; put it at the top instead
            imp_idx = 0
        out.insert(imp_idx, f"import {needs_import}")
    return out, imp_idx


def _check_outside_identical(src_lines: list[str], out: list[str], a: int, b: int, rep_n: int, imp_idx: int | None) -> None:
    """Hard guarantee: everything outside [a, b] (and the optional import line) is byte-identical."""
    before_src, after_src = src_lines[:a], src_lines[b + 1:]
    off = 1 if imp_idx is not None else 0
    if imp_idx is not None:
        if imp_idx > a:
            raise AssertionError("import inserted inside/after the span")
        before_out = out[:imp_idx] + out[imp_idx + 1:a + 1]
    else:
        before_out = out[:a]
    after_out = out[a + off + rep_n:]
    if before_out != before_src:
        raise AssertionError("content before the span changed")
    if after_out != after_src:
        raise AssertionError("content after the span changed")


# ---------------------------------------------------------------- model

def _context(src_lines: list[str], a: int, b: int) -> str:
    lo, hi = max(0, a - CONTEXT_RADIUS), min(len(src_lines), b + 1 + CONTEXT_RADIUS)
    return "\n".join(f"{i + 1:4d}{'>' if a <= i <= b else ' '} {src_lines[i]}" for i in range(lo, hi))


def _ask_model(body: FixIn, src_lines: list[str], a: int, b: int) -> tuple[str, str, str | None, str]:
    from openai import OpenAI
    if not config.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not configured and the finding carries no rule fix")
    span = "\n".join(src_lines[a:b + 1])
    user = (f"File: {body.path}\nRule: {body.rule_id}  {body.cwe or ''}\nMessage: {body.message}\n"
            f"Fix hint: {body.fix_hint or '(none)'}\n\n"
            f"Context (lines marked '>' are the span to replace; line numbers are NOT part of the code):\n```\n"
            f"{_context(src_lines, a, b)}\n```\n\nSPAN TO REPLACE (lines {a + 1}-{b + 1}), return the replacement for "
            f"exactly this text:\n```\n{span}\n```")
    client = OpenAI(api_key=config.OPENAI_API_KEY)
    resp = client.chat.completions.create(
        model=config.OPENAI_MODEL, temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
    )
    data = json.loads(resp.choices[0].message.content)
    rep = _strip_fences(str(data.get("replacement", "")))
    needs = data.get("needs_import") or None
    if needs is not None:
        needs = str(needs).strip() or None
        if needs and not re.match(r"^[A-Za-z_][A-Za-z0-9_.]*$", needs):
            needs = None
    return rep, str(data.get("explanation", "")), needs, resp.model or config.OPENAI_MODEL


# ---------------------------------------------------------------- route

def _fix(body: FixIn, t0: float) -> FixOut:
    workdir = scanner.checkout(body.repo, body.sha, config.GITHUB_TOKEN)
    target = (workdir / body.path).resolve()
    if workdir.resolve() not in target.parents:
        return FixOut(ok=False, error="path escapes the repository", ms=int((time.time() - t0) * 1000))
    if not target.is_file():
        return FixOut(ok=False, error=f"{body.path} not found at {body.sha[:7]}", ms=int((time.time() - t0) * 1000))
    original = target.read_text(errors="replace")
    nl = "\r\n" if "\r\n" in original else "\n"
    src_lines = original.split(nl)
    trailing_nl = original.endswith(nl)
    if trailing_nl:
        src_lines = src_lines[:-1]

    a, b = _locate(src_lines, body)
    needs_import: str | None = None
    if body.fix is not None and body.fix.strip():
        replacement, explanation, model = body.fix, f"Applied the Semgrep rule fix for {body.rule_id}.", "semgrep-rule-fix"
        m = re.match(r"^\s*import\s+([A-Za-z_][A-Za-z0-9_]*)\s*(;\s*|\n)", replacement)  # rule text inlines `import os;`
        if m:
            needs_import, replacement = m.group(1), replacement[m.end():]
        for mod in ("os", "shlex", "yaml", "secrets", "html"):  # playbook 3.4: the rule text may name a module
            if re.search(rf"\b{mod}\.", replacement) and not _has_import(src_lines, mod):
                needs_import = mod
                break
    else:
        replacement, explanation, needs_import, model = _ask_model(body, src_lines, a, b)
    replacement = _reindent(_strip_fences(replacement), src_lines[a])
    if not replacement.strip():
        return FixOut(ok=False, error="empty replacement", model=model, span={"start_line": a + 1, "end_line": b + 1},
                      ms=int((time.time() - t0) * 1000))
    if replacement.strip() == "\n".join(src_lines[a:b + 1]).strip():
        return FixOut(ok=False, error="replacement is identical to the original span", model=model,
                      explanation=explanation, span={"start_line": a + 1, "end_line": b + 1},
                      ms=int((time.time() - t0) * 1000))

    out, imp_idx = _splice(src_lines, a, b, replacement, needs_import)
    rep_n = len(replacement.strip("\n").splitlines())
    _check_outside_identical(src_lines, out, a, b, rep_n, imp_idx)
    new_content = nl.join(out) + (nl if trailing_nl else "")
    diff = "".join(difflib.unified_diff(original.splitlines(keepends=True), new_content.splitlines(keepends=True),
                                        fromfile=f"a/{body.path}", tofile=f"b/{body.path}"))
    if needs_import and imp_idx is not None:
        explanation = (explanation + f" Added `import {needs_import}` after the existing imports.").strip()
    # handoff guard: the patch is an artifact handed to the agent; Semgrep must not find anything NEW in it
    g = guard.guard_artifact("patch", guard.language_for_path(body.path), new_content,
                             "semgrep-rule-fix" if model == "semgrep-rule-fix" else "openai-fix", body.agent,
                             repo=body.repo, ref=body.path, baseline=original)
    if not g["clean"]:
        return FixOut(ok=False, error=("patch rejected by Semgrep: " + ", ".join(g["rule_ids"])) if g["rule_ids"]
                      else "patch rejected: " + g["verdict_reason"], replacement=replacement,
                      new_content=new_content, diff=diff, explanation=explanation, model=model, guard=g,
                      span={"start_line": a + 1, "end_line": b + 1}, ms=int((time.time() - t0) * 1000))
    return FixOut(ok=True, replacement=replacement, new_content=new_content, diff=diff, explanation=explanation,
                  model=model, guard=g, span={"start_line": a + 1, "end_line": b + 1}, ms=int((time.time() - t0) * 1000))


@router.post("/fix", operation_id="fix_code", response_model=FixOut,
             summary="Minimal span-only patch for one finding: Semgrep rule fix when present, else a model-written "
                     "replacement; returns the full patched file plus a unified diff. Nothing outside the span changes.")
def fix_code(body: FixIn) -> FixOut:
    t0 = time.time()
    try:
        with scanner.repo_lock(body.repo):
            res = _fix(body, t0)
    except subprocess.CalledProcessError as e:
        err = config.redact(e.stderr or str(e))  # str(e) carries argv, i.e. the token-bearing clone URL
        res = FixOut(ok=False, error=f"cannot check out {body.repo}@{body.sha[:7]}: {err[-300:]}",
                     ms=int((time.time() - t0) * 1000))
    except Exception as e:  # noqa
        log.exception("fix failed")
        res = FixOut(ok=False, error=f"{type(e).__name__}: {config.redact(str(e))[:300]}", ms=int((time.time() - t0) * 1000))
    kind = "fix_proposed" if res.ok else "fix_failed"
    try:
        ch.insert_action(body.agent, body.repo, kind, body.sha, "", res.ms)
    except Exception as e:  # noqa
        state.log_event("error", agent=body.agent, repo=body.repo, stage="clickhouse", error=str(e)[:300])
    state.log_event(kind, agent=body.agent, repo=body.repo, sha=body.sha[:7], path=body.path, rule_id=body.rule_id,
                    model=res.model, span=res.span, ms=res.ms, error=res.error)
    return res
