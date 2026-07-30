from __future__ import annotations

import logging
import os

import httpx
from flask import Flask, abort, jsonify, render_template_string, request, send_from_directory
from psycopg2 import pool

from checklists import CHECKLISTS

logger = logging.getLogger("vizaoson")


def notify_telegram(name: str, phone: str, country: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_NOTIFY_CHAT_ID")
    if not token or not chat_id:
        return
    text = f"📩 Новая заявка VizaOson\nИмя: {name}\nТелефон: {phone}\nНаправление: {country}"
    try:
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except Exception:
        logger.exception("Failed to send Telegram notification")

app = Flask(__name__, static_folder=".", static_url_path="")

CHECKLIST_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ data.title }} — чек-лист документов | VizaOson</title>
<style>
  :root { --blue: #2554c7; --blue-dark: #1a3d94; --ink: #14213d; --muted: #5b6478; --bg: #f7f9fc; --card: #ffffff; --border: #e4e8f0; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; background: var(--bg); color: var(--ink); line-height: 1.55; }
  .container { max-width: 720px; margin: 0 auto; padding: 48px 24px; }
  a.back { color: var(--blue); text-decoration: none; font-size: 14px; font-weight: 600; }
  h1 { font-size: 28px; margin: 16px 0 6px; letter-spacing: -0.02em; }
  .flag { font-size: 40px; }
  ul { list-style: none; margin-top: 24px; }
  li { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 16px 18px; margin-bottom: 10px; font-size: 15px; display: flex; gap: 10px; }
  li::before { content: "☐"; color: var(--blue); font-size: 18px; flex-shrink: 0; }
  .note { margin-top: 24px; background: #eaf0ff; border: 1px dashed var(--blue); border-radius: 12px; padding: 16px; font-size: 14px; color: var(--blue-dark); }
  .cta { display: inline-block; margin-top: 28px; background: var(--blue); color: #fff; padding: 13px 24px; border-radius: 10px; font-weight: 600; text-decoration: none; }
  .cta:hover { background: var(--blue-dark); }
</style>
</head>
<body>
<div class="container">
  <a href="/" class="back">← На главную</a>
  <div class="flag">{{ data.flag }}</div>
  <h1>{{ data.title }}</h1>
  <p style="color:var(--muted)">Примерный список документов — актуальный список для вашей ситуации уточним на консультации.</p>
  <ul>
    {% for item in data.documents %}
    <li>{{ item }}</li>
    {% endfor %}
  </ul>
  <div class="note">⚠️ {{ data.note }}</div>
  <a href="/#contact" class="cta">Записаться на консультацию</a>
</div>
</body>
</html>
"""

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


@app.route("/checklist/<country>")
def checklist(country: str):
    data = CHECKLISTS.get(country)
    if data is None:
        abort(404)
    return render_template_string(CHECKLIST_TEMPLATE, data=data)


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

    notify_telegram(name, phone, country)

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
