"""Accounts, chat logs, JWT tokens and route guards using native MySQL."""
import datetime as dt
from functools import wraps

import jwt
from flask import g, jsonify, redirect, request
from werkzeug.security import generate_password_hash

import config
from db import IntegrityError, ensure_database, get_db_connection

DUMMY_HASH = generate_password_hash("not-a-real-password")
COOKIE_NAME = "access_token"
TABLE = {"user": "users", "admin": "admins"}


def init_db():
    ensure_database()
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            for table in TABLE.values():
                c.execute(
                    f"""CREATE TABLE IF NOT EXISTS {table} (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        username VARCHAR(60) NOT NULL UNIQUE,
                        password_hash VARCHAR(255) NOT NULL,
                        created_at VARCHAR(40) NOT NULL
                    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"""
                )
            c.execute(
                """CREATE TABLE IF NOT EXISTS chat_logs (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    account_type ENUM('user','admin') NOT NULL,
                    account_id INT NOT NULL,
                    username VARCHAR(60) NOT NULL,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    sources VARCHAR(500) NOT NULL DEFAULT '',
                    answered TINYINT(1) NOT NULL,
                    created_at VARCHAR(40) NOT NULL,
                    INDEX idx_chat_account (account_type, account_id),
                    INDEX idx_chat_answered (answered)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"""
            )
        conn.commit()
    finally:
        conn.close()


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def username_exists(username, role=None):
    """Checks username ONLY in that specific role's table."""
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            # Neenga user na users table mattum, admin na admins table mattum check pannum
            tables = [TABLE[role]] if role in TABLE else TABLE.values()
            for t in tables:
                c.execute(f"SELECT 1 AS x FROM {t} WHERE username = %s LIMIT 1", (username,))
                if c.fetchone():
                    return True
            return False
    finally:
        conn.close()


def create_account(username, password, role):
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                f"INSERT INTO {TABLE[role]} (username, password_hash, created_at) VALUES (%s, %s, %s)",
                (username, generate_password_hash(password), _now()),
            )
        conn.commit()
    finally:
        conn.close()


# auth.py

def get_account_by_username(username, role=None):
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            # Role kuduthurundhaa andha table mattum
            if role in TABLE:
                c.execute(f"SELECT * FROM {TABLE[role]} WHERE username = %s LIMIT 1", (username,))
                row = c.fetchone()
                if row:
                    row["role"] = role
                    return row
            else:
                # Role illana rendu table-layum thedum (user first, then admin)
                for r, table in TABLE.items():
                    c.execute(f"SELECT * FROM {table} WHERE username = %s LIMIT 1", (username,))
                    row = c.fetchone()
                    if row:
                        row["role"] = r
                        return row
        return None
    finally:
        conn.close()


def get_account(role, account_id):
    if role not in TABLE:
        return None
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            c.execute(f"SELECT * FROM {TABLE[role]} WHERE id = %s LIMIT 1", (account_id,))
            row = c.fetchone()
            if row:
                row["role"] = role
            return row
    finally:
        conn.close()


def log_chat(account, question, answer, sources, answered):
    conn = get_db_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                "INSERT INTO chat_logs (account_type, account_id, username, question, answer, sources, answered, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    account["role"],
                    account["id"],
                    account["username"],
                    question,
                    answer,
                    ", ".join(sources)[:500],
                    int(answered),
                    _now(),
                ),
            )
        conn.commit()
    finally:
        conn.close()


# ---------- tokens & guards ----------
def make_token(user):
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": str(user["id"]),
        "username": user["username"],
        "role": user["role"],
        "iat": now,
        "exp": now + dt.timedelta(hours=config.TOKEN_HOURS),
    }
    return jwt.encode(payload, config.SECRET_KEY, algorithm="HS256")


def _user_from_token(token):
    if not token:
        return None
    try:
        claims = jwt.decode(token, config.SECRET_KEY, algorithms=["HS256"])
        return get_account(claims["role"], int(claims["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


def user_from_cookie():
    return _user_from_token(request.cookies.get(COOKIE_NAME))


def user_from_bearer():
    header = request.headers.get("Authorization", "")
    return _user_from_token(header[7:]) if header.startswith("Bearer ") else None


def api_auth(role=None):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = user_from_bearer()
            if not user:
                return jsonify(error="Authentication required"), 401
            if role and user["role"] != role:
                return jsonify(error="You do not have permission"), 403
            g.user = user
            return fn(*args, **kwargs)
        return wrapper
    return deco


def page_auth(role=None):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = user_from_cookie()
            if not user:
                return redirect("/login")
            if role and user["role"] != role:
                return redirect("/chat")
            g.user = user
            return fn(*args, **kwargs)
        return wrapper
    return deco