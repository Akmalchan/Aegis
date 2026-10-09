# Semgrep finding to submit

## The finding: our AI fixer "fixed" every vulnerability by breaking the parser, and got a green check

During the live run on 2026-10-09 (20:39 to 20:53) our Guild remediator agent (an LLM) was told to commit only the span patch from `/fix`. Instead it re-typed the whole `app.py` into a git tree call. Six commits from cascading sessions landed on one branch, `aegis/fix-6a71b18bfc74`. The head, [40c68ac](https://github.com/andriidrok1/aegis-demo-target/commit/40c68ac59891353f89fc14dd3ae77fd70e7a9f8d), has:

- line 1: `"""...Intentionally minimal.""#`, an unterminated docstring, so the whole file is a string literal
- `row = cur.fetchone()` deleted (NameError if it ever ran)
- the SQL injection was patched correctly in the same commit, so the diff looks plausible

Semgrep on that file: **0 findings, exit 0**, and one entry in `errors[]`: `PartialParsing ... Syntax error at line app.py:1`. Our verifier (`aegis/scanner.py` + `aegis/verify.py`, also AI-written, by Claude Code subagents) reads `results` and never `errors`. So "finding gone, 2 at base, 0 at head" became `aegis:verified` and a [✅ Fix verified comment on PR #56](https://github.com/andriidrok1/aegis-demo-target/pull/56#issuecomment-6089077018). `python -m py_compile` on the same file: `SyntaxError: unterminated triple-quoted string literal`.

PR #64 repeats it: the SQL line became `LIKE '%", + q + "%'`, labelled verified, Semgrep `PartialParsing`.

Why Semgrep product people should care: every AI pipeline that gates on "Semgrep finding count went down" has this hole, and an LLM that mangles a file is the cheapest way to hit it. Semgrep did report the problem; two layers of AI-written code (patch and gate) threw it away. The fix is one line: a non-empty `errors[]` on a touched file fails verification. Guardian could also say "N files not parsed, 0 findings is not clean".

## Status: gate fixed (2026-10-09, 15:20)

`aegis/scanner.py` now returns Semgrep `errors[]` (`run_semgrep_with_errors`); `verify.py` L1, `guard.py` and `fix.py` fail on a parse error in a touched file, and `.py` files must also compile. Re-run of `/verify` against the live scanner:

```
# PR #56: base 61e5f86 (its base branch), head 40c68ac
{"verified":false,"layers":[{"name":"static","passed":false,"details":"Semgrep could not parse app.py: PartialParsing: Syntax error at line app.py:1 — 0 findings is not clean (1 at base, 0 at head)"}, regression: skipped, targeted_test: skipped]}
# known-good pair: base 0ad1fc1, head 9893db2
{"verified":true,"layers":[{"name":"static","passed":true,"details":"no target finding given; checked for regressions only; no new findings vs 0ad1fc1 (4 at base, 0 at head)"}, ...]}
```

Write-up of the gap and the fix: `HOW-WE-KNOW.md` section 3d.

## Reproduce (put in the submission)

```
gh api "repos/andriidrok1/aegis-demo-target/contents/app.py?ref=40c68ac" --jq .content | base64 -d > pr56.py
semgrep scan --metrics=off --config p/python --config rules/ pr56.py --json | jq '{results:(.results|length), errors:[.errors[].type]}'
# {"results":0,"errors":[["PartialParsing", ...]]}
python3 -m py_compile pr56.py   # SyntaxError
# base 02f24a5, same command: 7 results (hardcoded-secret, sql-string-concat, taint-request-to-sql, flask-debug-true, avoid_app_run_with_bad_host, ...)
```

Screenshots: PR #56 "Files changed" with line 1 `""#`; the ✅ Fix verified comment; the jq output next to the base scan; the `py_compile` SyntaxError.

## Backups

1. **PR #12, flask-debug-true fix left the dev server on 0.0.0.0.** The fixer flipped `debug=True` to `debug=False` ([502212d](https://github.com/andriidrok1/aegis-demo-target/commit/502212d85118b6850eb362552f5c044121d5429e)). Our static layer flagged `avoid_app_run_with_bad_host` as "new" and refused the PR. Caveat: that finding was already at base 02f24a5; our diff matches by line text, so an edited line reads as new. `HOW-WE-KNOW.md` used to say "introduces"; corrected 2026-10-09. Keep it out of the submission.
2. **Self-audit of AI-written AEGIS code** (52d74a7): `dangerous-subprocess-use-tainted-env-args` in `aegis/supply_chain.py`, 15 → 4 findings. Real but generic. The token-in-clone-URL leak was found by hand, not by Semgrep, so it does not qualify.

The Semgrep Claude Code plugin is not installed here; all of the above is Semgrep CLI 1.180. Optional, under 5 minutes: `claude plugin install semgrep@claude-plugins-official`, open `pr56.py`, screenshot its output.

## 60-second pitch

"We built agents that fix vulnerabilities. One of them got creative. Told to commit a three-line patch, it re-typed the whole file and broke line 1: an unterminated docstring. Every vulnerability disappeared, because the file was now one big string. Semgrep exited 0 with zero findings, and it also said, in the errors array, 'I could not parse this file'. Our verifier, which an AI also wrote, only read the results array. So the pipeline stamped the broken file 'verified' and commented a green check. Here is PR 56 and here is the scan. Zero findings is not clean unless errors is empty too, and every agent pipeline gating on finding count has this hole."
