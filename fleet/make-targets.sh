#!/usr/bin/env bash
# Create the AEGIS demo fleet: GitHub repos $OWNER/aegis-target-01..09 + fleet.json.
#
#   fleet/make-targets.sh            # create missing repos only (idempotent; existing repos are left untouched)
#   fleet/make-targets.sh --refresh  # rebuild every target and force-push main + demo branches
#   OWNER=someone fleet/make-targets.sh
#   DRY=1 fleet/make-targets.sh      # build everything locally under .cache/targets, push nothing
#
# 01-03  Flask services (derived from demo-target/app.py), clean on main, a few commits of history each
# 04-06  Express services (derived from demo-target/js/app.js), clean on main
# 07-09  mirrors of small permissively-licensed public sample apps (full history)
# target-01 and target-04 also get branches demo/vuln (main + vulnerable variant) and demo/clean (demo/vuln + fix)
# and a tag demo/base (= clean main) so the demo can be reset. See docs/demo-checklist.md "Push commands".
set -euo pipefail

OWNER=${OWNER:-vincivv}
EXTRA_REPOS=${EXTRA_REPOS:-vincivv/snipbox}   # existing repos added to the fleet as-is (space separated)
REFRESH=0
[[ "${1:-}" == "--refresh" ]] && REFRESH=1

ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK="$ROOT/.cache/targets"
DEMO="$ROOT/demo-target"
mkdir -p "$WORK"

# Mirror sources for 07-09: "<github repo> <license>"
MIRROR_07="Azure-Samples/msdocs-python-flask-webapp-quickstart MIT"
MIRROR_08="Azure-Samples/msdocs-flask-postgresql-sample-app MIT"
MIRROR_09="Azure-Samples/msdocs-nodejs-mongodb-azure-sample-app MIT"

NOW=$(date +%s)
STEP_DAYS=0
log() { printf '\033[1;34m[make-targets]\033[0m %s\n' "$*"; }

repo_exists() { gh repo view "$OWNER/$1" >/dev/null 2>&1; }

# commit everything with a back-dated timestamp: commit "<msg>" <days ago>
commit() {
  local msg=$1 days=$2
  local ts="$((NOW - days * 86400 + RANDOM % 3600)) +0000"
  git add -A
  GIT_AUTHOR_DATE="$ts" GIT_COMMITTER_DATE="$ts" git commit --quiet -m "$msg"
}

fresh_dir() {
  local d="$WORK/$1"
  rm -rf "$d"; mkdir -p "$d"; cd "$d"
  git init --quiet -b main
}

publish() {
  # publish <name> <description>   (run inside the repo dir; pushes main)
  local name=$1 desc=$2
  if [[ -n "${DRY:-}" ]]; then log "DRY: would publish $name"; return; fi
  if repo_exists "$name"; then
    git remote remove origin 2>/dev/null || true
    git remote add origin "https://github.com/$OWNER/$name.git"
    git push --quiet --force origin main
    gh repo edit "$OWNER/$name" --description "$desc" >/dev/null
  else
    gh repo create "$OWNER/$name" --public --source=. --remote=origin --push --description "$desc" >/dev/null
  fi
  log "pushed https://github.com/$OWNER/$name"
}

push_demo_branches() {
  # push_demo_branches <file> <vuln source> <vuln msg> <fixed source> <fix msg>
  local file=$1 vsrc=$2 vmsg=$3 fsrc=$4 fmsg=$5
  git tag -f demo/base main >/dev/null
  git checkout --quiet -B demo/vuln main
  cp "$vsrc" "$file"; commit "$vmsg" 0
  git checkout --quiet -B demo/clean demo/vuln
  cp "$fsrc" "$file"; commit "$fmsg" 0
  git checkout --quiet main
  if [[ -n "${DRY:-}" ]]; then log "DRY: would push demo branches"; return; fi
  git push --quiet --force origin demo/vuln demo/clean
  git push --quiet --force origin refs/tags/demo/base
  log "  demo/vuln, demo/clean, tag demo/base pushed"
}

