from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator
from urllib.parse import unquote, urlparse

from backend.app.config import settings


class DatabaseUnavailable(RuntimeError):
    pass


def _parse_mysql_url(url: str) -> dict:
    parsed = urlparse(url)
    if parsed.scheme not in {"mysql", "mysql+pymysql"}:
        raise DatabaseUnavailable(f"unsupported DATABASE_URL scheme: {parsed.scheme}")
    return {
        "host": parsed.hostname or "127.0.0.1",
        "port": parsed.port or 3306,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": parsed.path.lstrip("/"),
        "charset": "utf8mb4",
        "autocommit": False,
        "cursorclass": _dict_cursor_class(),
    }


def _dict_cursor_class():
    try:
        import pymysql
    except ImportError as exc:
        raise DatabaseUnavailable("PyMySQL is not installed; run pip install -r backend/requirements.txt") from exc
    return pymysql.cursors.DictCursor


@contextmanager
def mysql_connection() -> Iterator:
    if not settings.database_url:
        raise DatabaseUnavailable("DATABASE_URL is not configured")
    try:
        import pymysql
    except ImportError as exc:
        raise DatabaseUnavailable("PyMySQL is not installed; run pip install -r backend/requirements.txt") from exc
    connection = pymysql.connect(**_parse_mysql_url(settings.database_url))
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
