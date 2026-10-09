"""Fallback analyst: OpenAI reasons over the Semgrep finding + surrounding code, then the scanner files the issue itself."""
import json
from pathlib import Path
from openai import OpenAI
from . import config, gh

SYSTEM = """You are AEGIS, an autonomous application-security analyst. You receive a Semgrep finding plus the surrounding
source. Decide whether it is a real, exploitable weakness. Respond ONLY with JSON:
{"confirmed": bool, "confidence": 0-1, "severity": "critical|high|medium|low", "title": str,
 "impact": str, "explanation": str, "fix": str, "fixed_snippet": str}
Be concrete: name the attacker-controlled input, the sink, and the concrete consequence. Keep the fix minimal and correct."""


def _context(workdir: Path, f: dict, radius: int = 25) -> str:
    try:
        lines = (workdir / f["path"]).read_text(errors="replace").splitlines()
    except OSError:
        return f["lines"]
    a = max(0, (f["start_line"] or 1) - 1 - radius)
    b = min(len(lines), (f["end_line"] or f["start_line"] or 1) + radius)
    return "\n".join(f"{i+1:4d}  {lines[i]}" for i in range(a, b))


def investigate(repo: str, sha: str, workdir: Path, f: dict, agent_name: str) -> dict:
    client = OpenAI(api_key=config.OPENAI_API_KEY)
    user = (f"Repository: {repo}\nCommit: {sha}\nFile: {f['path']} lines {f['start_line']}-{f['end_line']}\n"
            f"Semgrep rule: {f['rule_id']} ({f['severity']}, {f['cwe']})\nSemgrep message: {f['message']}\n\n"
            f"Code context:\n```\n{_context(workdir, f)}\n```")
    resp = client.chat.completions.create(
        model=config.OPENAI_MODEL, temperature=0.1,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
    )
    verdict = json.loads(resp.choices[0].message.content)
    if not verdict.get("confirmed") or float(verdict.get("confidence", 0)) < 0.6:
        return {"filed": False, "verdict": verdict}
    body = f"""## {verdict['title']}

**Severity:** {verdict['severity']}  ·  **Confidence:** {verdict['confidence']}  ·  **Rule:** `{f['rule_id']}`  ·  {f['cwe']}
**Location:** `{f['path']}` lines {f['start_line']}–{f['end_line']} @ `{sha[:7]}`

### What is wrong
{verdict['explanation']}

### Impact
{verdict['impact']}

### Recommended fix
{verdict['fix']}

```python
{verdict.get('fixed_snippet','')}
```

### Evidence (Semgrep)
```
{f['lines']}
```
{f['message']}

---
_Filed autonomously by AEGIS agent **{agent_name}** (analyst: OpenAI {config.OPENAI_MODEL}). Push a fix and I will re-scan and close this issue._
<!-- AEGIS-FP: {f['fingerprint']} -->
"""
    number = gh.create_issue(repo, f"[AEGIS] {verdict['title']}", body)
    return {"filed": True, "issue": number, "verdict": verdict}


def verify_close(repo: str, sha: str, number: int, f: dict, agent_name: str) -> None:
    gh.close_issue(repo, number, f"✅ Re-scanned `{f['path']}` at `{sha[:7]}`: finding `{f['rule_id']}` no longer present. "
                               f"Closing. — AEGIS agent **{agent_name}**")