readme() { # readme <title> <one-liner> <runcmd> <routes...>
  local title=$1 desc=$2 run=$3; shift 3
  {
    echo "# $title"
    echo
    echo "$desc"
    echo
    echo "Monitored by [AEGIS](https://github.com/$OWNER): every push to this repo is scanned autonomously by an AEGIS sentinel agent."
    echo
    echo "## Run"
    echo
    echo '```bash'
    echo "$run"
    echo '```'
    echo
    echo "## Routes"
    echo
    for r in "$@"; do echo "- \`$r\`"; done
  } > README.md
}

# ----------------------------------------------------------------------------------------------- Flask targets
flask_gitignore() { printf '__pycache__/\n*.pyc\n.venv/\n*.db\n.env\n' > .gitignore; }

make_target_01() { # users service = demo-target/app.py verbatim, + demo branches
  fresh_dir aegis-target-01
  flask_gitignore; echo flask > requirements.txt
  cat > app.py <<'EOF'
"""Tiny Flask demo service used as AEGIS's monitored target. Intentionally minimal."""
from flask import Flask

app = Flask(__name__)


@app.get("/health")
def health():
    return {"ok": True}


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
EOF
  commit "init: flask skeleton with /health" 21
  cat > schema.sql <<'EOF'
CREATE TABLE IF NOT EXISTS users (
  id    INTEGER PRIMARY KEY AUTOINCREMENT,
  name  TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE
);
EOF
  cat > seed.py <<'EOF'
"""Create users.db with a few demo rows."""
import sqlite3

conn = sqlite3.connect("users.db")
conn.executescript(open("schema.sql").read())
conn.executemany("INSERT OR IGNORE INTO users (name, email) VALUES (?, ?)",
                 [("ada", "ada@example.com"), ("linus", "linus@example.com"), ("grace", "grace@example.com")])
conn.commit()
print("seeded", conn.execute("SELECT count(*) FROM users").fetchone()[0], "users")
EOF
  commit "db: users schema and seed script" 17
  cp "$DEMO/app.py" app.py
  commit "feat: GET /users/<id>" 12
  readme "aegis-target-01 · users service" "Small Flask service that serves user profiles from SQLite." \
    "pip install -r requirements.txt && python seed.py && python app.py" "GET /health" "GET /users/<id>"
  commit "docs: README" 8
  mkdir -p tests
  cat > tests/test_app.py <<'EOF'
from app import app


def test_health():
    assert app.test_client().get("/health").get_json() == {"ok": True}
EOF
  commit "test: health check" 4
  publish aegis-target-01 "AEGIS demo target 01: Flask users service"
  push_demo_branches app.py "$DEMO/_variants/app_vulnerable.py" "feat: user search endpoint, admin key, debug mode" \
    "$DEMO/_variants/app_fixed.py" "fix: drop unsafe search, remove hardcoded key, disable debug"
}

make_target_02() { # notes service
  fresh_dir aegis-target-02
  flask_gitignore; echo flask > requirements.txt
  cat > app.py <<'EOF'
"""Notes service monitored by AEGIS."""
from flask import Flask, jsonify

app = Flask(__name__)
NOTES = {1: {"id": 1, "title": "welcome", "body": "hello"}}


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/notes")
def list_notes():
    return jsonify(list(NOTES.values()))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001)
EOF
  commit "init: notes service with in-memory store" 26
  cat > app.py <<'EOF'
"""Notes service monitored by AEGIS."""
import sqlite3
from flask import Flask, request, jsonify

app = Flask(__name__)
DB = "notes.db"


def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, title TEXT, body TEXT)")
    return conn


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/notes")
def list_notes():
    rows = db().execute("SELECT id, title, body FROM notes ORDER BY id").fetchall()
    return jsonify([dict(r) for r in rows])


@app.get("/notes/<int:note_id>")
def get_note(note_id):
    row = db().execute("SELECT id, title, body FROM notes WHERE id = ?", (note_id,)).fetchone()
    return jsonify(dict(row)) if row else (jsonify({"error": "not found"}), 404)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001)
EOF
  commit "feat: persist notes in sqlite, GET /notes/<id>" 19
  python3 - <<'EOF'
