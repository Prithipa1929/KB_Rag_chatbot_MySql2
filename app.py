import hmac
import re
from pathlib import Path

from flask import Flask, g, jsonify, redirect, render_template, request
import pymysql
from pymysql.cursors import DictCursor
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename

import auth
import config
import guardrails
import kb
import rag

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB per request
auth.init_db()
kb.init_kb()

ALLOWED_EXT = (".pdf", ".txt", ".md")


# MySQL Database Connection Helper
def get_db_connection():
    return pymysql.connect(
        host=config.MYSQL_HOST,
        port=config.MYSQL_PORT,
        user=config.MYSQL_USER,
        password=config.MYSQL_PASSWORD,
        database=config.MYSQL_DATABASE,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=True,
    )


# Startup checks
for _f in ["css/style.css", "js/common.js", "js/auth.js", "js/chat.js", "js/admin.js"]:
    _p = Path(app.static_folder or "static") / _f
    if not _p.is_file():
        print(f"WARNING: static file missing -> {_p}")
for _f in ["base", "login", "register", "chat", "admin"]:
    _p = Path(app.template_folder or "templates")
    _p = Path(app.root_path) / _p / f"{_f}.html"
    if not _p.is_file():
        print(f"WARNING: template missing -> {_p}")

USERNAME_RE = re.compile(r"[A-Za-z0-9_.@+-]{3,60}")


@app.context_processor
def inject_company():
    return {"company": config.COMPANY_NAME}


# app.py - line 40 around

# app.py - security_headers function

@app.after_request
def security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    # Inga 'https://cdn.jotfor.ms' matrum jotform domains-ai allow panrom
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self' https://*.jotform.com https://*.jotfor.ms; "
        "script-src 'self' 'unsafe-inline' https://*.jotfor.ms https://*.jotform.com; "
        "style-src 'self' 'unsafe-inline' https://*.jotfor.ms https://*.jotform.com; "
        "frame-src 'self' https://*.jotform.com https://*.jotfor.ms;"
    )
    return resp


@app.errorhandler(413)
def too_large(_):
    return jsonify(error="File too large (max 25 MB)"), 413


# ---------------- pages ----------------
@app.get("/")
def index():
    user = auth.user_from_cookie()
    if not user:
        return redirect("/login")
    return redirect("/admin" if user["role"] == "admin" else "/chat")


@app.get("/login")
def login_page():
    return render_template("login.html")


@app.get("/register")
def register_page():
    return render_template("register.html", admin_enabled=bool(config.ADMIN_SIGNUP_CODE))


@app.get("/chat")
@auth.page_auth()
def chat_page():
    return render_template("chat.html", user=g.user)


@app.get("/admin")
@auth.page_auth("admin")
def admin_page():
    return render_template("admin.html", user=g.user)


# ---------------- auth API ----------------
# app.py

@app.post("/api/auth/register")
def register():
    d = request.get_json(silent=True) or {}
    username = str(d.get("username") or "").strip()
    password = str(d.get("password") or "")
    role = d.get("role", "user")

    if not USERNAME_RE.fullmatch(username):
        return jsonify(error="Username must be 3-60 characters: letters, numbers, and . _ @ + -"), 400
    if len(password) < 3:
        return jsonify(error="Password must be at least 3 characters"), 400
    if role not in ("user", "admin"):
        return jsonify(error="Invalid account type"), 400
    if role == "admin":
        code = str(d.get("admin_code") or "")
        if not config.ADMIN_SIGNUP_CODE or not hmac.compare_digest(
            code.encode(), config.ADMIN_SIGNUP_CODE.encode()
        ):
            return jsonify(error="Invalid admin code"), 403

    # Ippo role-ai pass panrom, so admin 'ishaan' irundhalum user 'ishaan' allow aagum!
    if auth.username_exists(username, role=role):
        return jsonify(error=f"That username is already taken for {role}"), 409
    try:
        auth.create_account(username, password, role)
    except auth.IntegrityError:
        return jsonify(error=f"That username is already taken for {role}"), 409
    return jsonify(message="Account created. Please log in."), 201


# app.py - login endpoint

@app.post("/api/auth/login")
def login():
    d = request.get_json(silent=True) or {}
    username = str(d.get("username") or "").strip()
    password = str(d.get("password") or "")
    role = d.get("role")  # JS-la irundhu role varudha nu paarkkum

    # 1. First: Specific role kuduthurundhaa andha table-la paarkkum
    user = None
    if role in ("user", "admin"):
        user = auth.get_account_by_username(username, role=role)
    
    # 2. Oruvelai JS-la irundhu role varalana, auto-aa 'user' apram 'admin' table-la check pannum
    if not user:
        user = auth.get_account_by_username(username)

    ok = check_password_hash(user["password_hash"] if user else auth.DUMMY_HASH, password)
    if not user or not ok:
        return jsonify(error="Invalid username or password"), 401

    token = auth.make_token(user)
    resp = jsonify(
        token=token,
        token_type="Bearer",
        expires_in=config.TOKEN_HOURS * 3600,
        username=user["username"],
        role=user["role"],
    )
    resp.set_cookie(
        auth.COOKIE_NAME,
        token,
        httponly=True,
        samesite="Lax",
        secure=config.COOKIE_SECURE,
        max_age=config.TOKEN_HOURS * 3600,
    )
    return resp


