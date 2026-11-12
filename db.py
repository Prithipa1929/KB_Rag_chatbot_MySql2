"""Direct MySQL database connection helper using PyMySQL."""
import re
import pymysql
from pymysql.cursors import DictCursor
from pymysql.err import IntegrityError  # re-exported for auth

import config


def get_db_connection(database=config.MYSQL_DATABASE):
    """Establishes and returns a direct connection to MySQL."""
    return pymysql.connect(
        host=config.MYSQL_HOST,
        port=config.MYSQL_PORT,
        user=config.MYSQL_USER,
        password=config.MYSQL_PASSWORD,
        database=database,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
    )


def ensure_database():
    """Creates the MySQL database if it does not exist yet."""
    name = config.MYSQL_DATABASE
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise RuntimeError("MYSQL_DATABASE may contain only letters, numbers and _")
    try:
        conn = get_db_connection(database=None)
    except pymysql.err.OperationalError as e:
        raise RuntimeError(
            f"Cannot connect to MySQL at {config.MYSQL_HOST}:{config.MYSQL_PORT} "
            f"as '{config.MYSQL_USER}'. Check MySQL service and credentials. ({e})"
        ) from e

    with conn.cursor() as cur:
        cur.execute(
            f"CREATE DATABASE IF NOT EXISTS `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
    conn.commit()
    conn.close()