import re
src = open("app.py").read()
src = src.replace('''

if __name__ == "__main__":''', '''

@app.post("/notes")
def create_note():
    data = request.get_json(force=True) or {}
    title, body = str(data.get("title", ""))[:200], str(data.get("body", ""))[:5000]
    if not title:
        return jsonify({"error": "title required"}), 400
    conn = db()
    cur = conn.execute("INSERT INTO notes (title, body) VALUES (?, ?)", (title, body))
    conn.commit()
    return jsonify({"id": cur.lastrowid, "title": title, "body": body}), 201


if __name__ == "__main__":''')
open("app.py", "w").write(src)
EOF
  commit "feat: POST /notes with input validation" 11
  readme "aegis-target-02 · notes service" "Flask + SQLite notes API." "pip install -r requirements.txt && python app.py" \
    "GET /health" "GET /notes" "GET /notes/<id>" "POST /notes {title, body}"
  commit "docs: README" 6
  publish aegis-target-02 "AEGIS demo target 02: Flask notes service"
}

make_target_03() { # inventory service
  fresh_dir aegis-target-03
  flask_gitignore; printf 'flask\n' > requirements.txt
  cat > app.py <<'EOF'
"""Inventory service monitored by AEGIS."""
import os
from flask import Flask, jsonify, abort

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", os.urandom(16).hex())

ITEMS = {
    "sku-100": {"sku": "sku-100", "name": "widget", "qty": 42},
    "sku-200": {"sku": "sku-200", "name": "gadget", "qty": 7},
}


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/items")
def items():
    return jsonify(sorted(ITEMS.values(), key=lambda i: i["sku"]))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5002)
EOF
  commit "init: inventory service" 30
  python3 - <<'EOF'
src = open("app.py").read()
src = src.replace('''

if __name__ == "__main__":''', '''

@app.get("/items/<sku>")
def item(sku):
    if sku not in ITEMS:
        abort(404)
    return jsonify(ITEMS[sku])


if __name__ == "__main__":''')
open("app.py", "w").write(src)
EOF
  commit "feat: GET /items/<sku>" 22
  python3 - <<'EOF'
src = open("app.py").read()
src = src.replace("from flask import Flask, jsonify, abort", "from flask import Flask, jsonify, abort, request")
src = src.replace('''

if __name__ == "__main__":''', '''

@app.post("/items/<sku>/reserve")
def reserve(sku):
    qty = request.get_json(force=True).get("qty", 1)
    if not isinstance(qty, int) or qty < 1:
        return jsonify({"error": "qty must be a positive integer"}), 400
    item = ITEMS.get(sku) or abort(404)
    if item["qty"] < qty:
        return jsonify({"error": "insufficient stock"}), 409
    item["qty"] -= qty
    return jsonify(item)


if __name__ == "__main__":''')
open("app.py", "w").write(src)
EOF
  commit "feat: reserve stock endpoint" 15
  readme "aegis-target-03 · inventory service" "Flask inventory API with stock reservations." \
    "pip install -r requirements.txt && python app.py" "GET /health" "GET /items" "GET /items/<sku>" "POST /items/<sku>/reserve {qty}"
  commit "docs: README" 9
  publish aegis-target-03 "AEGIS demo target 03: Flask inventory service"
}

# --------------------------------------------------------------------------------------------- Express targets
node_gitignore() { printf 'node_modules/\n.env\nnpm-debug.log*\n' > .gitignore; }
package_json() { # package_json <name> <desc>
  sed -e "s/\"aegis-demo-target-js\"/\"$1\"/" -e "s/\"description\": \"[^\"]*\"/\"description\": \"$2\"/" "$DEMO/js/package.json" > package.json
}

