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
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB

ALLOWED_DOC_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/heic",
}

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
<title>Личный кабинет — VizaOson</title><style>{{ style }}
  .doc-row { display: flex; justify-content: space-between; align-items: center; background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; margin-bottom: 8px; font-size: 14px; }
  .doc-row a { color: var(--blue); text-decoration: none; font-weight: 600; }
  .doc-del { color: #c0392b; font-size: 13px; text-decoration: none; margin-left: 12px; }
  .upload-form { margin-top: 14px; display: flex; gap: 10px; align-items: center; }
</style></head><body>
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

  <h1 style="margin-top:36px; font-size:20px;">Мои документы</h1>
  {% if documents %}
    {% for doc in documents %}
    <div class="doc-row">
      <a href="/documents/{{ doc.id }}" target="_blank">{{ doc.filename }}</a>
      <span style="color:var(--muted)">
        {{ (doc.size / 1024) | round(1) }} КБ
        <a href="#" class="doc-del" onclick="deleteDoc({{ doc.id }}); return false;">удалить</a>
      </span>
    </div>
    {% endfor %}
  {% else %}
  <div class="empty">Документов пока нет.</div>
  {% endif %}

  <form class="upload-form" id="upload-form">
    <input type="file" id="doc-file" accept=".pdf,.jpg,.jpeg,.png,.heic" required>
    <button class="btn" type="submit">Загрузить</button>
  </form>
  <div id="upload-message" style="font-size:13px; margin-top:8px;"></div>
</div>
<script>
  document.getElementById('upload-form').addEventListener('submit', async function (e) {
    e.preventDefault();
    var fileInput = document.getElementById('doc-file');
    var msg = document.getElementById('upload-message');
    if (!fileInput.files.length) return;
    var formData = new FormData();
    formData.append('file', fileInput.files[0]);
    msg.textContent = 'Загружаем...';
    msg.style.color = 'var(--muted)';
    try {
      var res = await fetch('/api/documents', { method: 'POST', body: formData });
      var body = await res.json();
      if (!res.ok) throw new Error(body.error || 'upload failed');
      msg.style.color = '#1a9e5c';
      msg.textContent = 'Загружено!';
      setTimeout(function () { location.reload(); }, 700);
    } catch (err) {
      msg.style.color = '#c0392b';
      msg.textContent = 'Ошибка загрузки: ' + err.message;
    }
  });

  async function deleteDoc(id) {
    if (!confirm('Удалить документ?')) return;
    await fetch('/api/documents/' + id, { method: 'DELETE' });
    location.reload();
  }
</script>
</body></html>
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
ALTER TABLE visa_leads ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'new';
ALTER TABLE visa_leads ADD COLUMN IF NOT EXISTS notes TEXT;
CREATE TABLE IF NOT EXISTS documents (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    data BYTEA NOT NULL,
    size INTEGER NOT NULL,
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


SITE_URL = "https://vizaoson-site-production.up.railway.app"


@app.route("/robots.txt")
def robots_txt():
    body = f"User-agent: *\nAllow: /\nDisallow: /dashboard\nDisallow: /admin\nDisallow: /api/\nSitemap: {SITE_URL}/sitemap.xml\n"
    return app.response_class(body, mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap_xml():
    urls = ["/", "/login", "/register"] + [f"/checklist/{code}" for code in CHECKLISTS]
    body_urls = "\n".join(f"  <url><loc>{SITE_URL}{path}</loc></url>" for path in urls)
    body = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{body_urls}\n</urlset>'
    return app.response_class(body, mimetype="application/xml")


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


LEAD_STATUSES = {
    "new": "🆕 Новая",
    "contacted": "📞 Связались",
    "in_progress": "⏳ В работе",
    "done": "✅ Готово",
    "rejected": "❌ Отказ",
}


@app.route("/api/leads", methods=["GET"])
def list_leads():
    token = request.args.get("token")
    if token != os.environ.get("ADMIN_TOKEN"):
        return jsonify({"error": "unauthorized"}), 401

    conn = get_pool().getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT id, name, phone, country, status, notes, created_at FROM visa_leads ORDER BY id DESC")
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        get_pool().putconn(conn)

    return jsonify(rows)


@app.route("/api/leads/<int:lead_id>/status", methods=["POST"])
def update_lead_status(lead_id: int):
    token = request.args.get("token") or (request.get_json(silent=True) or {}).get("token")
    if token != os.environ.get("ADMIN_TOKEN"):
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    status = data.get("status")
    if status not in LEAD_STATUSES:
        return jsonify({"error": "invalid status"}), 400

    conn = get_pool().getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("UPDATE visa_leads SET status = %s WHERE id = %s", (status, lead_id))
            updated = cur.rowcount
    finally:
        get_pool().putconn(conn)

    if not updated:
        return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True})


ADMIN_TEMPLATE = """
<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Админ-панель — VizaOson</title><style>{{ style }}
  .stats { display: flex; gap: 16px; margin-bottom: 24px; }
  .stat { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 16px 20px; flex: 1; }
  .stat .num { font-size: 28px; font-weight: 800; color: var(--blue-dark); }
  .stat .label { font-size: 13px; color: var(--muted); }
  select.status-select { padding: 6px 8px; border-radius: 8px; border: 1.5px solid var(--border); font-size: 13px; font-family: inherit; background: var(--bg); }
</style></head><body>
<div class="container" style="max-width:900px">
  <h1>Админ-панель VizaOson</h1>
  <div class="stats">
    <div class="stat"><div class="num">{{ leads|length }}</div><div class="label">Заявок всего</div></div>
    <div class="stat"><div class="num">{{ users|length }}</div><div class="label">Зарегистрировано</div></div>
  </div>
  <h2 style="font-size:18px; margin-bottom:10px;">Заявки</h2>
  {% if leads %}
  <table>
    <tr><th>#</th><th>Имя</th><th>Телефон</th><th>Направление</th><th>Аккаунт</th><th>Дата</th><th>Статус</th></tr>
    {% for lead in leads %}
    <tr>
      <td>{{ lead.id }}</td><td>{{ lead.name }}</td><td>{{ lead.phone }}</td><td>{{ lead.country }}</td>
      <td>{{ lead.email or "—" }}</td><td>{{ lead.created_at }}</td>
      <td>
        <select class="status-select" onchange="updateStatus({{ lead.id }}, this.value)">
          {% for code, label in statuses.items() %}
          <option value="{{ code }}" {% if lead.status == code %}selected{% endif %}>{{ label }}</option>
          {% endfor %}
        </select>
      </td>
    </tr>
    {% endfor %}
  </table>
  {% else %}<div class="empty">Заявок пока нет.</div>{% endif %}

  <h2 style="font-size:18px; margin:28px 0 10px;">Пользователи</h2>
  {% if users %}
  <table>
    <tr><th>#</th><th>Имя</th><th>Email</th><th>Регистрация</th></tr>
    {% for u in users %}
    <tr><td>{{ u.id }}</td><td>{{ u.name }}</td><td>{{ u.email }}</td><td>{{ u.created_at }}</td></tr>
    {% endfor %}
  </table>
  {% else %}<div class="empty">Пользователей пока нет.</div>{% endif %}
</div>
<script>
  var TOKEN = {{ token|tojson }};
  async function updateStatus(id, status) {
    await fetch('/api/leads/' + id + '/status?token=' + encodeURIComponent(TOKEN), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: status }),
    });
  }
</script>
</body></html>
"""


@app.route("/admin")
def admin_panel():
    token = request.args.get("token")
    if token != os.environ.get("ADMIN_TOKEN"):
        abort(401)

    conn = get_pool().getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT l.id, l.name, l.phone, l.country, l.status, l.created_at, u.email
                FROM visa_leads l LEFT JOIN users u ON u.id = l.user_id
                ORDER BY l.id DESC
                """
            )
            cols = [d[0] for d in cur.description]
            leads = [dict(zip(cols, row)) for row in cur.fetchall()]

            cur.execute("SELECT id, name, email, created_at FROM users ORDER BY id DESC")
            cols = [d[0] for d in cur.description]
            users = [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        get_pool().putconn(conn)

    return render_template_string(
        ADMIN_TEMPLATE, style=AUTH_BASE_STYLE, leads=leads, users=users, statuses=LEAD_STATUSES, token=token
    )


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

            cur.execute(
                "SELECT id, filename, size FROM documents WHERE user_id = %s ORDER BY id DESC",
                (session["user_id"],),
            )
            cols = [d[0] for d in cur.description]
            documents = [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        get_pool().putconn(conn)

    return render_template_string(
        DASHBOARD_TEMPLATE, style=AUTH_BASE_STYLE, user_name=session["user_name"], leads=leads, documents=documents
    )


@app.route("/api/documents", methods=["POST"])
def upload_document():
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401

    file = request.files.get("file")
    if file is None or not file.filename:
        return jsonify({"error": "Файл не передан"}), 400

    content_type = file.content_type or "application/octet-stream"
    if content_type not in ALLOWED_DOC_TYPES:
        return jsonify({"error": "Разрешены только PDF, JPG, PNG, HEIC"}), 400

    data = file.read()
    if not data:
        return jsonify({"error": "Пустой файл"}), 400

    conn = get_pool().getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO documents (user_id, filename, content_type, data, size) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (session["user_id"], file.filename, content_type, data, len(data)),
            )
            doc_id = cur.fetchone()[0]
    finally:
        get_pool().putconn(conn)

    return jsonify({"ok": True, "id": doc_id}), 201


@app.route("/documents/<int:doc_id>")
def download_document(doc_id: int):
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_pool().getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT filename, content_type, data FROM documents WHERE id = %s AND user_id = %s",
                (doc_id, session["user_id"]),
            )
            row = cur.fetchone()
    finally:
        get_pool().putconn(conn)

    if row is None:
        abort(404)

    filename, content_type, data = row
    return app.response_class(
        bytes(data), mimetype=content_type, headers={"Content-Disposition": f'inline; filename="{filename}"'}
    )


@app.route("/api/documents/<int:doc_id>", methods=["DELETE"])
def delete_document(doc_id: int):
    if "user_id" not in session:
        return jsonify({"error": "unauthorized"}), 401

    conn = get_pool().getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("DELETE FROM documents WHERE id = %s AND user_id = %s", (doc_id, session["user_id"]))
            deleted = cur.rowcount
    finally:
        get_pool().putconn(conn)

    if not deleted:
        return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
