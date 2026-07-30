from __future__ import annotations

import os

from flask import Flask, jsonify, request, send_from_directory
from psycopg2 import pool

app = Flask(__name__, static_folder=".", static_url_path="")

SCHEMA = """
CREATE TABLE IF NOT EXISTS visa_leads (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    phone TEXT NOT NULL,
    country TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

_pool: pool.ThreadedConnectionPool | None = None


def get_pool() -> pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = pool.ThreadedConnectionPool(1, 5, dsn=os.environ["DATABASE_URL"])
        conn = _pool.getconn()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(SCHEMA)
        finally:
            _pool.putconn(conn)
    return _pool


@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/api/lead", methods=["POST"])
def create_lead():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    phone = (data.get("phone") or "").strip()
    country = (data.get("country") or "").strip()

    if not name or not phone or not country:
        return jsonify({"error": "name, phone и country обязательны"}), 400

    conn = get_pool().getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO visa_leads (name, phone, country) VALUES (%s, %s, %s) RETURNING id",
                (name, phone, country),
            )
            lead_id = cur.fetchone()[0]
    finally:
        get_pool().putconn(conn)

    return jsonify({"ok": True, "id": lead_id}), 201


@app.route("/api/leads", methods=["GET"])
def list_leads():
    token = request.args.get("token")
    if token != os.environ.get("ADMIN_TOKEN"):
        return jsonify({"error": "unauthorized"}), 401

    conn = get_pool().getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT id, name, phone, country, created_at FROM visa_leads ORDER BY id DESC")
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        get_pool().putconn(conn)

    return jsonify(rows)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
