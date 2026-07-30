from __future__ import annotations

import logging
import os

import httpx
from flask import Flask, abort, jsonify, redirect, render_template_string, request, send_from_directory, session, url_for
from psycopg2 import pool
from werkzeug.security import check_password_hash, generate_password_hash

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
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-insecure-key")

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

AUTH_BASE_STYLE = """
  :root { --blue: #2554c7; --blue-dark: #1a3d94; --ink: #14213d; --muted: #5b6478; --bg: #f7f9fc; --card: #ffffff; --border: #e4e8f0; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; background: var(--bg); color: var(--ink); line-height: 1.55; }
  .container { max-width: 420px; margin: 0 auto; padding: 64px 24px; }
  a.back { color: var(--blue); text-decoration: none; font-size: 14px; font-weight: 600; }
  h1 { font-size: 26px; margin: 16px 0 24px; letter-spacing: -0.02em; }
  form { display: flex; flex-direction: column; gap: 14px; background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 24px; }
  input { padding: 13px 16px; border-radius: 10px; border: 1.5px solid var(--border); font-size: 15px; font-family: inherit; background: var(--bg); }
  input:focus { outline: none; border-color: var(--blue); }
  .btn { background: var(--blue); color: #fff; padding: 13px 18px; border-radius: 10px; font-weight: 600; font-size: 15px; border: none; cursor: pointer; }
  .btn:hover { background: var(--blue-dark); }
  .error { background: #fdecea; border: 1px solid #f5c6c2; color: #c0392b; padding: 12px 14px; border-radius: 10px; font-size: 14px; }
  .switch { margin-top: 16px; font-size: 14px; color: var(--muted); text-align: center; }
  .switch a { color: var(--blue); font-weight: 600; text-decoration: none; }
  table { width: 100%; border-collapse: collapse; background: var(--card); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; margin-top: 20px; }
  th, td { text-align: left; padding: 12px 14px; font-size: 14px; border-bottom: 1px solid var(--border); }
  th { background: #f0f3fa; color: var(--muted); font-weight: 600; }
  .empty { color: var(--muted); font-size: 14px; margin-top: 20px; }
  .logout { float: right; font-size: 13px; color: var(--muted); }
"""

REGISTER_TEMPLATE = """
<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Регистрация — VizaOson</title><style>{{ style }}</style></head><body>
<div class="container">
  <a href="/" class="back">← На главную</a>
  <h1>Создать аккаунт</h1>
  {% if error %}<div class="error" style="margin-bottom:14px">{{ error }}</div>{% endif %}
  <form method="post">
    <input type="text" name="name" placeholder="Ваше имя" required>
    <input type="email" name="email" placeholder="Email" required>
    <input type="password" name="password" placeholder="Пароль (минимум 6 символов)" minlength="6" required>
    <button class="btn" type="submit">Зарегистрироваться</button>
  </form>
  <div class="switch">Уже есть аккаунт? <a href="/login">Войти</a></div>
</div></body></html>
"""

LOGIN_TEMPLATE = """
<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Вход — VizaOson</title><style>{{ style }}</style></head><body>
<div class="container">
  <a href="/" class="back">← На главную</a>
  <h1>Вход в кабинет</h1>
  {% if error %}<div class="error" style="margin-bottom:14px">{{ error }}</div>{% endif %}
  <form method="post">
    <input type="email" name="email" placeholder="Email" required>
    <input type="password" name="password" placeholder="Пароль" required>
    <button class="btn" type="submit">Войти</button>
  </form>
  <div class="switch">Нет аккаунта? <a href="/register">Зарегистрироваться</a></div>
</div></body></html>
"""

DASHBOARD_TEMPLATE = """
<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Личный кабинет — VizaOson</title><style>{{ style }}</style></head><body>
<div class="container" style="max-width:640px">
  <a href="/logout" class="logout">Выйти</a>
  <h1>Здравствуйте, {{ user_name }}</h1>
  <p style="color:var(--muted)">Ваши заявки:</p>
  {% if leads %}
  <table>
    <tr><th>#</th><th>Направление</th><th>Дата</th></tr>
    {% for lead in leads %}
    <tr><td>{{ lead.id }}</td><td>{{ lead.country }}</td><td>{{ lead.created_at }}</td></tr>
    {% endfor %}
  </table>
  {% else %}
  <div class="empty">Пока нет заявок — оставьте новую на <a href="/#contact">главной странице</a>, она автоматически привяжется к вашему аккаунту.</div>
  {% endif %}
</div></body></html>
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS visa_leads (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    phone TEXT NOT NULL,
    country TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE visa_leads ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id);
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

    user_id = session.get("user_id")

    conn = get_pool().getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO visa_leads (name, phone, country, user_id) VALUES (%s, %s, %s, %s) RETURNING id",
                (name, phone, country, user_id),
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


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template_string(REGISTER_TEMPLATE, style=AUTH_BASE_STYLE, error=None)

    name = (request.form.get("name") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""

    if not name or not email or len(password) < 6:
        return render_template_string(
            REGISTER_TEMPLATE, style=AUTH_BASE_STYLE, error="Проверьте, что все поля заполнены и пароль от 6 символов."
        )

    conn = get_pool().getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE email = %s", (email,))
            if cur.fetchone():
                return render_template_string(
                    REGISTER_TEMPLATE, style=AUTH_BASE_STYLE, error="Этот email уже зарегистрирован."
                )
            cur.execute(
                "INSERT INTO users (email, password_hash, name) VALUES (%s, %s, %s) RETURNING id",
                (email, generate_password_hash(password, method="pbkdf2:sha256"), name),
            )
            user_id = cur.fetchone()[0]
    finally:
        get_pool().putconn(conn)

    session["user_id"] = user_id
    session["user_name"] = name
    return redirect(url_for("dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template_string(LOGIN_TEMPLATE, style=AUTH_BASE_STYLE, error=None)

    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""

    conn = get_pool().getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT id, name, password_hash FROM users WHERE email = %s", (email,))
            row = cur.fetchone()
    finally:
        get_pool().putconn(conn)

    if row is None or not check_password_hash(row[2], password):
        return render_template_string(LOGIN_TEMPLATE, style=AUTH_BASE_STYLE, error="Неверный email или пароль.")

    session["user_id"] = row[0]
    session["user_name"] = row[1]
    return redirect(url_for("dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_pool().getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, country, created_at FROM visa_leads WHERE user_id = %s ORDER BY id DESC",
                (session["user_id"],),
            )
            cols = [d[0] for d in cur.description]
            leads = [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        get_pool().putconn(conn)

    return render_template_string(DASHBOARD_TEMPLATE, style=AUTH_BASE_STYLE, user_name=session["user_name"], leads=leads)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
