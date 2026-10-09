# AEGIS bundled Semgrep rules

`aegis.yml` (Python) and `aegis-js.yml` (JavaScript + TypeScript). Every rule has
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

## Tests

Fixtures live in `tests/` (`aegis.py`, `aegis.fixed.py`, `aegis-js.js`) with
`# ruleid:` / `# ok:` annotations. Run from the repo root:

```sh
semgrep --metrics=off --test --config rules/ rules/tests/
```

(`semgrep --test rules/` alone finds no tests because the fixtures are in a subdirectory.)
`--config rules/` only loads `.yml`/`.yaml`, so the fixtures are never read as rules.
Note that with a directory config Semgrep prefixes rule ids with the path, e.g.
`rules.aegis.hardcoded-secret`.
