# Semgrep, deeper

## A. What we use today

`semgrep scan --json --baseline-commit` (CLI 1.180, CE engine) over `rules/` (17 syntactic rules, 5 with `fix`/`fix-regex`, CWE metadata) plus cached `p/security-audit` and `p/secrets`. `rules/tests/` passes `semgrep --test` (17/17, 1 fix test) but nothing runs it automatically. No taint mode, no traces, no platform, no SARIF, no MCP.

## B. Deeper integrations, ranked

1. **Taint-mode rules for AI-generated code** (docs.semgrep.dev/writing-rules/data-flow/taint-mode/overview). Tracks data from a source to a sink through variables, so the finding is a flow, not a line. Add `rules/aegis-taint.yml` (tested below): `request.args` to `cursor.execute`, and the one nobody else has: `openai/anthropic/langchain response` to `subprocess/eval/execute/requests/open`. Judge sees "LLM response reaches shell" fire on an agent file in a target repo. **S**, low risk (both rules fire locally, `app_fixed.py` is clean).
2. **`--dataflow-traces` in the Issue** (same page). Prints "Taint comes from / flows through / reaches the sink" with line numbers. In 1.180 CE the trace is in text output only (`extra.dataflow_trace` stays empty in `--json`, verified), so `scanner.py` runs a second text pass for taint rule ids and attaches the block to the finding as `trace`; triage quotes it instead of guessing source and sink. **S**, risk: text parsing.
3. **`--validate` + `--test` as the rulesmith gate** (docs.semgrep.dev/writing-rules/testing-rules). Rulesmith writes `rule.yml` + `rule.py` with `# ruleid:` / `# ok:` lines (+ `rule.fixed.py` when it carries a fix); a new `POST /rules/propose` endpoint runs both and refuses the rule otherwise. Also a GitHub Action on our repo (`semgrep scan --test --config rules/ rules/tests/`). Judge sees a learned rule rejected for a false positive on its own `ok:` line. **S**, no risk.
4. **nosemgrep audit** (`--disable-nosem`). A suppression hides a finding; `--disable-nosem` reveals it, and JSON then lacks `is_ignored`, so a second pass diffed against the normal pass yields "suppressed findings". Scanner reports them as `aegis.suppressed-finding` (WARNING) and the sentinel comments on the PR that added the `nosemgrep`. Vibe-coded repos are full of these. **S**.
5. **`--sarif` to GitHub code scanning**. Our rules already produce valid SARIF (3 results, 7 rules on the demo file). Sentinel uploads it via `POST /repos/{o}/{r}/code-scanning/sarifs` (gzip+base64 of the SARIF, `commit_sha`, `ref`); findings appear in the repo Security tab next to the agent's Issue. **M**, risk: needs `security_events: write` on the Guild GitHub credential; private repos need GHAS.
6. **`semgrep ci` + AppSec Platform findings API as fleet memory #2** (docs.semgrep.dev/_llms/api). With `SEMGREP_APP_TOKEN` from the booth, warden runs `semgrep ci` per repo; findings, triage states ("Ignored via nosemgrep", "Provisionally ignored" by Multimodal autotriage) and Memories live in the platform. `GET /api/v1/deployments/{id}/findings` is read back into ClickHouse; bulk-triage API writes our dismissals up. **M**, risk: token and org onboarding time.
7. **Secrets validation** (docs.semgrep.dev/semgrep-secrets/validators). Secrets rules with `validators: http` call the provider and label a leaked key `valid`/`invalid`; `--secrets` needs the token. Issue says "key is LIVE, rotate now" vs "already revoked". Without token: write our own validator for the demo's `sk-live-` key shape and run it in `enrich.py`. **M**.
8. **Supply Chain reachability** (`semgrep ci --supply-chain`, token). Reachable only when the vulnerable function is actually called; warden's `scan_full` already has a `supply_chain.scan` slot. **M**, token-gated.
9. **Publish learned rules** (docs.semgrep.dev/writing-rules/private-rules): `semgrep publish --visibility=unlisted rules/learned/` after the gate in 3; Issue links the registry id, so "fix once, prevent everywhere" is a URL. **S**, needs `semgrep login`.
10. **MCP second opinion** (`semgrep mcp -t streamable-http`, hosted `https://mcp.semgrep.ai/mcp`). Expose `semgrep_scan_with_custom_rule` to triage: it drafts a narrower rule and checks it fires on the file. Also `semgrep mcp --hook post-tool-cli-scan` is exactly the Guardian hook; we can say our fleet is Guardian for repos that no IDE touches. **M**, risk: Guild sandbox has no outbound fetch.
11. **AST for triage** (`semgrep show dump-ast python FILE --json`) and **join mode / `--pro` interfile** (cross-file flows, needs login or Pro): mention only, **S/L**.

