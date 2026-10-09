# AEGIS security review

Use this when you hold one Semgrep finding and must decide: real vulnerability or noise, how bad, and what to write in the GitHub Issue. The finding gives you `rule_id`, `path`, `start_line`..`end_line`, `lines`, `message`, `severity`, `cwe`, `fingerprint`, maybe `fix`, `seen_before`, `dismissed_before`, `repo_mttr_h`.

## 1. Decide true positive or false positive

Read the file around the finding with `github_repos_get_content` (ref = the head sha). Then answer three questions in order. A finding is a true positive only when all three hold.

1. **Source.** Does attacker-controlled data reach the flagged line? Attacker-controlled means: HTTP request parameters, headers, cookies, JSON bodies, path segments, form fields, websocket messages, file uploads, CLI args of a network service, rows previously written from such input, environment of a multi-tenant runner. Values that are constants, config files the operator writes, or hardcoded test fixtures are not attacker-controlled.
2. **Sink.** Does that data hit a dangerous operation without a neutralising step? SQL string → `execute`, string → `subprocess`/`os.system`, string → `eval`/`exec`, bytes → `pickle.loads`/`yaml.load`, string → HTML response, URL → outbound `requests.get`. A neutralising step is parameterisation, an allowlist, escaping done by the framework (Jinja autoescape on), or a type cast to int/UUID before use.
3. **Consequence.** Name what the attacker gets: read other users' rows, run commands on the host, steal a token, execute JS in a victim's browser, reach internal services.

Then check reachability and scope:

- Is the function wired to a route, handler, task, CLI entry, or event? Dead code or a helper nobody calls is still a finding but severity drops one level and the Issue says so.
- Path under `tests/`, `test_*.py`, `*_test.*`, `fixtures/`, `examples/`, `docs/`: default false positive unless the rule is CWE-798 (a real credential in test code is still a leak).
- `dismissed_before: true` means a human or triage already called this fingerprint noise. Do not reopen it unless the code changed around it. Say nothing, record `kind=dismissed`.
- `seen_before > 0` with no open Issue means an earlier scan found it and it was closed as fixed. Treat as regression: true positive, severity unchanged, mention "regressed" in the title.
- A finding where the "input" is a hardcoded literal (`cursor.execute("SELECT 1 WHERE x=" + "5")`) is a false positive. Say so in one sentence in the session output, do not open an Issue.

Confidence: 0.9+ when source, sink and route are visible in the file. 0.6 to 0.8 when the source is in another file you did not read. Below 0.6: do not open an Issue, write one sentence why.

## 2. Severity rubric by CWE family

Severity goes into the Issue header and the commit status description. Start from the CWE's base level, then adjust: unreachable code = minus one, authentication required before the sink = minus one, public unauthenticated route = stays or plus one. Never go above critical or below low.

| CWE | What it is | Base | Typical consequence to state |
|---|---|---|---|
| CWE-89 | SQL injection: user string inside query text | critical | read/alter any table, auth bypass with `' OR 1=1 --` |
| CWE-78 | OS command injection: user string in `shell=True`, `os.system`, backticks | critical | arbitrary command execution on the host as the service user |
| CWE-502 | Unsafe deserialisation: `pickle.loads`, `yaml.load` without SafeLoader, `marshal` on untrusted bytes | critical | code execution on load |
| CWE-95 | `eval`/`exec` on a string containing user input | critical | code execution in the process |
| CWE-798 | Hardcoded credential, API key, private key, DB password in source | high | anyone with repo read has the secret; rotate immediately |
| CWE-918 | SSRF: user-supplied URL fetched server side | high | reach cloud metadata (169.254.169.254), internal admin ports |
| CWE-79 | XSS: user string rendered into HTML without escaping (`Markup`, `|safe`, `innerHTML`, `render_template_string` with f-string) | high | session theft, actions as the victim |
| CWE-489 | Debug mode in production (`app.run(debug=True)`, `DEBUG = True` in settings) | medium | Werkzeug debugger = RCE if the PIN is weak; stack traces leak paths and secrets |

Semgrep `severity` (ERROR/WARNING/INFO) is the rule author's guess. Your rubric wins; mention both in the Issue.

## 3. Issue template

Title: `[AEGIS] <severity>: <CWE short name> in <path>:<line>` e.g. `[AEGIS] critical: SQL injection in app.py:28`. Add ` (regressed)` when `seen_before > 0`.

Labels: `aegis`, `security`, plus one of `severity:critical`, `severity:high`, `severity:medium`, `severity:low`.

Body, exactly these sections, in this order:

```
## Summary
**Severity:** critical · **Confidence:** 0.95 · **Rule:** rules.aegis.sql-string-concat · **CWE:** CWE-89
**Where:** `app.py:28` @ `abc1234` · **Agent:** aegis-sentinel-01

## What is wrong
<2-4 sentences: the source, the sink, why the neutralising step is missing. Quote the line.>

## Impact
<1-3 sentences: what an attacker does with it, with a one-line example payload.>

## Fix
<the corrected code block, minimal diff. If the finding came with `fix`, use it verbatim. Say whether a PR was opened.>

## Evidence
Semgrep: <message>
```
<the matched `lines`>
```

<!-- AEGIS-FP: <fingerprint> -->
```

The last line of the body must be `<!-- AEGIS-FP: <fingerprint> -->` with nothing after it. The sentinel reads this marker on the next scan to decide whether the Issue is still open. Without it the Issue can never be auto-closed.

Before creating, call `github_issues_list_for_repo` with `labels=aegis, state=open` and search bodies for `AEGIS-FP: <fingerprint>`. If present, do not create a second Issue; comment on the existing one only if the location changed.

## 4. Tone rules

- Concrete. Name the variable, the function, the line, the payload. "User input reaches `cur.execute` through `q` from `request.args`" beats "unsanitised input may be used".
- No hedging. No "may", "could potentially", "it is recommended to consider". If you are not sure it is exploitable, lower the confidence number and say what you could not verify in one sentence.
- Under 250 words for the whole body. The person reading it is the developer who wrote the line; they do not need a CWE lecture.
- No praise, no apology, no "as an AI".
- One finding per Issue. Two findings on the same line with different rules: one Issue, mention both rules, fingerprint of the higher-severity one.
- The commit status description (140 chars max) is the title without the `[AEGIS]` prefix plus the Issue number: `critical: SQL injection in app.py:28 → #12`.