make_target_04() { # notes API = demo-target/js/app.js verbatim, + demo branches
  fresh_dir aegis-target-04
  node_gitignore; package_json aegis-target-04 "Express notes API monitored by AEGIS"
  cat > app.js <<'EOF'
// Tiny Express demo service used as AEGIS's monitored target. Intentionally minimal.
const express = require("express");

const app = express();
app.use(express.json());

app.get("/health", (req, res) => {
  res.json({ ok: true });
});

const PORT = Number(process.env.PORT || 3000);
app.listen(PORT, "127.0.0.1", () => console.log(`listening on ${PORT}`));
EOF
  commit "init: express skeleton with /health" 20
  python3 - <<'EOF'
src = open("app.js").read()
src = src.replace('''app.get("/health"''', '''const notes = [
  { id: 1, title: "welcome", body: "AEGIS is watching this repo." },
  { id: 2, title: "todo", body: "ship the demo" },
];

app.get("/health"''')
src = src.replace('''const PORT''', '''app.get("/notes", (req, res) => {
  res.json(notes);
});

app.get("/notes/:id", (req, res) => {
  const note = notes.find((n) => n.id === Number(req.params.id));
  if (!note) return res.status(404).json({ error: "not found" });
  res.json(note);
});

const PORT''')
open("app.js", "w").write(src)
EOF
  commit "feat: notes list and get" 14
  cp "$DEMO/js/app.js" app.js
  commit "feat: escaped greeting page and ping endpoint" 9
  readme "aegis-target-04 · notes API (Express)" "Small Express service with notes, a greeting page and a ping check." \
    "npm install && npm start" "GET /health" "GET /notes" "GET /notes/:id" "GET /hello?name=" "GET /ping?host="
  commit "docs: README" 5
  publish aegis-target-04 "AEGIS demo target 04: Express notes API"
  push_demo_branches app.js "$DEMO/js/_variants/app_vulnerable.js" "feat: calc endpoint, token auth on ping, simpler greeting" \
    "$DEMO/js/_variants/app_fixed.js" "fix: execFile with args, token from env, escape output, no eval"
}

make_target_05() { # link shortener
  fresh_dir aegis-target-05
  node_gitignore; package_json aegis-target-05 "Express link shortener monitored by AEGIS"
  cat > app.js <<'EOF'
// Link shortener monitored by AEGIS.
const express = require("express");
const crypto = require("crypto");

const app = express();
app.use(express.json());
const links = new Map();

app.get("/health", (req, res) => res.json({ ok: true }));

app.post("/links", (req, res) => {
  const url = String(req.body.url || "");
  if (!/^https?:\/\//.test(url)) return res.status(400).json({ error: "http(s) url required" });
  const code = crypto.randomBytes(4).toString("hex");
  links.set(code, url);
  res.status(201).json({ code, url });
});

const PORT = Number(process.env.PORT || 3001);
app.listen(PORT, "127.0.0.1", () => console.log(`listening on ${PORT}`));
EOF
  commit "init: create short links" 24
  python3 - <<'EOF'
src = open("app.js").read()
src = src.replace("const PORT", '''app.get("/:code", (req, res) => {
  const url = links.get(req.params.code);
  if (!url) return res.status(404).json({ error: "unknown code" });
  res.redirect(302, url);
});

const PORT''')
open("app.js", "w").write(src)
EOF
  commit "feat: redirect by code" 16
  python3 - <<'EOF'
src = open("app.js").read()
src = src.replace('app.get("/:code"', '''app.get("/links", (req, res) => {
  res.json([...links].map(([code, url]) => ({ code, url })));
});

app.get("/:code"''')
open("app.js", "w").write(src)
EOF
  commit "feat: list links" 10
  readme "aegis-target-05 · link shortener (Express)" "Tiny in-memory link shortener." "npm install && npm start" \
    "GET /health" "POST /links {url}" "GET /links" "GET /:code"
  commit "docs: README" 3
  publish aegis-target-05 "AEGIS demo target 05: Express link shortener"
}

make_target_06() { # todo API
  fresh_dir aegis-target-06
  node_gitignore; package_json aegis-target-06 "Express todo API monitored by AEGIS"
  cat > app.js <<'EOF'
// Todo API monitored by AEGIS.
const express = require("express");

const app = express();
app.use(express.json());
let nextId = 1;
const todos = [];

app.get("/health", (req, res) => res.json({ ok: true }));
app.get("/todos", (req, res) => res.json(todos));

const PORT = Number(process.env.PORT || 3002);
app.listen(PORT, "127.0.0.1", () => console.log(`listening on ${PORT}`));
EOF
  commit "init: todo api" 28
  python3 - <<'EOF'
src = open("app.js").read()
src = src.replace("const PORT", '''app.post("/todos", (req, res) => {
  const text = String(req.body.text || "").slice(0, 280);
  if (!text) return res.status(400).json({ error: "text required" });
  const todo = { id: nextId++, text, done: false };
  todos.push(todo);
  res.status(201).json(todo);
});

const PORT''')
open("app.js", "w").write(src)
EOF
  commit "feat: create todos" 21
  python3 - <<'EOF'
src = open("app.js").read()
src = src.replace("const PORT", '''app.patch("/todos/:id", (req, res) => {
  const todo = todos.find((t) => t.id === Number(req.params.id));
  if (!todo) return res.status(404).json({ error: "not found" });
  if (typeof req.body.done === "boolean") todo.done = req.body.done;
  res.json(todo);
});

app.delete("/todos/:id", (req, res) => {
  const i = todos.findIndex((t) => t.id === Number(req.params.id));
  if (i < 0) return res.status(404).json({ error: "not found" });
  todos.splice(i, 1);
  res.status(204).end();
});

const PORT''')
open("app.js", "w").write(src)
EOF
  commit "feat: complete and delete todos" 13
  readme "aegis-target-06 · todo API (Express)" "In-memory todo list API." "npm install && npm start" \
    "GET /health" "GET /todos" "POST /todos {text}" "PATCH /todos/:id {done}" "DELETE /todos/:id"
  commit "docs: README" 7
  publish aegis-target-06 "AEGIS demo target 06: Express todo API"
}

# ------------------------------------------------------------------------------------------------ OSS mirrors
make_mirror() { # make_mirror <NN> "<src repo> <license>"
  local nn=$1 src lic name
  read -r src lic <<<"$2"
  name="aegis-target-$nn"
  rm -rf "$WORK/$name"
  git clone --quiet "https://github.com/$src.git" "$WORK/$name"
  cd "$WORK/$name"
  local branch; branch=$(git symbolic-ref --short HEAD)
  [[ "$branch" == main ]] || git branch -m "$branch" main
  git remote remove origin
  # upstream CI/deploy workflows need Azure secrets we don't have; drop them so pushes don't spam failures
  git rm -r --quiet --ignore-unmatch .github/workflows
  printf '\n---\nMirrored from https://github.com/%s (%s) for AEGIS demo monitoring.\n' "$src" "$lic" >> README.md
  git add -A
  git commit --quiet -m "docs: note mirror source for AEGIS demo monitoring"
  publish "$name" "AEGIS demo target $nn: mirror of $src ($lic)"
}

# ----------------------------------------------------------------------------------------------------- main
for nn in 01 02 03 04 05 06 07 08 09; do
  name="aegis-target-$nn"
  if [[ -z "${DRY:-}" && $REFRESH == 0 ]] && repo_exists "$name"; then
    log "$OWNER/$name exists, skipping (use --refresh to rebuild)"
    continue
  fi
  log "building $name"
  case $nn in
    07) make_mirror 07 "$MIRROR_07" ;;
    08) make_mirror 08 "$MIRROR_08" ;;
    09) make_mirror 09 "$MIRROR_09" ;;
    *) "make_target_$nn" ;;
  esac
  cd "$ROOT"
