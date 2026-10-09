# AEGIS remediation playbook

Use this after a finding is confirmed (see the security-review skill) and you must decide whether to ship a patch, and how to write it.

## 1. PR or Issue only

Open a PR when all of these hold:

- The fix is local: one file, one hunk, under 15 changed lines.
- The behaviour for valid input stays identical. Parameterising a query, moving a literal to `os.environ`, turning `shell=True` into a list: these keep behaviour. Adding input validation that rejects values the app used to accept does not.
- You read the whole function and nothing else depends on the exact string being built (no logging of the raw SQL, no second use of the shell string).
- The finding is CWE-89, CWE-78, CWE-798, CWE-489, CWE-95 with a trivial replacement, or the Semgrep rule shipped a `fix`.

Issue only (no PR) when:

- CWE-502 or CWE-918: the right fix is a design decision (which loader, which allowlist of hosts). Describe two options in the Issue.
- CWE-79 where the template engine is unknown or autoescape is off globally.
- The fix needs a new dependency, a migration, a config file, or touches more than one file.
- Confidence under 0.8.
- The file is generated, vendored, or minified.

If in doubt: Issue only. A wrong patch costs the team more than a missing one.

## 2. Minimal diff rule

The PR changes exactly the lines that make the finding disappear, plus imports those lines require. Nothing else. Concretely:

- Do not rename variables, reorder imports, fix typos in comments, or change quotes.
- Do not run a formatter. Match the file's existing indentation (tabs or spaces, count them) and quote style.
- Keep the same line count where possible so the diff reads as a substitution.
- Do not add comments like `# fixed by AEGIS`. The commit message and PR body carry that.
- One finding, one commit, one PR. Two findings in one file: two PRs, unless they are on adjacent lines.

## 3. Applying a Semgrep `fix` string

When the finding carries `fix`, Semgrep already computed the replacement for the matched range `start_line`..`end_line`:

1. `github_repos_get_content` with `ref=<head sha>`; decode base64; split into lines.
2. Replace lines `start_line`..`end_line` (1-based, inclusive) with the `fix` text. Keep the leading whitespace of the original first line and prepend it to every line of `fix`.
3. Confirm the `lines` field of the finding matches what you removed (strip whitespace before comparing). If it does not match, stop: the file changed since the scan. Issue only.
4. Check the fix introduces no new name. `fix` may reference a module (`shlex`, `os`) that is not imported; add the import at the top of the import block.
5. Commit with `github_repos_create_or_update_file_contents` on the branch `aegis/fix-<fingerprint>`, passing the file's `sha` from step 1.

## 4. Patterns

### Parameterised SQL (CWE-89)

sqlite3, placeholder `?`:

```python
# before
cur.execute("SELECT * FROM users WHERE name = '" + name + "'")
cur.execute(f"SELECT * FROM items WHERE id = {item_id}")
# after
cur.execute("SELECT * FROM users WHERE name = ?", (name,))
cur.execute("SELECT * FROM items WHERE id = ?", (item_id,))
```

psycopg2 / psycopg3, placeholder `%s` (keep `%s` even for integers):

```python
cur.execute("SELECT * FROM users WHERE email = %s", (email,))
# LIKE: build the pattern in Python, pass it as a parameter
cur.execute("SELECT * FROM posts WHERE title LIKE %s", (f"%{q}%",))
```

SQLAlchemy Core / `text()`:

```python
from sqlalchemy import text
conn.execute(text("SELECT * FROM users WHERE name = :name"), {"name": name})
# ORM: never .filter(f"...") ; use column comparisons
session.query(User).filter(User.name == name)
```

Identifiers (table or column names) cannot be parameters. Map them through a dict of allowed values: `col = {"name": "name", "email": "email"}[sort_key]`.

### Secret to environment variable (CWE-798)

```python
# before
API_KEY = "sk-live-4f8a..."
# after
import os
API_KEY = os.environ.get("AEGIS_API_KEY", "")   # .get keeps imports and test collection working when unset
```

Default to `os.environ.get(NAME, "")`: `os.environ[NAME]` raises KeyError at import time and breaks the repo's own test suite in verify (snipbox pre-run). Use `[...]` only when the app already reads other required env vars that way. Pick the variable name from the constant name. In the PR body say the secret must be rotated: the old value is in git history forever.

### `shell=True` to list args (CWE-78)

```python
# before
subprocess.run("ping -c 1 " + host, shell=True)
os.system(f"tar czf {out} {src}")
# after
subprocess.run(["ping", "-c", "1", host])
subprocess.run(["tar", "czf", out, src])
```

If the original relied on a shell feature (pipes, globbing, `&&`), do not patch; Issue only, explain which feature blocks the list form.

### Debug mode (CWE-489)

```python
app.run(debug=True)  ->  app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
DEBUG = True         ->  DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"
```

### eval (CWE-95)

`eval(expr)` on numeric or literal input: `ast.literal_eval(expr)`. Anything else: Issue only.

## 5. PR body template

Title: `AEGIS: fix <rule_id short name> in <path>` e.g. `AEGIS: fix sql-string-concat in app.py`.

Branch: `aegis/fix-<fingerprint>` from the head sha of the push. Base: the repo default branch (or the PR head branch when triggered by a pull_request event).

```
Fixes #<issue number>.

**Finding:** <rule_id>, <CWE>, `<path>:<start_line>`
**Change:** <one sentence: what was replaced with what>
**Behaviour:** unchanged for valid input. <one sentence on anything a reviewer should test by hand>
**Not done:** <rotation of the leaked key / allowlist for hosts / nothing>

Opened by aegis-sentinel-NN after push <short sha>. Session: <session_url>
<!-- AEGIS-FP: <fingerprint> -->
```

Under 120 words. The footer marker lets the verifier link PR, Issue and finding without parsing titles.

After opening the PR: call `aegis_scanner_record_action(kind="pr_opened", ref=<pr number>, fingerprint=...)`, then comment on the Issue: `Patch in #<pr>.` Request changes on the original PR if the push came from one; otherwise leave the red commit status as the signal.

## 6. Never

- Never refactor. If the function is ugly, that is not your problem today.
- Never change formatting, imports order, blank lines, trailing whitespace.
- Never touch tests, fixtures, CI config, lockfiles, or documentation in a fix PR.
- Never push to the default branch directly. Always a branch plus PR.
- Never force-push or delete a branch.
- Never patch a file whose current content differs from what the scanner saw (step 3 of section 3).
- Never open a second PR for a fingerprint that already has an open PR; find it via the `AEGIS-FP` marker and comment on the Issue instead.
- Never commit a secret, even the one you are removing, into the PR body or commit message. Write `<redacted>`.
