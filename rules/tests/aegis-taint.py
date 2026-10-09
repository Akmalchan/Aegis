import json
import os
import sqlite3
import subprocess

from flask import request
from openai import OpenAI

client = OpenAI()
db = sqlite3.connect("app.db")


def search():
    q = request.args.get("q", "")
    cur = db.cursor()
    # ruleid: aegis.taint-request-to-sql
    cur.execute("SELECT * FROM notes WHERE title LIKE '%" + q + "%'")
    # ok: aegis.taint-request-to-sql
    cur.execute("SELECT * FROM notes WHERE title LIKE ?", ("%" + q + "%",))
    return cur.fetchall()


def by_id():
    nid = int(request.args["id"])
    cur = db.cursor()
    # ok: aegis.taint-request-to-sql
    cur.execute("SELECT * FROM notes WHERE id = " + str(nid))
    return cur.fetchone()


def run_task(goal):
    resp = client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": goal}])
    cmd = resp.choices[0].message.content
    # ruleid: aegis.taint-llm-output-to-exec
    subprocess.run(cmd, shell=True)
    # ruleid: aegis.taint-llm-output-to-exec
    os.system(cmd)
    plan = json.loads(cmd)
    # ok: aegis.taint-llm-output-to-exec
    subprocess.run(plan["argv"])
