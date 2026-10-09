# Stream B — Scanner + Semgrep (owner: person 2)

Paste this whole file as the first message of a Claude Code session opened in this repo.

## Context for Claude

Read `PLAN.md`, `openapi.yaml` (Contract 1 — frozen), `aegis/ch.py` (Contract 2 — stub, stream C fills it), `aegis/server.py`, `aegis/scanner.py`, `rules/aegis.yml`. Existing v1 code (webhook → Semgrep → OpenAI/Guild analyst) works end-to-end; keep `/webhook/github` as a fallback path but the new contract endpoints are the product.

Semgrep facts: binary at `~/.local/bin/semgrep` (1.180); `semgrep scan --json --quiet --metrics=off --config rules/ --config p/security-audit --config p/secrets <files>`; `--baseline-commit <sha>` reports only findings absent at that commit (requires a clean working tree); rules may carry `fix:` and the JSON result exposes it under `extra.fix`; `--autofix --dryrun` prints patches; `semgrep --test rules/` runs fixtures with `# ruleid:` / `# ok:` annotations; `semgrep ci --supply-chain --json` needs `SEMGREP_APP_TOKEN`. Registry packs may be unreachable → `run_semgrep` already falls back to bundled rules.

Guild facts: our scanner becomes a Guild custom integration: `guild integration create aegis-scanner --base-url https://<tunnel> --auth-scheme api-key --description "..."`, `guild integration operation create <owner>~aegis-scanner --openapi ./openapi.yaml`, `guild integration version build <owner>~aegis-scanner --version-number 1.0.0`, `... version publish ...`, `guild integration connect <owner>~aegis-scanner --owner <account> --token <SCANNER_KEY>`, `guild integration version test <owner>~aegis-scanner --operation scan_diff --input-body '{...}'`. The base URL is **frozen after publish** → the tunnel must live all day. Guild blocks private/loopback URLs → use the public cloudflared URL. Spec must be self-contained OpenAPI 3.0/3.1 (no external `$ref`).

## Spawn 3 subagents IN PARALLEL (Agent tool, one message), then integrate

### B1 — API (owns `aegis/server.py`, `aegis/scanner.py`, `fleet/export_openapi.py`, `fleet/integration.sh`)
- Implement Contract 1 exactly as in `openapi.yaml`: `POST /scan/diff`, `POST /scan/full`, `POST /actions`, `GET /insights`. Auth dependency: header `X-AEGIS-Key` must equal `SCANNER_KEY` from `.env` (add to `.env.example`).
- `/scan/diff`: `scanner.checkout(repo, head_sha)`, then Semgrep over all tracked code files with `--baseline-commit base_sha`; if `base_sha` is empty/zeros or not an ancestor → fall back to full scan. `verdict = unsafe` if any finding with severity ERROR or WARNING. Call `ch.enrich(repo, findings)`, `ch.insert_scan(...)`, `ch.insert_findings(...)`, and `state.log_event` for the local feed. Return `ScanResult`.
- `/scan/full`: same without baseline.
- `/actions`: validate kind enum, `ch.insert_action`, `state.log_event`.
- `/insights`: `ch.insights(hours)`.
- `scanner.run_semgrep`: add `fix` (from `extra.fix`) to each finding; accept `baseline_commit: str | None`; include the `--autofix --dryrun`-equivalent by just returning `fix`.
- `fleet/export_openapi.py`: NOT needed if `openapi.yaml` is hand-written (it is). Instead write `tests/check_openapi.py` that validates FastAPI's `/openapi.json` operationIds and paths match `openapi.yaml` (operationIds `scan_diff`, `scan_full`, `record_action`, `fleet_insights`). Set `operation_id=` on each route.
- `fleet/integration.sh`: the full `guild integration ...` sequence above with `$TUNNEL_URL`, `$OWNER`, `$SCANNER_KEY` variables, idempotent where possible, plus `sed` to put the tunnel URL into `servers[0].url` of a temp copy of `openapi.yaml`.
- Done when: `curl -s -X POST localhost:8787/scan/diff -H "X-AEGIS-Key: $KEY" -H 'content-type: application/json' -d '{"repo":"andriidrok1/aegis-demo-target","base_sha":"<clean>","head_sha":"<vuln>","agent":"test"}'` returns 3 findings, two with `fix`; swapping shas returns 0; missing key → 401.

### B2 — rules (owns `rules/`)
- `rules/aegis.yml`: add `fix:` to deterministic rules: `aegis.flask-debug-true` → `debug=False`; `aegis.yaml-unsafe-load` → `yaml.safe_load($X)`; `aegis.sql-string-concat` → split into variants where a parameterized fix is expressible (e.g. `$CUR.execute("..." + $X + "...")` has no safe mechanical fix: leave without `fix`, but add a `metadata.fix_hint`); `aegis.subprocess-shell-true` → `shell=False` only when args is a list; `aegis.eval-user-input` → `ast.literal_eval($X)`.
- `rules/aegis-js.yml` (javascript, typescript): `child_process.exec` / `execSync` with template/concat → fix hint `execFile`; hardcoded secret assignment (same regex idea as python); `eval($X)`; `res.send(... + req.query...)`/`res.write` reflected XSS; `new Function($X)`; `app.listen` with `DEBUG`-style flags is not a thing — skip; `jwt.verify(..., {algorithms: ["none"]})`; `mongoose`/`knex.raw` string concat.
- `rules/tests/`: one fixture per rule with `# ruleid: <id>` and `# ok: <id>` lines (python) / `// ruleid:` (js) so `semgrep --test rules/` is green. Also a `rules/README.md` table: rule → CWE → has fix.
- Done when: `~/.local/bin/semgrep --test rules/` passes; `semgrep scan --config rules/ --autofix --dryrun demo-target/_variants/app_vulnerable.py` prints at least one patch.

### B3 — glue (owns `aegis/enrich.py`, `run.sh`, `tests/test_scanner.sh`, `aegis/supply_chain.py`)
- `aegis/enrich.py`: thin wrapper that calls `ch.enrich` and applies the verdict rule (`dismissed_before` findings are kept in the list but flagged; verdict ignores them). Unit-testable without ClickHouse.
- `run.sh`: start uvicorn on 8787 + cloudflared quick tunnel, write URL to `state/tunnel_url.txt`, keep both alive (restart cloudflared on exit), print the URL big. If `CLOUDFLARE_TUNNEL_TOKEN` is set use a named tunnel instead (stable URL).
- `tests/test_scanner.sh`: end-to-end: creates a temp git repo with clean then vulnerable commit (copy from `demo-target/_variants/`), pushes nothing (uses local path — add support in `scanner.checkout` for `file://` or local path repos when `repo` starts with `local:`), calls `/scan/diff` both directions, asserts counts, asserts `/actions` and `/insights` respond.
- `aegis/supply_chain.py`: if `SEMGREP_APP_TOKEN` set, run `semgrep ci --supply-chain --json` in the checkout and convert results to the `Finding` shape (rule_id = advisory id, cwe from metadata, path = lockfile); hooked into `/scan/full` only.
- Done when: `tests/test_scanner.sh` is green locally.

## After subagents finish (you, the session owner)
1. `git pull --rebase`; run `./run.sh`; put the tunnel URL + `SCANNER_KEY` into `fleet/integration.sh` and run it (needs Andrii's Guild login on this machine, or hand the script to him).
2. `guild integration version test` must return findings through Guild.
3. Commit after every working step: `git add -A && git commit -m "B: ..." && git pull --rebase && git push`.
