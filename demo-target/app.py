"""Tiny Flask demo service used as AEGIS's monitored target. Intentionally minimal."""
import sqlite3
from flask import Flask, request, jsonify

app = Flask(__name__)
DB = "users.db"


def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


@app.get("/users/<int:user_id>")
def get_user(user_id):
    cur = db().cursor()
    cur.execute("SELECT id, name, email FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    return jsonify(dict(row)) if row else (jsonify({"error": "not found"}), 404)


@app.get("/health")
def health():
    return {"ok": True}


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
