# AEGIS bundled Semgrep rules

`aegis.yml` (Python), `aegis-js.yml` (JavaScript + TypeScript) and `aegis-taint.yml`
(Python, Semgrep `mode: taint`: a finding is a proven source-to-sink flow, not a line). Every rule has
`metadata.cwe` and `metadata.fix_hint`; rules marked "yes" also carry a `fix` /
`fix-regex`, which Semgrep renders into `extra.fix` in `--json` output.

| Rule id | CWE | Severity | Autofix |
|---|---|---|---|
| `aegis.sql-string-concat` | CWE-89 | ERROR | no (placeholder style depends on driver; see `fix_hint`) |
| `aegis.hardcoded-secret` | CWE-798 | ERROR | yes: `import os; NAME = os.environ["NAME"]` (plain names only) |
| `aegis.flask-debug-true` | CWE-489 | WARNING | yes: `debug=True` -> `debug=False` |
| `aegis.subprocess-shell-true` | CWE-78 | ERROR | no (string command; see `fix_hint`) |
| `aegis.subprocess-shell-true-list` | CWE-78 | WARNING | yes: `shell=True` -> `shell=False` (argument list) |
| `aegis.yaml-unsafe-load` | CWE-502 | ERROR | yes: `yaml.safe_load($X)` |
| `aegis.eval-user-input` | CWE-95 | ERROR | yes: `ast.literal_eval($X)` (needs `import ast`) |
| `aegis.js-child-process-exec-injection` | CWE-78 | ERROR | no (use `execFile` + arg array) |
| `aegis.js-hardcoded-secret` | CWE-798 | WARNING | no (use `process.env`) |
| `aegis.js-eval-injection` | CWE-95 | ERROR | no |
| `aegis.js-new-function` | CWE-95 | ERROR | no |
| `aegis.js-reflected-xss` | CWE-79 | ERROR | no (taint: `req.query/params/body` -> `res.send/write`) |
| `aegis.js-jwt-none-algorithm` | CWE-347 | WARNING | no |
| `aegis.js-sql-string-concat` | CWE-89 | ERROR | no (knex `raw`/`*Raw`, `.query` with SQL text) |
| `aegis.js-nosql-where-injection` | CWE-943 | ERROR | no (Mongo `$where`) |
| `aegis.taint-request-to-sql` | CWE-89 | ERROR | no (taint: `request.args/form/get_json` -> `.execute/.executescript`; `int()` sanitizes) |
| `aegis.taint-llm-output-to-exec` | CWE-94 | ERROR | no (taint: OpenAI/Anthropic/Gemini/LangChain/ollama response -> `eval/exec/os.system/subprocess/.execute/requests/open`; `json.loads`/`shlex.quote` sanitize) |
| `aegis.tool-arg-to-shell` | CWE-78 | ERROR | no (`aegis-ai-agents.yml`, taint: parameter of a LangChain `@tool` / MCP `@mcp.tool()` function -> `subprocess(shell=True)`/`os.system`/`open`/`.execute`; `shlex.quote` sanitizes) |
| `aegis.taint-secret-into-prompt` | CWE-200 | WARNING | no (`aegis-ai-agents.yml`, taint: `os.environ`/`os.getenv` -> `messages=` of OpenAI/Anthropic calls) |
| `aegis.webhook-missing-signature-check` | CWE-345 | ERROR | no (`aegis-ai-agents.yml`: FastAPI `@app.post` on a webhook/hook/event path reads `await req.json()` outside an `hmac.compare_digest` guard) |

## Tests

Fixtures live in `tests/` (`aegis.py`, `aegis.fixed.py`, `aegis-js.js`, `aegis-taint.py`, `aegis-ai-agents.py`) with
`# ruleid:` / `# ok:` annotations. Run from the repo root:

```sh
semgrep --metrics=off --test --config rules/ rules/tests/
```

(`semgrep --test rules/` alone finds no tests because the fixtures are in a subdirectory.)
`--config rules/` only loads `.yml`/`.yaml`, so the fixtures are never read as rules.
Note that with a directory config Semgrep prefixes rule ids with the path, e.g.
`rules.aegis.hardcoded-secret`.

## Validate + traces

```sh
semgrep scan --metrics=off --validate --config rules/aegis-taint.yml
semgrep scan --metrics=off --dataflow-traces --config rules/aegis-taint.yml path/to/file.py
```

The scanner runs the second command for taint findings and attaches the
"Taint comes from / flows through / reaches the sink" block as `dataflow_trace`.
`POST /rules/propose` runs `--validate` and `--test` on a proposed rule + fixture
before the rulesmith is allowed to open a PR; `.github/workflows/semgrep-rules.yml`
runs `--test` on every PR touching `rules/`.
