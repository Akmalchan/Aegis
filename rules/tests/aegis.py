# Fixtures for rules/aegis.yml (semgrep --test). Not real code.
import ast
import os
import subprocess

import yaml


# ---------------------------------------------------------------- aegis.sql-string-concat
def sql(cur, q, uid):
    # ruleid: aegis.sql-string-concat
    cur.execute("SELECT * FROM users WHERE name LIKE '%" + q + "%'")
    # ruleid: aegis.sql-string-concat
    cur.execute("SELECT * FROM users WHERE id = " + uid)
    # ruleid: aegis.sql-string-concat
    cur.execute(f"SELECT * FROM users WHERE id = {uid}")
    # ruleid: aegis.sql-string-concat
    cur.execute("SELECT * FROM users WHERE id = {}".format(uid))
    # ruleid: aegis.sql-string-concat
    cur.execute("SELECT * FROM users WHERE id = %s" % uid)
    # ok: aegis.sql-string-concat
    cur.execute("SELECT * FROM users WHERE id = ?", (uid,))
    # ok: aegis.sql-string-concat
    cur.execute("SELECT * FROM users WHERE name LIKE ?", ("%" + q + "%",))


# ---------------------------------------------------------------- aegis.hardcoded-secret
# ruleid: aegis.hardcoded-secret
ADMIN_API_KEY = "sk-live-9f3c2a7e1b4d8c6f0a5e7d2b9c1f4a8e"
# ruleid: aegis.hardcoded-secret
db_password = "hunter2hunter2"


class Client:
    def __init__(self):
        # ruleid: aegis.hardcoded-secret
        self.auth_token = "abc123abc123"


# ok: aegis.hardcoded-secret
EMPTY_TOKEN = ""
# ok: aegis.hardcoded-secret
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
# ok: aegis.hardcoded-secret
DB_HOST = "localhost"


# ---------------------------------------------------------------- aegis.flask-debug-true
def run(app):
    # ruleid: aegis.flask-debug-true
    app.run(host="0.0.0.0", port=5000, debug=True)
    # ruleid: aegis.flask-debug-true
    app.run(debug=True)
    # ok: aegis.flask-debug-true
    app.run(host="127.0.0.1", port=5000)
    # ok: aegis.flask-debug-true
    app.run(debug=False)


# ---------------------------------------------------------------- aegis.subprocess-shell-true(-list)
def shell(host):
    # ruleid: aegis.subprocess-shell-true
    subprocess.run("ping -c 1 " + host, shell=True)
    # ruleid: aegis.subprocess-shell-true
    subprocess.check_output(f"ping -c 1 {host}", shell=True)
    # ok: aegis.subprocess-shell-true
    subprocess.run("ls -la", shell=True)
    # ok: aegis.subprocess-shell-true
    subprocess.run(["ping", "-c", "1", host])
    # ruleid: aegis.subprocess-shell-true-list
    subprocess.run(["ping", "-c", "1", host], shell=True)
    # ok: aegis.subprocess-shell-true-list
    subprocess.run(["ping", "-c", "1", host], shell=False)


# ---------------------------------------------------------------- aegis.yaml-unsafe-load
def load(data):
    # ruleid: aegis.yaml-unsafe-load
    a = yaml.load(data)
    # ruleid: aegis.yaml-unsafe-load
    b = yaml.load(data, Loader=yaml.Loader)
    # ruleid: aegis.yaml-unsafe-load
    c = yaml.unsafe_load(data)
    # ok: aegis.yaml-unsafe-load
    d = yaml.safe_load(data)
    # ok: aegis.yaml-unsafe-load
    e = yaml.load(data, Loader=yaml.SafeLoader)
    return a, b, c, d, e


# ---------------------------------------------------------------- aegis.eval-user-input
def calc(expr):
    # ruleid: aegis.eval-user-input
    v = eval(expr)
    # ok: aegis.eval-user-input
    w = eval("1 + 2")
    # ok: aegis.eval-user-input
    x = ast.literal_eval(expr)
    return v, w, x