@app.post("/api/auth/logout")
def logout():
    resp = jsonify(message="Logged out")
    resp.delete_cookie(auth.COOKIE_NAME)
    return resp


@app.get("/api/auth/me")
@auth.api_auth()
def me():
    return jsonify(username=g.user["username"], role=g.user["role"])


# ---------------- chat API ----------------
@app.post("/api/chat")
@auth.api_auth()
def chat():
    question = str((request.get_json(silent=True) or {}).get("question") or "").strip()
    if not question or len(question) > 1000:
        return jsonify(error="Question must be 1-1000 characters"), 400
    verdict = guardrails.check_input(question)          # 1) block prompt-injection
    safe_q = guardrails.mask_pii(question)              # 2) hide phone / email / Aadhaar ...
    if not verdict.ok:
        auth.log_chat(g.user, safe_q, "[BLOCKED: " + verdict.reason + "]", [], False)
        return jsonify(answer=guardrails.BLOCKED_ANSWER, sources=[])
    try:
        answer, sources, found = rag.ask(safe_q)
    except Exception:
        app.logger.exception("RAG failure")
        return jsonify(error="The assistant is unavailable right now. Please try again."), 500
    answer = guardrails.guard_output(answer)            # 3) last check on the answer
    auth.log_chat(g.user, safe_q, answer, sources, found)   # log stores the MASKED question
    return jsonify(answer=answer, sources=sources)


# ---------------- admin API ----------------
@app.get("/api/admin/documents")
@auth.api_auth("admin")
def list_docs():
    docs = kb.list_documents()
    return jsonify(documents=[{"name": n, "chunks": c} for n, c in sorted(docs.items())])


@app.post("/api/admin/upload")
@auth.api_auth("admin")
def upload():
    results = []
    for f in request.files.getlist("files"):
        name = secure_filename(f.filename or "")
        if not name.lower().endswith(ALLOWED_EXT):
            results.append({"name": f.filename, "error": "Only PDF, TXT or MD files are allowed"})
            continue
        path = config.DOCS_DIR / name
        f.save(path)
        try:
            results.append({"name": name, "chunks": kb.ingest_file(path)})
        except ValueError as e:
            path.unlink(missing_ok=True)
            results.append({"name": name, "error": str(e)})
        except Exception:
            app.logger.exception("Ingest failed for %s", name)
            path.unlink(missing_ok=True)
            results.append({"name": name, "error": "Could not read or index this file"})
    return jsonify(results=results)


@app.delete("/api/admin/documents/<name>")
@auth.api_auth("admin")
def delete_doc(name):
    if secure_filename(name) != name:
        return jsonify(error="Invalid file name"), 400
    kb.delete_document(name)
    return jsonify(message="Deleted")


@app.post("/api/admin/rebuild")
@auth.api_auth("admin")
def rebuild():
    try:
        return jsonify(results=kb.ingest_all())
    except Exception:
        app.logger.exception("Rebuild failed")
        return jsonify(error="Rebuild failed. Check the server log."), 500


@app.get("/api/admin/users")
@auth.api_auth("admin")
def list_users():
    """Direct MySQL connection inside route handler."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT u.username AS username, 'user' AS role, u.created_at AS created_at, "
                "(SELECT COUNT(*) FROM chat_logs l WHERE l.account_type = 'user' AND l.account_id = u.id) AS questions "
                "FROM users u "
                "UNION ALL "
                "SELECT a.username, 'admin', a.created_at, "
                "(SELECT COUNT(*) FROM chat_logs l WHERE l.account_type = 'admin' AND l.account_id = a.id) "
                "FROM admins a ORDER BY created_at DESC"
            )
            rows = cursor.fetchall()
            return jsonify(users=rows)
    finally:
        conn.close()


@app.get("/api/admin/unanswered")
@auth.api_auth("admin")
def unanswered():
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT question, created_at, username FROM chat_logs WHERE answered = 0 "
                "ORDER BY id DESC LIMIT 50"
            )
            rows = cursor.fetchall()
            return jsonify(items=rows)
    finally:
        conn.close()


@app.get("/api/admin/chats")
@auth.api_auth("admin")
def chat_history():
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT username, account_type AS role, question, answer, sources, answered, created_at "
                "FROM chat_logs ORDER BY id DESC LIMIT 200"
            )
            rows = cursor.fetchall()
            return jsonify(chats=rows)
    finally:
        conn.close()


if __name__ == "__main__":
    app.run(host="192.168.23.152", port=5000, debug=False)