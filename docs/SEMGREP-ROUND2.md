# Semgrep, round 2

Ranked by judge impact times "one engineer, 45 minutes". Rules marked "tested" passed `semgrep --validate` and `semgrep --test` on 1.180.0 today; the YAML and fixtures are in `/tmp/claude-1000/-home-andrii-Documents-Obsidian-Vault/92fb70f7-b71c-483a-8284-2d4794ff0663/scratchpad/r2/` (`agent.yml`, `agent.py`, `nosem.yml`).

## 1. Hooks on our own agents' tool calls (Guardian pattern)

What: every Guild agent tool that writes code (open PR, push patch, write file) calls `/guard` inside the tool wrapper, before the side effect. The model cannot skip it because the prompt never mentions it. Same thing for humans on our repo: `.claude/settings.json` gets a PostToolUse hook `semgrep mcp -k post-tool-cli-scan -a claude` and a SessionStart hook `semgrep mcp -k inject-secure-defaults -a claude` (both flags exist in our CLI).
Why they care: Semgrep's own thesis is "MCP makes the scanner available, hooks make it run" ([MCP vs hooks](https://semgrep.dev/blog/2026/mcp-vs-hooks-ai-agent-security), [Cursor hooks](https://semgrep.dev/blog/2025/cursor-hooks-mcp-server), [Guardian](https://docs.semgrep.dev/semgrep-guardian/overview)). We would be running Guardian's design for agents that have no IDE.
Code: `guild-agent/*/tools` write paths call `POST /guard` and throw on `handoff_rejected`; add `.claude/settings.json`.
Demo: remediator's patch tries to add `shell=True`; the tool call fails with the Semgrep finding, agent regenerates.
Effort: S (25 min).

## 2. AI-agent anti-pattern pack (tested, 3/3)

What: `rules/aegis-agent.yml` with three rules:
- `aegis.tool-arg-to-shell` (taint): a parameter of a LangChain `@tool` or MCP `@mcp.tool()` function is the source; `subprocess(..., shell=True)`, `os.system`, `open`, `execute` are sinks; `shlex.quote` sanitizes. Uses `pattern-inside` on the decorator, so it is framework-aware.
- `aegis.taint-secret-into-prompt` (taint): `os.environ`/`getenv` reaching `messages=` of OpenAI/Anthropic calls.
- `aegis.webhook-missing-signature-check`: FastAPI `@app.post` with a path matching `webhook|hook|event` (`metavariable-regex`) that reads `await req.json()` outside an `hmac.compare_digest` guard (`pattern-not-inside`).

```yaml
pattern-sources:
  - patterns:
      - pattern-inside: |
          @$MCP.tool()
          def $F(..., $ARG, ...):
              ...
      - pattern: $ARG
```
Why: the hosted MCP server and Guardian protect code agents write; nobody ships rules for code *that is* an agent. Taint mode is what Semgrep says separates it from grep ([taint docs](https://docs.semgrep.dev/writing-rules/data-flow/taint-mode/overview)).
Code: copy `agent.yml` to `rules/`, `agent.py` to `rules/tests/aegis-agent.py`; add an MCP tool file to `demo-target`. Our own `/webhook/github` stays clean (it reads `body()` and calls `_verify`).
Demo: push an MCP server with `def read_file(path): open(path)`; Issue carries the dataflow trace "tool argument reaches open()".
Effort: S (15 min).

## 3. Per-rule precision from ClickHouse, auto-tuned severity

What: `SELECT rule_id, countIf(status='resolved') r, countIf(status='dismissed') d, r/(r+d) p FROM aegis.findings GROUP BY rule_id HAVING r+d >= 5`. In `scanner.scan()`, a rule with p < 0.4 drops to INFO (no failing status, still in the Issue digest); show the table in `/insights` and the dashboard.
Why: noise is the product problem Semgrep Assistant attacks with autotriage and Memories ([Assistant](https://docs.semgrep.dev/semgrep-assistant/overview)). Measuring rule precision per fleet is what their rule authors wish customers reported back.
Effort: S (20 min).

## 4. Fix once, prevent everywhere

What: when `POST /rules/propose` accepts a rule, `warden` scans every repo in `fleet.json` with only that rule (`--config learned.yml`, cached checkouts) and opens Issues; then `semgrep publish --visibility=unlisted rules/learned/x.yml` and put the registry link in each Issue.
Why: [private/unlisted rules](https://docs.semgrep.dev/writing-rules/private-rules) exist for exactly this sharing.
Demo: one fix on repo A, three Issues appear on B, C, D within a minute.
Effort: M (35 min; publish needs `semgrep login`, fleet scan does not).

## 5. Suppression audit (tested)

What: generic rule `aegis.nosemgrep-without-reason`, regex `(?m)(#|//)\s*nosemgrep(:\s*[\w.\-]+)?[ \t]*$`, run with `--disable-nosem` (without it Semgrep suppresses the match on its own line, verified). Plus the base vs `--disable-nosem` diff from SEMGREP-DEEP B4.
Why: a vibe-coded repo, or a prompt-injected agent, silences a scanner with one comment. Our own two `nosemgrep` lines carry `-- reason` and pass.
Effort: S (15 min).

## 6. SARIF to GitHub code scanning

`semgrep scan --sarif`, gzip+base64, `POST /repos/{o}/{r}/code-scanning/sarifs`. Findings show in the Security tab. Needs `security_events: write`; private repos need GHAS. M.

## 7. `semgrep ci` with a booth token

Supply Chain reachability and Secrets `validators` ([validators](https://docs.semgrep.dev/semgrep-secrets/validators)) turn "a key" into "a live key". Highest ceiling, but token and org onboarding eat the 45 minutes. Say it, do it after.

## 8. Hosted MCP as second opinion

Triage calls `semgrep_scan_with_custom_rule` on `https://mcp.semgrep.ai/mcp` to draft a narrower rule. Guild sandbox outbound access is unknown. Mention only.

## Top 2 to do now

1. **Agent anti-pattern pack (#2).** Tested, 15 minutes, gives a new demo finding nobody else has.
2. **Hooks on agent tool calls (#1).** Turns the handoff guard into Semgrep's own Guardian argument, deterministic and outside the prompt.

## Talking points

- Our agents can't write code that Semgrep hasn't scanned: the check sits in the tool call, the prompt doesn't know about it.
- We write rules for code that is itself an agent: MCP tool arguments, prompts carrying secrets, unsigned webhooks.
- Every rule has a measured precision from real dismissals, and noisy rules demote themselves.
- A fix on one repo becomes a tested, published rule that scans the whole fleet the same minute.
