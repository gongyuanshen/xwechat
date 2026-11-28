"""Helpers shared by several annual report cards."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from ...chat_helpers import _decode_sqlite_text


def _year_range_epoch_seconds(year: int) -> tuple[int, int]:
    # Keep the same semantics as other parts of the project: local time boundaries.
    start = int(datetime(year, 1, 1).timestamp())
    end = int(datetime(year + 1, 1, 1).timestamp())
    return start, end


def _mask_name(name: str) -> str:
    s = str(name or "").strip()
    if not s:
        return ""
    # 全星号：不保留任何原字符，长度封顶 6，数字类信息不经过本函数
    return "*" * max(1, min(len(s), 6))


def _list_message_tables(conn: sqlite3.Connection) -> list[str]:
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    except Exception:
        return []
    names: list[str] = []
    for r in rows:
        if not r or not r[0]:
            continue
        name = _decode_sqlite_text(r[0]).strip()
        if not name:
            continue
        ln = name.lower()
        if ln.startswith(("msg_", "chat_")):
            names.append(name)
    return names


def _list_session_usernames(session_db_path: Path) -> list[str]:
    if not session_db_path.exists():
        return []

    conn = sqlite3.connect(str(session_db_path))
    try:
        try:
            rows = conn.execute("SELECT username FROM SessionTable").fetchall()
        except sqlite3.OperationalError:
            rows = conn.execute("SELECT username FROM Session").fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()

    out: list[str] = []
    for r in rows:
        if not r or not r[0]:
            continue
        u = str(r[0]).strip()
        if u:
            out.append(u)
    return out
