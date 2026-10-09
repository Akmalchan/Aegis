# AEGIS fleet and backfill repos

Created by `fleet/make-targets.sh` (`OWNER=vincivv` by default). Agent ownership lives in `fleet.json`.
`clickhouse/backfill.py` loads the history of every repo listed below into ClickHouse.
Commit counts are taken on 2026-10-09 and given as all commits / first-parent commits.

## Monitored fleet (10 repos)

| Repo | Agent | Kind | Source / license | Commits | Notes |
|---|---|---|---|---|---|
| [vincivv/aegis-target-01](https://github.com/vincivv/aegis-target-01) | aegis-sentinel-01 | Flask users service | built from `demo-target/app.py` | 5 | Clean on `main`. **Demo repo:** `demo/vuln` (from `_variants/app_vulnerable.py`, 3 findings), `demo/clean` (from `_variants/app_fixed.py`), tag `demo/base` |
| [vincivv/aegis-target-02](https://github.com/vincivv/aegis-target-02) | aegis-sentinel-01 | Flask notes service (SQLite) | written by make-targets.sh | 4 | clean |
| [vincivv/aegis-target-03](https://github.com/vincivv/aegis-target-03) | aegis-sentinel-01 | Flask inventory service | written by make-targets.sh | 4 | clean |
| [vincivv/aegis-target-04](https://github.com/vincivv/aegis-target-04) | aegis-sentinel-02 | Express notes API | built from `demo-target/js/app.js` | 4 | Clean on `main`. **Demo repo:** `demo/vuln` (from `js/_variants/app_vulnerable.js`, 4 findings: exec injection, hard-coded token, reflected XSS, eval), `demo/clean`, tag `demo/base` |
| [vincivv/aegis-target-05](https://github.com/vincivv/aegis-target-05) | aegis-sentinel-02 | Express link shortener | written by make-targets.sh | 4 | clean |
| [vincivv/aegis-target-06](https://github.com/vincivv/aegis-target-06) | aegis-sentinel-02 | Express todo API | written by make-targets.sh | 4 | clean |
| [vincivv/aegis-target-07](https://github.com/vincivv/aegis-target-07) | aegis-sentinel-03 | Flask quickstart (mirror) | [Azure-Samples/msdocs-python-flask-webapp-quickstart](https://github.com/Azure-Samples/msdocs-python-flask-webapp-quickstart) (MIT) | 33 / 24 | 24 `aegis.js-hardcoded-secret` hits in vendored `static/bootstrap/*.js` (`DATA_API_KEY = '.data-api'`), which are false positives |
| [vincivv/aegis-target-08](https://github.com/vincivv/aegis-target-08) | aegis-sentinel-03 | Flask + PostgreSQL sample (mirror) | [Azure-Samples/msdocs-flask-postgresql-sample-app](https://github.com/Azure-Samples/msdocs-flask-postgresql-sample-app) (MIT) | 130 / 55 | clean at HEAD |
| [vincivv/aegis-target-09](https://github.com/vincivv/aegis-target-09) | aegis-sentinel-03 | Express + MongoDB sample (mirror) | [Azure-Samples/msdocs-nodejs-mongodb-azure-sample-app](https://github.com/Azure-Samples/msdocs-nodejs-mongodb-azure-sample-app) (MIT) | 24 / 23 | clean at HEAD |
| vincivv/snipbox (private, pre-existing) | aegis-sentinel-03 | FastAPI snippet manager | the team's own repo, contents not modified | 1 | clean at HEAD |

Mirrors 07–09 were cloned with their full history and pushed as `main`. One commit was added on top that removes upstream `.github/workflows`, since those need Azure secrets we don't have. It also appends `Mirrored from <url> (<license>) for AEGIS demo monitoring.` to the README.

## Backfill-only OSS repos (history scanned, not monitored)

These were chosen because our bundled rules (`rules/`) produce real hits at HEAD: subprocess `shell=True`, SQL string formatting, hard-coded secrets, eval, and JS `exec`/`eval`.

| Repo | License | Commits (all / first-parent) | Findings at HEAD (bundled rules) |
|---|---|---|---|
| [sqlmapproject/sqlmap](https://github.com/sqlmapproject/sqlmap) | GPL-2.0 (with exceptions) | 11 099 / 10 491 | 23 |
| [buildbot/buildbot](https://github.com/buildbot/buildbot) | GPL-2.0 | 28 944 / 8 905 | 21 |
| [OWASP/NodeGoat](https://github.com/OWASP/NodeGoat) | Apache-2.0 | 461 / 160 | 15 |

The backfill only scans these repos locally and stores findings metadata in ClickHouse. Nothing is redistributed, so their licenses are not a concern.
Histories longer than `--max-commits` (default 400) are sampled evenly along the first-parent history. See the docstring in `clickhouse/backfill.py`.