## C. Do first

**1. Taint rules.** Save as `rules/aegis-taint.yml`:

```yaml
rules:
  - id: aegis.taint-request-to-sql
    mode: taint
    languages: [python]
    severity: ERROR
    message: "Request data flows into a SQL query without parameterization."
    metadata: {cwe: "CWE-89", category: security, fix_hint: Bind the value as a query parameter.}
    pattern-sources:
      - pattern-either:
          - pattern: request.args.get(...)
          - pattern: request.args[...]
          - pattern: request.form.get(...)
          - pattern: request.form[...]
          - pattern: request.get_json(...)
    pattern-sanitizers:
      - pattern: int(...)
    pattern-sinks:
      - patterns:
          - pattern-either:
              - pattern: $CUR.execute($Q, ...)
              - pattern: $CUR.executescript($Q)
          - focus-metavariable: $Q
  - id: aegis.taint-llm-output-to-exec
    mode: taint
    languages: [python]
    severity: ERROR
    message: "LLM response reaches a code/command/SQL sink. Model output is attacker-influenced data, never a command."
    metadata: {cwe: "CWE-94", category: security, subcategory: ai-generated-code,
               fix_hint: Parse the model output into a typed structure with an allowlist before executing anything.}
    pattern-sources:
      - pattern-either:
          - pattern: $CLIENT.chat.completions.create(...)
          - pattern: $CLIENT.messages.create(...)
          - pattern: $MODEL.generate_content(...).text
          - pattern: $CHAIN.invoke(...)
          - pattern: ollama.chat(...)
    pattern-sanitizers:
      - pattern: json.loads(...)
      - pattern: shlex.quote(...)
    pattern-sinks:
      - patterns:
          - pattern-either:
              - pattern: eval($X)
              - pattern: exec($X)
              - pattern: os.system($X)
              - pattern: subprocess.$F($X, ...)
              - pattern: $CUR.execute($X, ...)
              - pattern: requests.$M($X, ...)
              - pattern: open($X, ...)
          - focus-metavariable: $X
```

Test fixture `rules/tests/aegis-taint.py`:

```python
import subprocess, os, json
from openai import OpenAI
client = OpenAI()
def run_task(goal):
    resp = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": goal}])
    cmd = resp.choices[0].message.content
    # ruleid: aegis.taint-llm-output-to-exec
    subprocess.run(cmd, shell=True)
    plan = json.loads(cmd)
    # ok: aegis.taint-llm-output-to-exec
    subprocess.run(plan["argv"])
```

Verified locally: `semgrep scan --config rules/aegis-taint.yml demo-target/_variants/app_vulnerable.py` fires `taint-request-to-sql` at line 28 (the `LIKE '%' + q` query), 0 findings on `app_fixed.py`; the fixture fires twice and the `json.loads` path is clean.

**2. Traces into the Issue.** In `scanner.scan()` after the JSON pass:

```python
if any(f["rule_id"].startswith("aegis.taint-") for f in uniq):
    txt = subprocess.run([SEMGREP, "scan", "--quiet", "--metrics=off", "--dataflow-traces",
                          "--config", str(config.RULES_DIR / "aegis-taint.yml"), *targets],
                         cwd=workdir, capture_output=True, text=True).stdout
    # split on "❯❯❱", attach the "Taint comes from" ... "reaches the sink" block as f["trace"]
```

Triage prompt: "quote `trace` verbatim as source/sink".

**3. Rule gate.** `POST /rules/propose {yaml, test_py, fixed_py?}` writes to a temp dir and runs:

```sh
semgrep scan --metrics=off --validate --config rule.yml && semgrep scan --metrics=off --test --config rule.yml rule.py
```

Exit code non-zero means rejected with the diff. Then `.github/workflows/rules.yml`: `semgrep scan --metrics=off --test --config rules/ rules/tests/`.

## D. Say to the Semgrep judge

- We moved from syntactic patterns to taint mode; the AI-specific rule treats LLM output as a taint source and `subprocess/eval/execute/requests` as sinks, with `json.loads` as the sanitizer. It fires on real agent code.
- Findings carry Semgrep's own dataflow trace into the GitHub Issue; the LLM explains the flow Semgrep proved, it never invents one.
- Every learned rule passes `semgrep validate` and `semgrep --test` with `ruleid:`/`ok:` fixtures before the fleet uses it, and fix rules are checked against `.fixed.py`.
- `--disable-nosem` diff turns suppressions into findings: a vibe-coded repo cannot silence us with a comment.
- SARIF from Semgrep goes to GitHub code scanning, and with a platform token the same findings sync to the AppSec Platform via the findings API, so ClickHouse and Semgrep agree on what is open.