done

# fleet.json: 3 repos per sentinel, extra repos go to the last sentinel.
# An existing "agents" map is shared with stream A (deploy.sh, policies.json): kept unless FLEET_REWRITE=1.
python3 - "$ROOT/fleet.json" "$OWNER" "$EXTRA_REPOS" "${FLEET_REWRITE:-0}" <<'EOF'
import json, sys
path, owner, extra, rewrite = sys.argv[1], sys.argv[2], sys.argv[3].split(), sys.argv[4] == "1"
t = lambda n: f"{owner}/aegis-target-{n:02d}"
try:
    fleet = json.load(open(path))          # keep other top-level keys (report_repo, warden, ...)
except (FileNotFoundError, json.JSONDecodeError):
    fleet = {}
if fleet.get("agents") and not rewrite:
    print("fleet.json agents kept (FLEET_REWRITE=1 to regenerate)"); sys.exit(0)
fleet["agents"] = {
    "aegis-sentinel-01": [t(1), t(2), t(3)],
    "aegis-sentinel-02": [t(4), t(5), t(6)],
    "aegis-sentinel-03": [t(7), t(8), t(9)] + extra,
}
open(path, "w").write(json.dumps(fleet, indent=2) + "\n")
print(open(path).read())
EOF
log "done. gh repo list $OWNER --limit 100 | grep aegis-target"
