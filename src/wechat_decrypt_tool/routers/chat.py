from ..account_identity import resolve_account_username
from ..snapshot_registry import resolve_account_database_dir
from ..account_workers import account_to_thread
import os
import re
import sqlite3
import json
import shutil
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from ..account_identity import (
    canonical_account_name,
    is_internal_account_directory_name,
    resolve_account_self_rowid,
    resolve_account_self_username,
)
from ..logging_config import get_logger
from ..chat_search_index import (
    get_chat_search_index_db_path,
    get_chat_search_index_status,
    start_chat_search_index_build,
)
from ..chat_accounts import list_chat_account_contexts, resolve_chat_account_context
from ..chat_helpers import (
    _build_avatar_url,
    _build_fts_query,
    _decode_message_content,
    _decode_sqlite_text,
    _extract_chatroom_top_message_metadata,
    _extract_image_group_info,
    _extract_md5_from_packed_info,
    _extract_voice_transcript_from_packed_info,
    _extract_sender_from_group_xml,
    _extract_xml_attr,
    _extract_image_cdn_refs,
    _extract_image_md5_candidates,
    _extract_xml_tag_or_attr,
    _extract_xml_tag_text,
    _format_session_time,
    _infer_last_message_brief,
    _infer_message_brief_by_local_type,
    _infer_transfer_status_text,
    _iter_message_db_paths,
    _list_decrypted_accounts,
    _make_search_tokens,
    _make_snippet,
    _load_contact_rows,
    _load_group_nickname_map_from_contact_db,
    _load_usernames_by_display_names,
    _load_latest_message_previews,
    _build_group_sender_display_name_map,
    _normalize_session_preview_text,
    _extract_group_preview_sender_username,
    _lookup_resource_md5,
    _normalize_xml_url,
    _parse_app_message,
    _parse_location_message,
    _parse_system_message_content,
    _parse_pat_message,
    _pick_display_name,
    _query_head_image_usernames,
    _quote_ident,
    _resolve_account_dir,
    _resolve_msg_table_name,
    _row_to_search_hit,
    _resource_lookup_chat_id,
    _should_keep_session,
    _split_group_sender_prefix,
    _to_char_token_text,
)
from ..app_paths import get_output_dir
from ..database_filters import list_countable_database_names
from ..key_store import remove_account_family_keys_from_store
from ..path_fix import PathFixRoute
from ..perf_trace import create_perf_trace, get_request_perf_context
from ..session_last_message import (
    build_session_last_message_table,
    get_session_last_message_status,
)
from ..anti_revoke import (
    format_revoked_message_as_chat_item,
    get_revoked_messages_list,
    get_revoked_messages_map,
    parse_revoke_xml,
    process_revocation_event,
)
from .chat_contacts import _load_enterprise_contact_info

logger = get_logger(__name__)

_DEBUG_SESSIONS = os.environ.get("WECHAT_TOOL_DEBUG_SESSIONS", "0") == "1"

router = APIRouter(route_class=PathFixRoute)







def _is_hex_md5(value: Any) -> bool:
    s = str(value or "").strip().lower()
    return len(s) == 32 and all(c in "0123456789abcdef" for c in s)


def _bytes_to_hex(value: bytes) -> str:
    return "0x" + value.hex()


def _is_mostly_printable_text(s: str) -> bool:
    if not s:
        return False
    sample = s[:600]
    if not sample:
        return False
    printable = sum(1 for ch in sample if ch.isprintable() or ch in {"\n", "\r", "\t"})
    return (printable / len(sample)) >= 0.85


def _jsonify_db_value(key: str, value: Any) -> Any:
    """Convert sqlite row values into JSON-friendly values (best-effort)."""
    if value is None:
        return None
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        b = bytes(value)
        k = str(key or "").strip().lower()
        if k in {"compress_content", "packed_info_data", "packed_info", "packedinfo", "packedinfodata"} or k.endswith(
            "_data"
        ):
            return _bytes_to_hex(b)
        if not b:
            return ""
        try:
            s = b.decode("utf-8")
            if _is_mostly_printable_text(s):
                return s
        except Exception:
            pass
        return _bytes_to_hex(b)
    if isinstance(value, (int, float, bool, str)):
        return value
    try:
        return str(value)
    except Exception:
        return None


def _pick_case_insensitive_value(item: Any, *keys: str) -> Any:
    if not isinstance(item, dict):
        return None
    for key in keys:
        if key in item and item[key] is not None:
            return item[key]
        key_lc = str(key or "").strip().lower()
        for actual_key, actual_value in item.items():
            if str(actual_key or "").strip().lower() == key_lc and actual_value is not None:
                return actual_value
    return None


def _avatar_url_unified(
    *,
    account_dir: Path,
    username: str,
    local_avatar_usernames: set[str] | None = None,
) -> str:
    u = str(username or "").strip()
    if not u:
        return ""
    # Unified avatar entrypoint: backend decides local db vs remote fallback + cache.
    return _build_avatar_url(str(account_dir.name or ""), u)





def _load_group_nickname_map(
    *, account_dir: Path, contact_db_path: Path, chatroom_id: str,
    sender_usernames: list[str],
) -> dict[str, str]:
    """Read group cards from the account's contact database."""
    return _load_group_nickname_map_from_contact_db(contact_db_path, chatroom_id, sender_usernames)


def _resolve_sender_display_name(*, sender_username: str, sender_contact_rows: dict[str, sqlite3.Row], group_nicknames: Optional[dict[str, str]] = None) -> str:
    username = str(sender_username or "").strip()
    if not username:
        return ""
    group_name = str((group_nicknames or {}).get(username) or "").strip()
    return group_name or _pick_display_name(sender_contact_rows.get(username), username)


def _resolve_system_message_display_name(*, sender_username: str, fallback_display_name: str, sender_contact_rows: dict[str, sqlite3.Row]) -> str:
    username = str(sender_username or "").strip()
    fallback = str(fallback_display_name or "").strip()
    if not username:
        return fallback or "你"
    display_name = _pick_display_name(sender_contact_rows.get(username), username)
    if display_name != username:
        return display_name
    return fallback or username


def _postprocess_special_message_content(*, message: dict[str, Any], sender_contact_rows: dict[str, sqlite3.Row]) -> None:
    raw = str(message.get("_rawText") or "")
    if not raw:
        message.pop("_rawText", None)
        return

    local_type = int(message.get("type") or 0)
    if local_type == 266287972401:
        message["content"] = _parse_pat_message(raw, sender_contact_rows)
    elif local_type == 10000:
        message["content"] = _parse_system_message_content(
            raw,
            resolve_display_name=lambda sender_username, fallback_display_name="": _resolve_system_message_display_name(
                sender_username=sender_username,
                fallback_display_name=fallback_display_name,
                sender_contact_rows=sender_contact_rows,

            ),
        )

    message.pop("_rawText", None)


def _row_get_value(row: Any, *keys: str) -> Any:
    if isinstance(row, dict):
        return _pick_case_insensitive_value(row, *keys)
    for key in keys:
        try:
            return row[key]
        except Exception:
            continue
    return None


def _decode_msg_source(value: Any) -> str:
    """Decode Msg.source / msg_source.

    In recent PC WeChat message DBs this field is often a zstd-compressed
    `<msgsource>...</msgsource>` XML payload. Reuse the message-content decoder
    so live WCDB hex/base64/raw-blob representations are handled consistently.
    """

    try:
        return _decode_message_content(None, value).strip()
    except Exception:
        return _decode_sqlite_text(value).strip()


def _extract_at_usernames_from_source(value: Any) -> list[str]:
    source_xml = _decode_msg_source(value)
    if not source_xml:
        return []
    at_raw = _extract_xml_tag_text(source_xml, "atuserlist")
    if not at_raw:
        return []

    out: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[,;\s]+", str(at_raw or "")):
        username = part.strip()
        if not username or username in seen:
            continue
        seen.add(username)
        out.append(username)
    return out






def _normalize_chat_source(value: Optional[str]) -> str:
    value = str(value or "").strip().lower()
    if value in {"", "auto", "default", "wechat", "decrypted", "local", "sqlite"}:
        return "decrypted"
    raise HTTPException(status_code=400, detail="Invalid source; only decrypted snapshots are supported.")
















def _lookup_contact_alias(
    conn: Optional[sqlite3.Connection],
    cache: dict[str, str],
    username: str,
) -> str:
    u = str(username or "").strip()
    if not u or conn is None:
        return ""
    if u in cache:
        return cache[u]

    alias = ""
    try:
        r = conn.execute("SELECT alias FROM contact WHERE username = ? LIMIT 1", (u,)).fetchone()
        if r is not None and r[0] is not None:
            alias = str(r[0] or "").strip()
        if not alias:
            r = conn.execute("SELECT alias FROM stranger WHERE username = ? LIMIT 1", (u,)).fetchone()
            if r is not None and r[0] is not None:
                alias = str(r[0] or "").strip()
    except Exception:
        alias = ""

    cache[u] = alias
    return alias










def _local_month_range_epoch_seconds(*, year: int, month: int) -> tuple[int, int]:
    """Return [start, end) range as epoch seconds for local time month boundaries.

    Notes:
    - Uses local midnight boundaries (not +86400 * days) to stay DST-safe.
    - Returned timestamps are integers (seconds).
    """

    start = datetime(int(year), int(month), 1)
    if int(month) == 12:
        end = datetime(int(year) + 1, 1, 1)
    else:
        end = datetime(int(year), int(month) + 1, 1)
    return int(start.timestamp()), int(end.timestamp())


def _local_day_range_epoch_seconds(*, date_str: str) -> tuple[int, int, str]:
    """Return [start, end) range as epoch seconds for local date boundaries.

    Returns the normalized `YYYY-MM-DD` date string as the 3rd element.
    """

    d0 = datetime.strptime(str(date_str or "").strip(), "%Y-%m-%d")
    d1 = d0 + timedelta(days=1)
    return int(d0.timestamp()), int(d1.timestamp()), d0.strftime("%Y-%m-%d")


















def _load_session_last_message_meta(account_dir: Path, usernames: list[str]) -> dict[str, dict[str, Any]]:
    """Load per-session latest message cache used to correct stale SessionTable rows."""

    uniq = list(dict.fromkeys([str(u or "").strip() for u in usernames if str(u or "").strip()]))
    if not uniq:
        return {}

    session_db_path = resolve_account_database_dir(account_dir) / "session.db"
    if not session_db_path.exists():
        return {}

    out: dict[str, dict[str, Any]] = {}
    conn = sqlite3.connect(str(session_db_path))
    conn.row_factory = sqlite3.Row
    try:
        chunk_size = 900
        for i in range(0, len(uniq), chunk_size):
            chunk = uniq[i : i + chunk_size]
            placeholders = ",".join(["?"] * len(chunk))
            try:
                rows = conn.execute(
                    "SELECT username, create_time, sort_seq, local_id, local_type, sender_username, preview "
                    f"FROM session_last_message WHERE username IN ({placeholders})",
                    chunk,
                ).fetchall()
            except Exception:
                continue
            for r in rows:
                u = str(r["username"] or "").strip()
                if not u:
                    continue
                try:
                    create_time = int(r["create_time"] or 0)
                except Exception:
                    create_time = 0
                try:
                    sort_seq = int(r["sort_seq"] or 0)
                except Exception:
                    sort_seq = 0
                try:
                    local_id = int(r["local_id"] or 0)
                except Exception:
                    local_id = 0
                out[u] = {
                    "create_time": int(create_time or 0),
                    "sort_seq": int(sort_seq or 0),
                    "local_id": int(local_id or 0),
                    "local_type": int(r["local_type"] or 0),
                    "sender_username": str(r["sender_username"] or "").strip(),
                    "preview": str(r["preview"] or "").strip(),
                }
    finally:
        conn.close()
    return out


def _session_row_get(row: Any, key: str, default: Any = None) -> Any:
    try:
        if isinstance(row, sqlite3.Row):
            return row[key]
    except Exception:
        return default
    try:
        return row.get(key, default)
    except Exception:
        return default


def _contact_flag_is_top(flag_value: Any) -> bool:
    try:
        flag_int = int(flag_value)
    except Exception:
        return False
    if flag_int < 0:
        flag_int &= (1 << 64) - 1
    return bool((flag_int >> 11) & 1)


def _load_contact_top_flags(contact_db_path: Path, usernames: list[str]) -> dict[str, bool]:
    uniq = list(dict.fromkeys([str(u or "").strip() for u in usernames if str(u or "").strip()]))
    if not uniq:
        return {}
    if not contact_db_path.exists():
        return {}

    out: dict[str, bool] = {}
    conn = sqlite3.connect(str(contact_db_path))
    conn.row_factory = sqlite3.Row
    try:
        def has_flag_column(table: str) -> bool:
            try:
                rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            except Exception:
                return False
            cols: set[str] = set()
            for r in rows:
                try:
                    cols.add(str(r["name"] if isinstance(r, sqlite3.Row) else r[1]).strip().lower())
                except Exception:
                    continue
            return ("username" in cols) and ("flag" in cols)

        chunk_size = 900
        for table in ("contact", "stranger"):
            if not has_flag_column(table):
                continue

            for i in range(0, len(uniq), chunk_size):
                chunk = uniq[i : i + chunk_size]
                placeholders = ",".join(["?"] * len(chunk))
                try:
                    rows = conn.execute(
                        f"SELECT username, flag FROM {table} WHERE username IN ({placeholders})",
                        chunk,
                    ).fetchall()
                except Exception:
                    continue

                for r in rows:
                    username = str(_session_row_get(r, "username", "") or "").strip()
                    if not username:
                        continue
                    is_top = _contact_flag_is_top(_session_row_get(r, "flag", 0))
                    if is_top:
                        out[username] = True
                    else:
                        out.setdefault(username, False)
        return out
    finally:
        conn.close()


def _normalize_session_type(value: Optional[str]) -> Optional[str]:
    v = str(value or "").strip().lower()
    if not v or v in {"all", "any", "none", "null", "0"}:
        return None
    if v in {"group", "groups", "chatroom", "chatrooms"}:
        return "group"
    if v in {"single", "singles", "person", "people", "user", "users", "contact", "contacts"}:
        return "single"
    raise HTTPException(status_code=400, detail="Invalid session_type, use 'group' or 'single'.")


def _normalize_render_type_key(value: Any) -> str:
    v = str(value or "").strip()
    if not v:
        return ""
    if v == "redPacket":
        return "redpacket"
    lower = v.lower()
    if lower in {"redpacket", "red_packet", "red-packet", "redenvelope", "red_envelope"}:
        return "redpacket"
    return lower


@router.get("/api/chat/search-index/status", summary="消息搜索索引状态")
async def chat_search_index_status(account: Optional[str] = None, source: Optional[str] = None):
    account_dir = _resolve_account_dir(account)
    return get_chat_search_index_status(account_dir, source=_normalize_chat_source(source))


@router.post("/api/chat/search-index/build", summary="构建/重建消息搜索索引")
async def chat_search_index_build(account: Optional[str] = None, rebuild: bool = False, source: Optional[str] = None):
    account_dir = _resolve_account_dir(account)
    return start_chat_search_index_build(account_dir, rebuild=bool(rebuild), source=_normalize_chat_source(source))


@router.get("/api/chat/session-last-message/status", summary="会话最后一条消息缓存表状态")
async def session_last_message_status(account: Optional[str] = None):
    account_dir = _resolve_account_dir(account)
    return get_session_last_message_status(account_dir)


@router.post("/api/chat/session-last-message/build", summary="构建/重建会话最后一条消息缓存表")
async def session_last_message_build(
    account: Optional[str] = None,
    rebuild: bool = False,
    include_hidden: bool = True,
    include_official: bool = True,
):
    account_dir = _resolve_account_dir(account)
    return build_session_last_message_table(
        account_dir,
        rebuild=bool(rebuild),
        include_hidden=bool(include_hidden),
        include_official=bool(include_official),
    )


@router.get("/api/chat/search-index/senders", summary="消息搜索索引发送者列表")
async def chat_search_index_senders(
    account: Optional[str] = None,
    username: Optional[str] = None,
    session_type: Optional[str] = None,
    message_q: Optional[str] = None,
    limit: int = 200,
    q: Optional[str] = None,
    start_time: Optional[int] = None,
    end_time: Optional[int] = None,
    render_types: Optional[str] = None,
    include_hidden: bool = False,
    include_official: bool = False,
    source: Optional[str] = None,
):
    if limit <= 0:
        limit = 200
    if limit > 2000:
        limit = 2000

    username = str(username or "").strip()
    if not username:
        username = None

    session_type_norm = _normalize_session_type(session_type)

    message_q = str(message_q or "").strip()
    if not message_q:
        message_q = None

    q = str(q or "").strip()
    if not q:
        q = None

    account_dir = _resolve_account_dir(account)
    contact_db_path = resolve_account_database_dir(account_dir) / "contact.db"
    source_requested = _normalize_chat_source(source)
    index_status = get_chat_search_index_status(account_dir, source=source_requested)
    index = dict(index_status.get("index") or {})
    build = dict(index.get("build") or {})

    index_exists = bool(index.get("exists"))
    index_ready = bool(index.get("ready"))
    build_status = str(build.get("status") or "").strip()

    if (not index_ready) and build_status not in {"building", "error"}:
        start_chat_search_index_build(account_dir, rebuild=bool(index_exists), source=source_requested)
        index_status = get_chat_search_index_status(account_dir, source=source_requested)
        index = dict(index_status.get("index") or {})
        build = dict(index.get("build") or {})
        build_status = str(build.get("status") or "").strip()
        index_exists = bool(index.get("exists"))
        index_ready = bool(index.get("ready"))

    if build_status == "error":
        return {
            "status": "index_error",
            "account": account_dir.name,
            "username": username,
            "scope": "conversation" if username else "global",
            "senders": [],
            "index": index,
            "message": str(build.get("error") or "Search index build failed."),
        }

    if not index_ready:
        return {
            "status": "index_building",
            "account": account_dir.name,
            "username": username,
            "scope": "conversation" if username else "global",
            "senders": [],
            "index": index,
            "message": "Search index is building. Please retry in a moment.",
        }

    index_db_path = get_chat_search_index_db_path(account_dir)
    conn = sqlite3.connect(str(index_db_path))
    conn.row_factory = sqlite3.Row
    try:
        where_parts: list[str] = ["sender_username <> ''"]
        params: list[Any] = []

        if message_q is not None:
            fts_query = _build_fts_query(message_q)
            if fts_query:
                where_parts.insert(0, "message_fts MATCH ?")
                params.append(fts_query)

        if username is not None:
            where_parts.append("username = ?")
            params.append(username)
        elif session_type_norm == "group":
            where_parts.append("username LIKE ?")
            params.append("%@chatroom")
        elif session_type_norm == "single":
            where_parts.append("username NOT LIKE ?")
            params.append("%@chatroom")

        if q is not None:
            where_parts.append("sender_username LIKE ?")
            params.append(f"%{q}%")

        want_types: Optional[set[str]] = None
        if render_types is not None:
            parts = [p.strip() for p in str(render_types or "").split(",") if p.strip()]
            want_types = {p for p in parts if p}
            if not want_types:
                want_types = None

        if want_types is not None:
            types_sorted = sorted(want_types)
            placeholders = ",".join(["?"] * len(types_sorted))
            where_parts.append(f"render_type IN ({placeholders})")
            params.extend(types_sorted)

        start_ts = int(start_time) if start_time is not None else None
        end_ts = int(end_time) if end_time is not None else None
        if start_ts is not None and start_ts < 0:
            start_ts = 0
        if end_ts is not None and end_ts < 0:
            end_ts = 0

        if start_ts is not None:
            where_parts.append("CAST(create_time AS INTEGER) >= ?")
            params.append(int(start_ts))
        if end_ts is not None:
            where_parts.append("CAST(create_time AS INTEGER) <= ?")
            params.append(int(end_ts))

        if not include_hidden:
            where_parts.append("CAST(is_hidden AS INTEGER) = 0")
        if not include_official:
            where_parts.append("CAST(is_official AS INTEGER) = 0")

        where_sql = " AND ".join(where_parts)
        rows = conn.execute(
            f"""
            SELECT
                sender_username AS sender_username,
                COUNT(*) AS c
            FROM message_fts
            WHERE {where_sql}
            GROUP BY sender_username
            ORDER BY c DESC, sender_username ASC
            LIMIT ?
            """,
            params + [int(limit)],
        ).fetchall()
    finally:
        conn.close()

    sender_usernames = [str(r["sender_username"] or "").strip() for r in rows if r and r["sender_username"]]
    sender_usernames = [u for u in sender_usernames if u]
    contact_rows = _load_contact_rows(contact_db_path, sender_usernames)
    head_image_db_path = resolve_account_database_dir(account_dir) / "head_image.db"
    local_sender_avatars = _query_head_image_usernames(head_image_db_path, sender_usernames)

    senders: list[dict[str, Any]] = []
    for r in rows:
        su = str(r["sender_username"] or "").strip()
        if not su:
            continue
        cnt = int(r["c"] or 0)
        row = contact_rows.get(su)
        avatar_url = _avatar_url_unified(
            account_dir=account_dir,
            username=su,
            local_avatar_usernames=local_sender_avatars,
        )
        senders.append(
            {
                "username": su,
                "displayName": _pick_display_name(row, su) if row is not None else su,
                "avatar": avatar_url,
                "count": cnt,
            }
        )

    return {
        "status": "success",
        "account": account_dir.name,
        "username": username,
        "scope": "conversation" if username else "global",
        "senders": senders,
        "index": index,
    }


def _append_full_messages_from_rows(
    *,
    merged: list[dict[str, Any]],
    sender_usernames: list[str],
    quote_usernames: list[str],
    pat_usernames: set[str],
    rows: list[sqlite3.Row],
    db_path: Path,
    table_name: str,
    username: str,
    account_dir: Path,
    is_group: bool,
    my_rowid: Optional[int],
    resource_conn: Optional[sqlite3.Connection],
    resource_chat_id: Optional[int],
    self_username: str = "",
) -> None:
    resolved_self_username = str(
        self_username or resolve_account_self_username(account_dir) or account_dir.name or ""
    ).strip()
    contact_conn: Optional[sqlite3.Connection] = None
    alias_cache: dict[str, str] = {}
    if is_group:
        try:
            contact_db_path = resolve_account_database_dir(account_dir) / "contact.db"
            if contact_db_path.exists():
                contact_conn = sqlite3.connect(str(contact_db_path))
        except Exception:
            contact_conn = None

    for r in rows:
        effective_db_path = db_path
        effective_table_name = table_name
        effective_my_rowid = my_rowid
        if isinstance(r, dict):
            row_db_path = str(_pick_case_insensitive_value(r, "_db_path", "db_path", "dbPath") or "").strip()
            if row_db_path:
                try:
                    effective_db_path = Path(row_db_path)
                except Exception:
                    effective_db_path = db_path
            row_table_name = str(_pick_case_insensitive_value(r, "table_name", "tableName") or "").strip()
            if row_table_name:
                effective_table_name = row_table_name
            row_my = _pick_case_insensitive_value(r, "__my_rowid", "_my_rowid", "debug_my_rowid", "my_rowid", "myRowid")
            if row_my is not None:
                try:
                    row_my_int = int(row_my or 0)
                    if row_my_int > 0:
                        effective_my_rowid = row_my_int
                except Exception:
                    pass

        local_id = int(r["local_id"] or 0)
        create_time = int(r["create_time"] or 0)
        sort_seq = int(r["sort_seq"] or 0) if r["sort_seq"] is not None else 0
        local_type = int(r["local_type"] or 0)
        native_voice_transcript = (
            _extract_voice_transcript_from_packed_info(_row_get_value(r, "packed_info_data"))
            if local_type == 34
            else ""
        )
        sender_username = _decode_sqlite_text(r["sender_username"]).strip()

        is_sent = False
        if effective_my_rowid is not None:
            try:
                is_sent = int(r["real_sender_id"] or 0) == int(effective_my_rowid)
            except Exception:
                is_sent = False
        else:

            for k in (
                "computed_is_send",
                "computed_is_sent",
                "computed_isSend",
                "is_send",
                "isSent",
            ):
                try:
                    v = r[k]
                except Exception:
                    v = None
                if v is None:
                    continue
                try:
                    is_sent = bool(int(v))
                except Exception:
                    is_sent = bool(v)
                break

            if not is_sent:
                # Fallback: some builds include the resolved "my rowid" for debugging.
                try:
                    my_debug = None
                    for k2 in ("debug_my_rowid", "debugMyRowid", "my_rowid", "myRowid"):
                        try:
                            my_debug = r[k2]
                            break
                        except Exception:
                            continue
                    if my_debug is not None and int(my_debug or 0) > 0:
                        is_sent = int(r["real_sender_id"] or 0) == int(my_debug)
                except Exception:
                    pass

            if not is_sent:
                try:
                    su = str(sender_username or "").strip().lower()
                    me = resolved_self_username.lower()
                    if su and me and su == me:
                        is_sent = True
                except Exception:
                    pass

        raw_text = _decode_message_content(r["compress_content"], r["message_content"])
        raw_text = raw_text.strip()
        at_usernames = _extract_at_usernames_from_source(_row_get_value(r, "msg_source", "source"))

        sender_prefix = ""
        if is_group and raw_text and (not raw_text.startswith("<")) and (not raw_text.startswith('"<')):
            sender_alias = ""
            sep = raw_text.find(":\n")
            if sep > 0:
                prefix = raw_text[:sep].strip()
                if prefix and sender_username and prefix != sender_username:
                    strong_hint = prefix.startswith("wxid_") or prefix.endswith("@chatroom") or "@" in prefix
                    if not strong_hint:
                        body_probe = raw_text[sep + 2 :].lstrip("\n").lstrip()
                        body_is_xml = body_probe.startswith("<") or body_probe.startswith('"<')
                        if not body_is_xml:
                            sender_alias = _lookup_contact_alias(contact_conn, alias_cache, sender_username)
            sender_prefix, raw_text = _split_group_sender_prefix(raw_text, sender_username, sender_alias)

        if is_group and sender_prefix and (not sender_username):
            sender_username = sender_prefix

        if is_group and (not sender_username) and (raw_text.startswith("<") or raw_text.startswith('"<')):
            xml_sender = _extract_sender_from_group_xml(raw_text)
            if xml_sender:
                sender_username = xml_sender

        if is_sent:
            sender_username = resolved_self_username
        elif (not is_group) and (not sender_username):
            sender_username = username

        if sender_username:
            sender_usernames.append(sender_username)

        render_type = "text"
        content_text = raw_text
        title = ""
        url = ""
        from_name = ""
        from_username = ""
        record_item = ""
        image_md5 = ""
        emoji_md5 = ""
        emoji_url = ""
        thumb_url = ""
        image_url = ""
        image_file_id = ""
        image_group_type = ""
        image_group_id = ""
        image_group_count = 0
        video_md5 = ""
        video_thumb_md5 = ""
        video_file_id = ""
        video_thumb_file_id = ""
        video_url = ""
        video_thumb_url = ""
        voice_length = ""
        quote_username = ""
        quote_title = ""
        quote_content = ""
        quote_thumb_url = ""
        link_type = ""
        link_style = ""
        object_id = ""
        object_nonce_id = ""
        quote_server_id = ""
        quote_type = ""
        quote_voice_length = ""
        amount = ""
        cover_url = ""
        file_size = ""
        pay_sub_type = ""
        transfer_status = ""
        file_md5 = ""
        transfer_id = ""
        voip_type = ""
        location_lat: Optional[float] = None
        location_lng: Optional[float] = None
        location_poiname = ""
        location_label = ""

        revoked_server_id = ""
        revoked_local_id = 0
        message_revoke_time = 0
        revoke_original_status = ""
        if local_type == 10000:
            render_type = "system"
            content_text = _parse_system_message_content(raw_text)
            if "revokemsg" in raw_text.lower():
                try:
                    parsed_rev = parse_revoke_xml(raw_text)
                    if parsed_rev:
                        revoked_server_id = str(parsed_rev.get("newmsgid") or "")
                        message_revoke_time = int(parsed_rev.get("revoketime") or create_time or 0)
                        revoked = process_revocation_event(
                            account_dir=account_dir,
                            account_name=account_dir.name,
                            username=username,
                            xml_text=raw_text,
                            revoke_time=create_time or int(time.time()),
                            server_id=str(r["server_id"] or "0"),
                            local_id=local_id,
                            source_db=effective_db_path.stem,
                            source_table=effective_table_name,
                        )
                        revoke_original_status = "captured" if revoked is not None else "unresolved"
                        if revoked is not None:
                            revoked_server_id = str(revoked["server_id"] or "")
                            revoked_local_id = int(revoked["local_id"] or 0)
                            message_revoke_time = int(revoked["revoke_time"])
                except Exception as e:
                    logger.exception("[anti_revoke] process_revocation_event failed account=%s user=%s local_id=%s: %s", account_dir.name, username, local_id, e)
                    raise
        elif local_type == 49:
            parsed = _parse_app_message(raw_text)
            render_type = str(parsed.get("renderType") or "text")
            content_text = str(parsed.get("content") or "")
            title = str(parsed.get("title") or "")
            url = str(parsed.get("url") or "")
            from_name = str(parsed.get("from") or "")
            from_username = str(parsed.get("fromUsername") or "")
            record_item = str(parsed.get("recordItem") or "")
            quote_title = str(parsed.get("quoteTitle") or "")
            quote_content = str(parsed.get("quoteContent") or "")
            quote_thumb_url = str(parsed.get("quoteThumbUrl") or "")
            link_type = str(parsed.get("linkType") or "")
            link_style = str(parsed.get("linkStyle") or "")
            object_id = str(parsed.get("objectId") or "")
            object_nonce_id = str(parsed.get("objectNonceId") or "")
            quote_username = str(parsed.get("quoteUsername") or "")
            quote_server_id = str(parsed.get("quoteServerId") or "")
            quote_type = str(parsed.get("quoteType") or "")
            quote_voice_length = str(parsed.get("quoteVoiceLength") or "")
            amount = str(parsed.get("amount") or "")
            cover_url = str(parsed.get("coverUrl") or "")
            thumb_url = str(parsed.get("thumbUrl") or "")
            file_size = str(parsed.get("size") or "")
            pay_sub_type = str(parsed.get("paySubType") or "")
            file_md5 = str(parsed.get("fileMd5") or "")
            transfer_id = str(parsed.get("transferId") or "")

            if render_type == "transfer":
                # 直接从原始 XML 提取 transferid（可能在 wcpayinfo 内）
                if not transfer_id:
                    transfer_id = _extract_xml_tag_or_attr(raw_text, "transferid") or ""
                transfer_status = _infer_transfer_status_text(
                    is_sent=is_sent,
                    paysubtype=pay_sub_type,
                    receivestatus=str(parsed.get("receiveStatus") or ""),
                    sendertitle=str(parsed.get("senderTitle") or ""),
                    receivertitle=str(parsed.get("receiverTitle") or ""),
                    senderdes=str(parsed.get("senderDes") or ""),
                    receiverdes=str(parsed.get("receiverDes") or ""),
                )
                if not content_text:
                    content_text = transfer_status or "转账"
        elif local_type == 266287972401:
            render_type = "system"
            template = _extract_xml_tag_text(raw_text, "template")
            if template:
                pat_usernames.update(
                    {m.group(1) for m in re.finditer(r"\$\{([^}]+)\}", template) if m.group(1)}
                )
                content_text = "[拍一拍]"
            else:
                content_text = "[拍一拍]"
        elif local_type == 244813135921:
            render_type = "quote"
            parsed = _parse_app_message(raw_text)
            content_text = str(parsed.get("content") or "[引用消息]")
            quote_title = str(parsed.get("quoteTitle") or "")
            quote_content = str(parsed.get("quoteContent") or "")
            quote_thumb_url = str(parsed.get("quoteThumbUrl") or "")
            link_type = str(parsed.get("linkType") or "")
            link_style = str(parsed.get("linkStyle") or "")
            quote_username = str(parsed.get("quoteUsername") or "")
            quote_server_id = str(parsed.get("quoteServerId") or "")
            quote_type = str(parsed.get("quoteType") or "")
            quote_voice_length = str(parsed.get("quoteVoiceLength") or "")
        elif local_type == 3:
            render_type = "image"
            image_group_info = _extract_image_group_info(raw_text)
            image_group_type = str(image_group_info.get("type") or "")
            image_group_id = str(image_group_info.get("id") or "")
            image_group_count = int(image_group_info.get("count") or 0)
            xml_md5s = _extract_image_md5_candidates(raw_text)
            image_md5 = xml_md5s[0] if xml_md5s else ''

            # Prefer message_resource.db md5 for local files: XML md5 frequently differs from the on-disk *.dat basename
            # (especially for *_t.dat thumbnails), causing the media endpoint to 404.
            if resource_conn is not None:
                try:
                    resource_md5 = _lookup_resource_md5(
                        resource_conn,
                        resource_chat_id,
                        message_local_type=local_type,
                        server_id=int(r["server_id"] or 0),
                        local_id=local_id,
                        create_time=create_time,
                    )
                except Exception:
                    resource_md5 = ""
                resource_md5 = str(resource_md5 or "").strip().lower()
                if len(resource_md5) == 32 and all(c in "0123456789abcdef" for c in resource_md5):
                    image_md5 = resource_md5

            try:
                packed_val = r["packed_info_data"]
            except Exception:
                try:
                    packed_val = r.get("packed_info_data")  # type: ignore[attr-defined]
                except Exception:
                    packed_val = None
            packed_md5 = _extract_md5_from_packed_info(packed_val)
            if packed_md5:
                image_md5 = packed_md5

            image_url, image_file_id, _ = _extract_image_cdn_refs(raw_text)

            content_text = "[图片]"
        elif local_type == 34:
            render_type = "voice"
            duration = _extract_xml_attr(raw_text, "voicelength")
            voice_length = duration
            content_text = f"[语音 {duration}秒]" if duration else "[语音]"
        elif local_type == 43 or local_type == 62:
            render_type = "video"
            video_md5 = _extract_xml_attr(raw_text, "md5")
            video_thumb_md5 = _extract_xml_attr(raw_text, "cdnthumbmd5")
            video_thumb_url_or_id = _extract_xml_attr(raw_text, "cdnthumburl") or _extract_xml_tag_text(
                raw_text, "cdnthumburl"
            )
            video_url_or_id = _extract_xml_attr(raw_text, "cdnvideourl") or _extract_xml_tag_text(
                raw_text, "cdnvideourl"
            )

            video_thumb_url_or_id = _normalize_xml_url(video_thumb_url_or_id)
            video_url_or_id = _normalize_xml_url(video_url_or_id)

            video_thumb_url = (
                video_thumb_url_or_id
                if str(video_thumb_url_or_id or "").strip().lower().startswith(("http://", "https://"))
                else ""
            )
            video_url = (
                video_url_or_id
                if str(video_url_or_id or "").strip().lower().startswith(("http://", "https://"))
                else ""
            )
            video_thumb_file_id = "" if video_thumb_url else (str(video_thumb_url_or_id or "").strip() or "")
            video_file_id = "" if video_url else (str(video_url_or_id or "").strip() or "")
            if (not video_thumb_md5) and resource_conn is not None:
                video_thumb_md5 = _lookup_resource_md5(
                    resource_conn,
                    resource_chat_id,
                    message_local_type=local_type,
                    server_id=int(r["server_id"] or 0),
                    local_id=local_id,
                    create_time=create_time,
                )

            # Match WeFlow's video strategy: packed_info_data often stores the local msg/video basename.
            # Prefer this token for video lookup; keep XML CDN/file_id as fallback query parameters.
            try:
                packed_val = r["packed_info_data"]
            except Exception:
                try:
                    packed_val = r.get("packed_info_data")  # type: ignore[attr-defined]
                except Exception:
                    packed_val = None
            packed_video_token = _extract_md5_from_packed_info(packed_val)
            if packed_video_token:
                video_md5 = packed_video_token
                if not _is_hex_md5(video_thumb_md5):
                    video_thumb_md5 = packed_video_token
                    video_thumb_file_id = ""
            content_text = "[视频]"
        elif local_type == 47:
            render_type = "emoji"
            emoji_md5 = _extract_xml_attr(raw_text, "md5")
            if not emoji_md5:
                emoji_md5 = _extract_xml_tag_text(raw_text, "md5")
            emoji_url = _extract_xml_attr(raw_text, "cdnurl")
            if not emoji_url:
                emoji_url = _extract_xml_tag_text(raw_text, "cdn_url")
            emoji_url = _normalize_xml_url(emoji_url)
            if (not emoji_md5) and resource_conn is not None:
                emoji_md5 = _lookup_resource_md5(
                    resource_conn,
                    resource_chat_id,
                    message_local_type=local_type,
                    server_id=int(r["server_id"] or 0),
                    local_id=local_id,
                    create_time=create_time,
                )
            content_text = "[表情]"
        elif local_type == 48:
            parsed = _parse_location_message(raw_text)
            render_type = str(parsed.get("renderType") or "location")
            content_text = str(parsed.get("content") or "[Location]")
            location_lat = parsed.get("locationLat")
            location_lng = parsed.get("locationLng")
            location_poiname = str(parsed.get("locationPoiname") or "")
            location_label = str(parsed.get("locationLabel") or "")
        elif local_type == 50:
            render_type = "voip"
            try:
                block = raw_text
                m_voip = re.search(
                    r"(<VoIPBubbleMsg[^>]*>.*?</VoIPBubbleMsg>)",
                    raw_text,
                    flags=re.IGNORECASE | re.DOTALL,
                )
                if m_voip:
                    block = m_voip.group(1) or raw_text
                room_type = str(_extract_xml_tag_text(block, "room_type") or "").strip()
                if room_type == "0":
                    voip_type = "video"
                elif room_type == "1":
                    voip_type = "audio"

                voip_msg = str(_extract_xml_tag_text(block, "msg") or "").strip()
                content_text = voip_msg or "通话"
            except Exception:
                content_text = "通话"
        elif local_type != 1:
            if not content_text:
                content_text = _infer_message_brief_by_local_type(local_type)
            else:
                if content_text.startswith("<") or content_text.startswith('"<'):
                    parsed_special = False
                    if "<appmsg" in content_text.lower():
                        parsed = _parse_app_message(content_text)
                        rt = str(parsed.get("renderType") or "")
                        if rt and rt != "text":
                            parsed_special = True
                            render_type = rt
                            content_text = str(parsed.get("content") or content_text)
                            title = str(parsed.get("title") or title)
                            url = str(parsed.get("url") or url)
                            record_item = str(parsed.get("recordItem") or record_item)
                            quote_title = str(parsed.get("quoteTitle") or quote_title)
                            quote_content = str(parsed.get("quoteContent") or quote_content)
                            quote_thumb_url = str(parsed.get("quoteThumbUrl") or quote_thumb_url)
                            link_type = str(parsed.get("linkType") or link_type)
                            link_style = str(parsed.get("linkStyle") or link_style)
                            object_id = str(parsed.get("objectId") or object_id)
                            object_nonce_id = str(parsed.get("objectNonceId") or object_nonce_id)
                            amount = str(parsed.get("amount") or amount)
                            cover_url = str(parsed.get("coverUrl") or cover_url)
                            thumb_url = str(parsed.get("thumbUrl") or thumb_url)
                            from_name = str(parsed.get("from") or from_name)
                            from_username = str(parsed.get("fromUsername") or from_username)
                            file_size = str(parsed.get("size") or file_size)
                            pay_sub_type = str(parsed.get("paySubType") or pay_sub_type)
                            file_md5 = str(parsed.get("fileMd5") or file_md5)
                            transfer_id = str(parsed.get("transferId") or transfer_id)
                            quote_username = str(parsed.get("quoteUsername") or quote_username)
                            quote_server_id = str(parsed.get("quoteServerId") or quote_server_id)
                            quote_type = str(parsed.get("quoteType") or quote_type)
                            quote_voice_length = str(parsed.get("quoteVoiceLength") or quote_voice_length)

                            if render_type == "transfer":
                                # 如果 transferId 仍为空，尝试从原始 XML 提取
                                if not transfer_id:
                                    transfer_id = _extract_xml_tag_or_attr(content_text, "transferid") or ""
                                transfer_status = _infer_transfer_status_text(
                                    is_sent=is_sent,
                                    paysubtype=pay_sub_type,
                                    receivestatus=str(parsed.get("receiveStatus") or ""),
                                    sendertitle=str(parsed.get("senderTitle") or ""),
                                    receivertitle=str(parsed.get("receiverTitle") or ""),
                                    senderdes=str(parsed.get("senderDes") or ""),
                                    receiverdes=str(parsed.get("receiverDes") or ""),
                                )
                                if not content_text:
                                    content_text = transfer_status or "转账"

                    if not parsed_special:
                        t = _extract_xml_tag_text(content_text, "title")
                        d = _extract_xml_tag_text(content_text, "des")
                        content_text = t or d or _infer_message_brief_by_local_type(local_type)

        if not content_text:
            content_text = _infer_message_brief_by_local_type(local_type)

        if quote_username:
            quote_usernames.append(str(quote_username).strip())

        merged.append(
            {
                "id": f"{effective_db_path.stem}:{effective_table_name}:{local_id}",
                "localId": local_id,
                "serverId": int(r["server_id"] or 0),
                "serverIdStr": str(int(r["server_id"] or 0)) if int(r["server_id"] or 0) else "",
                "type": local_type,
                "createTime": create_time,
                "sortSeq": sort_seq,
                "senderUsername": sender_username,
                "isSent": bool(is_sent),
                "renderType": render_type,
                "content": content_text,
                "atUsernames": at_usernames,
                "atUsers": [],
                "title": title,
                "url": url,
                "linkType": link_type,
                "linkStyle": link_style,
                "objectId": object_id,
                "objectNonceId": object_nonce_id,
                "from": from_name,
                "fromUsername": from_username,
                "recordItem": record_item,
                "imageMd5": image_md5,
                "imageFileId": image_file_id,
                "imageGroupType": image_group_type,
                "imageGroupId": image_group_id,
                "imageGroupCount": image_group_count,
                "emojiMd5": emoji_md5,
                "emojiUrl": emoji_url,
                "thumbUrl": thumb_url,
                "imageUrl": image_url,
                "videoMd5": video_md5,
                "videoThumbMd5": video_thumb_md5,
                "videoFileId": video_file_id,
                "videoThumbFileId": video_thumb_file_id,
                "videoUrl": video_url,
                "videoThumbUrl": video_thumb_url,
                "voiceLength": voice_length,
                "voiceTranscript": native_voice_transcript,
                "voiceTranscriptStatus": "success" if native_voice_transcript else "idle",
                "voiceTranscriptError": "",
                "voiceTranscriptLanguage": "",
                "voiceTranscriptModel": "wechat-native" if native_voice_transcript else "",
                "voipType": voip_type,
                "quoteUsername": str(quote_username).strip(),
                "quoteServerId": str(quote_server_id).strip(),
                "quoteType": str(quote_type).strip(),
                "quoteVoiceLength": str(quote_voice_length).strip(),
                "quoteTitle": quote_title,
                "quoteContent": quote_content,
                "quoteThumbUrl": quote_thumb_url,
                "amount": amount,
                "coverUrl": cover_url,
                "fileSize": file_size,
                "fileMd5": file_md5,
                "paySubType": pay_sub_type,
                "transferStatus": transfer_status,
                "transferId": transfer_id,
                "locationLat": location_lat,
                "locationLng": location_lng,
                "locationPoiname": location_poiname,
                "locationLabel": location_label,
                "revokedServerId": revoked_server_id,
                "revokedLocalId": revoked_local_id,
                "revokeTime": message_revoke_time,
                "revokeOriginalStatus": revoke_original_status,
                "_rawText": raw_text if local_type in (10000, 266287972401) else "",
            }
        )

    if contact_conn is not None:
        try:
            contact_conn.close()
        except Exception:
            pass


def _postprocess_transfer_messages(merged: list[dict[str, Any]]) -> None:
    # 后处理：关联转账消息的最终状态
    # 策略：优先使用 transferId 精确匹配，回退到金额+时间窗口匹配
    # paysubtype 含义：1=不明确 3=已收款 4=对方退回给你 8=发起转账 9=被对方退回 10=已过期
    #
    # Windows 微信在部分场景会为同一笔转账记录两条消息：
    # - paysubtype=1/8：发起/待收款（这里回填为“已被接收”）
    # - paysubtype=3：收款确认（展示为“已收款”）
    #
    # 这两条消息的 isSent 并不能稳定表示“付款方/收款方视角”，因此这里以 transferId 关联结果为准：
    # - 将原始转账消息（1/8）回填为“已被接收”
    # - 若同一 transferId 同时存在原始消息与 paysubtype=3 消息，则将 paysubtype=3 的那条校正为“已收款”

    def _is_transfer_expired_system_message(text: Any) -> bool:
        content = str(text or "").strip()
        if not content:
            return False
        if "转账" not in content or "过期" not in content:
            return False
        if "未接收" in content and ("24小时" in content or "二十四小时" in content):
            return True
        return "已过期" in content and ("收款方" in content or "转账" in content)

    def _mark_pending_transfers_expired_by_system_messages() -> set[str]:
        expired_system_times: list[int] = []
        pending_candidates: list[tuple[int, int]] = []  # (index, createTime)

        for idx, msg in enumerate(merged):
            rt = str(msg.get("renderType") or "").strip()
            if rt == "system":
                if _is_transfer_expired_system_message(msg.get("content")):
                    try:
                        ts = int(msg.get("createTime") or 0)
                    except Exception:
                        ts = 0
                    if ts > 0:
                        expired_system_times.append(ts)
                continue

            if rt != "transfer":
                continue

            pst = str(msg.get("paySubType") or "").strip()
            if pst not in ("1", "8"):
                continue

            try:
                ts = int(msg.get("createTime") or 0)
            except Exception:
                ts = 0
            if ts <= 0:
                continue

            pending_candidates.append((idx, ts))

        if not expired_system_times or not pending_candidates:
            return set()

        used_pending_indexes: set[int] = set()
        expired_transfer_ids: set[str] = set()

        # 过期系统提示通常出现在转账发起约 24 小时后。
        # 为避免误匹配，要求时间差落在 [22h, 26h] 范围内，并选择最接近 24h 的待收款消息。
        for sys_ts in sorted(expired_system_times):
            best_index = -1
            best_distance = 10**9

            for idx, transfer_ts in pending_candidates:
                if idx in used_pending_indexes:
                    continue
                delta = sys_ts - transfer_ts
                if delta < 0:
                    continue
                if delta < 22 * 3600 or delta > 26 * 3600:
                    continue

                distance = abs(delta - 24 * 3600)
                if distance < best_distance:
                    best_distance = distance
                    best_index = idx

            if best_index < 0:
                continue

            used_pending_indexes.add(best_index)
            transfer_msg = merged[best_index]
            transfer_msg["paySubType"] = "10"
            transfer_msg["transferStatus"] = "已过期"

            tid = str(transfer_msg.get("transferId") or "").strip()
            if tid:
                expired_transfer_ids.add(tid)

        return expired_transfer_ids

    expired_transfer_ids = _mark_pending_transfers_expired_by_system_messages()

    returned_transfer_ids: set[str] = set()  # 退还状态的 transferId
    received_transfer_ids: set[str] = set()  # 已收款状态的 transferId
    returned_amounts_with_time: list[tuple[str, int]] = []  # (金额, 时间戳) 用于退还回退匹配
    received_amounts_with_time: list[tuple[str, int]] = []  # (金额, 时间戳) 用于收款回退匹配
    pending_transfer_ids: set[str] = set()  # (paysubtype=1/8) 的 transferId，用于识别“收款确认”消息

    for m in merged:
        if m.get("renderType") != "transfer":
            continue

        pst = str(m.get("paySubType") or "")
        tid = str(m.get("transferId") or "").strip()
        amt = str(m.get("amount") or "")
        ts = int(m.get("createTime") or 0)

        if tid and pst in ("1", "8"):
            pending_transfer_ids.add(tid)

        if pst in ("4", "9"):  # 退还状态
            if tid:
                returned_transfer_ids.add(tid)
            if amt:
                returned_amounts_with_time.append((amt, ts))
        elif pst == "3":  # 已收款状态
            if tid:
                received_transfer_ids.add(tid)
            if amt:
                received_amounts_with_time.append((amt, ts))

    backfilled_message_ids: set[str] = set()

    for m in merged:
        if m.get("renderType") != "transfer":
            continue

        pst = str(m.get("paySubType") or "")
        if pst not in ("1", "8"):
            continue

        tid = str(m.get("transferId") or "").strip()
        amt = str(m.get("amount") or "")
        ts = int(m.get("createTime") or 0)

        should_mark_returned = False
        should_mark_received = False

        # 策略1：精确 transferId 匹配
        if tid:
            if tid in returned_transfer_ids:
                should_mark_returned = True
            elif tid in received_transfer_ids:
                should_mark_received = True

        # 策略2：回退到金额+时间窗口匹配（24小时内同金额）
        if not should_mark_returned and not should_mark_received and amt:
            for ret_amt, ret_ts in returned_amounts_with_time:
                if ret_amt == amt and abs(ret_ts - ts) <= 86400:
                    should_mark_returned = True
                    break
            if not should_mark_returned:
                for rec_amt, rec_ts in received_amounts_with_time:
                    if rec_amt == amt and abs(rec_ts - ts) <= 86400:
                        should_mark_received = True
                        break

        if should_mark_returned:
            m["paySubType"] = "9"
            m["transferStatus"] = "已被退还"
        elif should_mark_received:
            m["paySubType"] = "3"
            m["transferStatus"] = "已被接收"
            mid = str(m.get("id") or "").strip()
            if mid:
                backfilled_message_ids.add(mid)

    # 修正收款确认消息：当同一 transferId 同时存在原始转账消息（1/8）与收款消息（3）时，
    # paysubtype=3 的那条通常是收款确认消息，状态文案应为“已收款”。
    for m in merged:
        if m.get("renderType") != "transfer":
            continue
        pst = str(m.get("paySubType") or "")
        if pst != "3":
            continue
        tid = str(m.get("transferId") or "").strip()
        if not tid or tid not in pending_transfer_ids:
            continue
        if tid in expired_transfer_ids:
            continue
        mid = str(m.get("id") or "").strip()
        if mid and mid in backfilled_message_ids:
            continue
        m["transferStatus"] = "已收款"


def _postprocess_full_messages(*, merged: list[dict[str, Any]], sender_usernames: list[str], quote_usernames: list[str], pat_usernames: set[str], account_dir: Path, username: str, base_url: str, contact_db_path: Path, head_image_db_path: Path) -> None:
    _postprocess_transfer_messages(merged)

    # Some appmsg payloads provide only `from` (sourcedisplayname) but not `fromUsername` (sourceusername).
    # Recover `fromUsername` via contact.db so the frontend can render the publisher avatar.
    missing_from_names = [
        str(m.get("from") or "").strip()
        for m in merged
        if str(m.get("renderType") or "").strip() == "link"
        and str(m.get("from") or "").strip()
        and not str(m.get("fromUsername") or "").strip()
    ]
    if missing_from_names:
        name_to_username = _load_usernames_by_display_names(contact_db_path, missing_from_names)
        if name_to_username:
            for m in merged:
                if str(m.get("fromUsername") or "").strip():
                    continue
                if str(m.get("renderType") or "").strip() != "link":
                    continue
                fn = str(m.get("from") or "").strip()
                if fn and fn in name_to_username:
                    m["fromUsername"] = name_to_username[fn]

    system_usernames: set[str] = set()
    for m in merged:
        if int(m.get("type") or 0) != 10000:
            continue
        meta = _extract_chatroom_top_message_metadata(str(m.get("_rawText") or ""))
        operator_username = str(meta.get("operatorUsername") or "").strip()
        if operator_username:
            system_usernames.add(operator_username)

    from_usernames = [str(m.get("fromUsername") or "").strip() for m in merged]
    at_usernames = [
        str(u or "").strip()
        for m in merged
        for u in (m.get("atUsernames") or [])
        if str(u or "").strip()
    ]
    uniq_senders = list(
        dict.fromkeys(
            [
                u
                for u in (
                    sender_usernames
                    + list(pat_usernames)
                    + quote_usernames
                    + from_usernames
                    + at_usernames
                    + list(system_usernames)
                )
                if u
            ]
        )
    )
    sender_contact_rows = _load_contact_rows(contact_db_path, uniq_senders)
    local_sender_avatars = _query_head_image_usernames(head_image_db_path, uniq_senders)


    group_nicknames = _load_group_nickname_map(
        account_dir=account_dir,
        contact_db_path=contact_db_path,
        chatroom_id=username,
        sender_usernames=uniq_senders,
    )

    enterprise_contacts = _load_enterprise_contact_info(contact_db_path, sender_usernames)
    for m in merged:
        # If appmsg doesn't provide sourcedisplayname, try mapping sourceusername to display name.
        if (not str(m.get("from") or "").strip()) and str(m.get("fromUsername") or "").strip():
            fu = str(m.get("fromUsername") or "").strip()
            frow = sender_contact_rows.get(fu)
            if frow is not None:
                m["from"] = _pick_display_name(frow, fu)

        su = str(m.get("senderUsername") or "")
        if su:
            m["senderEnterpriseName"] = enterprise_contacts.get(su, {}).get("enterpriseName", "")
            m["senderDisplayName"] = _resolve_sender_display_name(
                sender_username=su,
                sender_contact_rows=sender_contact_rows,

                group_nicknames=group_nicknames,
            )
            avatar_url = base_url + _avatar_url_unified(
                account_dir=account_dir,
                username=su,
                local_avatar_usernames=local_sender_avatars,
            )
            m["senderAvatar"] = avatar_url

        msg_at_usernames = list(
            dict.fromkeys([str(u or "").strip() for u in (m.get("atUsernames") or []) if str(u or "").strip()])
        )
        m["atUsernames"] = msg_at_usernames
        if msg_at_usernames:
            at_users: list[dict[str, Any]] = []
            for au in msg_at_usernames:
                if au == "notify@all":
                    at_users.append(
                        {
                            "username": au,
                            "displayName": "所有人",
                            "avatar": "",
                        }
                    )
                    continue
                at_users.append(
                    {
                        "username": au,
                        "displayName": _resolve_sender_display_name(
                            sender_username=au,
                            sender_contact_rows=sender_contact_rows,

                            group_nicknames=group_nicknames,
                        ),
                        "avatar": base_url
                        + _avatar_url_unified(
                            account_dir=account_dir,
                            username=au,
                            local_avatar_usernames=local_sender_avatars,
                        ),
                    }
                )
            m["atUsers"] = at_users
        else:
            m["atUsers"] = []

        qu = str(m.get("quoteUsername") or "").strip()
        if qu:
            qrow = sender_contact_rows.get(qu)
            qt = str(m.get("quoteTitle") or "").strip()
            if qrow is not None:
                remark = ""
                try:
                    remark = str(qrow["remark"] or "").strip()
                except Exception:
                    remark = ""
                if remark:
                    m["quoteTitle"] = remark
                elif not qt:
                    title = _pick_display_name(qrow, qu)
                    m["quoteTitle"] = title
            elif not qt:
                m["quoteTitle"] = qu

        # Media URL fallback: if CDN URLs missing, use local media endpoints.
        try:
            rt = str(m.get("renderType") or "")
            if rt == "image":
                if not str(m.get("imageUrl") or ""):
                    md5 = str(m.get("imageMd5") or "").strip()
                    file_id = str(m.get("imageFileId") or "").strip()
                    if md5:
                        m["imageUrl"] = (
                            base_url
                            + f"/api/chat/media/image?account={quote(account_dir.name)}&md5={quote(md5)}&username={quote(username)}"
                        )
                    elif file_id:
                        m["imageUrl"] = (
                            base_url
                            + f"/api/chat/media/image?account={quote(account_dir.name)}&file_id={quote(file_id)}&username={quote(username)}"
                        )
            elif rt == "emoji":
                md5 = str(m.get("emojiMd5") or "")
                if md5:
                    existing_local: Optional[Path] = None

                    # The local endpoint resolves encrypted sources and exposes 404/422;
                    # message assembly must not bypass it or scan/decode every image.
                    try:
                        cur = str(m.get("emojiUrl") or "")
                        if cur and re.match(r"^https?://", cur, flags=re.I) and (
                            "/api/chat/media/emoji" not in cur
                        ):
                            m["emojiRemoteUrl"] = cur
                    except Exception:
                        pass

                    m["emojiUrl"] = (
                        base_url
                        + f"/api/chat/media/emoji?account={quote(account_dir.name)}&md5={quote(md5)}&username={quote(username)}"
                    )
            elif rt == "video":
                video_thumb_url = str(m.get("videoThumbUrl") or "").strip()
                video_thumb_md5 = str(m.get("videoThumbMd5") or "").strip()
                video_thumb_file_id = str(m.get("videoThumbFileId") or "").strip()
                if (not video_thumb_url) or (
                    not video_thumb_url.lower().startswith(("http://", "https://"))
                ):
                    if video_thumb_md5:
                        m["videoThumbUrl"] = (
                            base_url
                            + f"/api/chat/media/video_thumb?account={quote(account_dir.name)}&md5={quote(video_thumb_md5)}&username={quote(username)}"
                            + (f"&file_id={quote(video_thumb_file_id)}" if video_thumb_file_id else "")
                        )
                    elif video_thumb_file_id:
                        m["videoThumbUrl"] = (
                            base_url
                            + f"/api/chat/media/video_thumb?account={quote(account_dir.name)}&file_id={quote(video_thumb_file_id)}&username={quote(username)}"
                        )

                video_url = str(m.get("videoUrl") or "").strip()
                video_md5 = str(m.get("videoMd5") or "").strip()
                video_file_id = str(m.get("videoFileId") or "").strip()
                if (not video_url) or (not video_url.lower().startswith(("http://", "https://"))):
                    if video_md5:
                        m["videoUrl"] = (
                            base_url
                            + f"/api/chat/media/video?account={quote(account_dir.name)}&md5={quote(video_md5)}&username={quote(username)}"
                            + (f"&file_id={quote(video_file_id)}" if video_file_id else "")
                        )
                    elif video_file_id:
                        m["videoUrl"] = (
                            base_url
                            + f"/api/chat/media/video?account={quote(account_dir.name)}&file_id={quote(video_file_id)}&username={quote(username)}"
                        )
            elif rt == "link":
                # Some appmsg link cards (notably Bilibili shares) carry a non-HTTP `<thumburl>` payload
                # (often an ASN.1-ish hex blob). The actual preview image is typically saved as:
                #   msg/attach/{md5(conv_username)}/.../Img/{local_id}_{create_time}_t.dat
                # Expose it via the existing image endpoint using file_id.
                thumb_url = str(m.get("thumbUrl") or "").strip()
                if thumb_url and (not thumb_url.lower().startswith(("http://", "https://"))):
                    try:
                        lid = int(m.get("localId") or 0)
                    except Exception:
                        lid = 0
                    try:
                        ct = int(m.get("createTime") or 0)
                    except Exception:
                        ct = 0
                    if lid > 0 and ct > 0:
                        file_id = f"{lid}_{ct}"
                        m["thumbUrl"] = (
                            base_url
                            + f"/api/chat/media/image?account={quote(account_dir.name)}&file_id={quote(file_id)}&username={quote(username)}"
                        )
            elif rt == "voice":
                if str(m.get("serverId") or ""):
                    sid = int(m.get("serverId") or 0)
                    if sid:
                        m["voiceUrl"] = base_url + f"/api/chat/media/voice?account={quote(account_dir.name)}&server_id={sid}"
        except Exception:
            pass

        _postprocess_special_message_content(
            message=m,
            sender_contact_rows=sender_contact_rows,

        )


@router.get("/api/chat/accounts", summary="列出聊天账号")
async def list_chat_accounts():
    """列出可用于聊天预览的账号（direct WCDB + legacy decrypted 兼容）。"""
    contexts = list_chat_account_contexts()
    accounts = [ctx.name for ctx in contexts]
    account_infos = [_chat_account_context_public(ctx) for ctx in contexts]
    key_ready_accounts = [ctx.name for ctx in contexts if bool(getattr(ctx, "keys_ready", False))]
    switchable_accounts = [
        ctx.name for ctx in contexts
        if (ctx.has_decrypted_dbs)
    ]
    switchable_account_set = set(switchable_accounts)
    switchable_account_infos = [
        info for info in account_infos if str(info.get("account") or "").strip() in switchable_account_set
    ]
    if not contexts:
        return {
            "status": "error",
            "accounts": [],
            "default_account": None,
            "switchable_accounts": [],
            "switchableAccounts": [],
            "keyReadyAccounts": [],
            "default_switchable_account": None,
            "defaultSwitchableAccount": None,
            "accountInfos": [],
            "items": [],
            "switchableAccountInfos": [],
            "message": "No chat accounts found. Please save a db key/db_storage path or decrypt first.",
        }

    return {
        "status": "success",
        "accounts": accounts,
        "default_account": accounts[0],
        "switchable_accounts": switchable_accounts,
        "switchableAccounts": switchable_accounts,
        "keyReadyAccounts": key_ready_accounts,
        "default_switchable_account": switchable_accounts[0] if switchable_accounts else None,
        "defaultSwitchableAccount": switchable_accounts[0] if switchable_accounts else None,
        "accountInfos": account_infos,
        "items": account_infos,
        "switchableAccountInfos": switchable_account_infos,
    }


_ACCOUNT_PROFILE_NAME_KEYS = (
    "selfDisplayName",
    "self_display_name",
    "nickname",
    "nickName",
    "nick_name",
    "nick",
    "displayName",
    "display_name",
)
_ACCOUNT_PROFILE_INVISIBLE_CHARS = ("\u3164", "\u200b", "\u200c", "\u200d", "\ufeff")


def _clean_account_display_name(value: Any, fallback_username: str = "") -> str:
    text = str(value or "")
    for char in _ACCOUNT_PROFILE_INVISIBLE_CHARS:
        text = text.replace(char, "")
    text = text.strip()
    fallback = str(fallback_username or "").strip()
    if not text or (fallback and text.casefold() == fallback.casefold()):
        return ""
    return text


def _read_account_profile_display_name(account_dir: Path, fallback_username: str) -> str:
    """Read the account nickname from its saved profile."""
    for filename in ("account.json", "_source.json"):
        try:
            value = json.loads((Path(account_dir) / filename).read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(value, dict):
            continue
        candidates: list[dict[str, Any]] = [value]
        for nested_key in ("original_info", "profile"):
            nested = value.get(nested_key)
            if isinstance(nested, dict):
                candidates.append(nested)
        for candidate in candidates:
            for key in _ACCOUNT_PROFILE_NAME_KEYS:
                display_name = _clean_account_display_name(candidate.get(key), fallback_username)
                if display_name:
                    return display_name
    return ""


def _resolve_account_self_display_name(account_dir: Path, username: str) -> str:
    account_path = Path(account_dir)
    display_name = _read_account_profile_display_name(account_path, username)
    if display_name:
        return display_name
    rows = _load_contact_rows(resolve_account_database_dir(account_path) / "contact.db", [username])
    return _clean_account_display_name(_pick_display_name(rows.get(username), username), username)


def _chat_account_context_public(ctx: Any) -> dict[str, Any]:
    account_dir = Path(ctx.account_dir)
    db_files = list_countable_database_names(account_dir)
    username = resolve_account_username(account_dir)
    self_display_name = _resolve_account_self_display_name(account_dir, username)
    has_decrypted_dbs = bool(ctx.has_decrypted_dbs)
    return {
        "account": ctx.name, "name": ctx.name, "selfUsername": username,
        "selfDisplayName": self_display_name, "nickname": self_display_name,
        "displayName": self_display_name, "mode": "decrypted", "defaultSource": "decrypted",
        "path": str(account_dir), "dataSourcePath": str(account_dir), "accountDir": str(account_dir),
        "hasDecryptedDbs": has_decrypted_dbs, "database_count": len(db_files), "databases": db_files,
        "dbKeyPresent": bool(ctx.db_key_present), "imageKeyPresent": bool(ctx.image_key_present),
        "imageXorKeyPresent": bool(ctx.image_xor_key_present), "imageAesKeyPresent": bool(ctx.image_aes_key_present),
        "keysReady": bool(ctx.keys_ready), "keyReady": bool(ctx.keys_ready),
        "switchable": has_decrypted_dbs, "keysUpdatedAt": str(ctx.keys_updated_at or ""),
    }


@router.get("/api/chat/account_info", summary="获取当前账号信息")
def get_chat_account_info(account: Optional[str] = None):
    ctx = resolve_chat_account_context(account)
    account_dir = ctx.account_dir
    db_files = list_countable_database_names(account_dir)

    session_db = resolve_account_database_dir(account_dir) / "session.db"
    session_updated_at = 0
    try:
        session_updated_at = int(session_db.stat().st_mtime)
    except Exception:
        session_updated_at = 0

    info = _chat_account_context_public(ctx)
    info.update({
        "status": "success",
        "account": account_dir.name,
        "database_count": len(db_files),
        "databases": db_files,
        "session_updated_at": session_updated_at,
    })
    return info


@router.delete("/api/chat/account", summary="删除当前账号在本项目中的数据")
def delete_chat_account(account: str):
    from ..ai.service import get_ai_service
    with get_ai_service().account_lifecycle_lock:
        return _delete_chat_account(account)


def _delete_chat_account(account: str):
    from ..snapshot_refresh import SNAPSHOT_REFRESH
    from ..snapshot_registry import (
        SnapshotRegistryError, account_deletion_scope, require_account_idle,
    )

    requested_account_name = str(account or "").strip()
    if not requested_account_name:
        raise HTTPException(status_code=400, detail="Missing account.")
    try:
        account_dir = _resolve_account_delete_root(requested_account_name)
        with account_deletion_scope(account_dir):
            SNAPSHOT_REFRESH.stop_and_join(account_dir.name)
            require_account_idle(account_dir)
            result = _delete_chat_account_data(account_dir, requested_account_name)
            SNAPSHOT_REFRESH.forget_account(account_dir.name)
    except SnapshotRegistryError as exc:
        raise HTTPException(status_code=409, detail=f"账号数据尚不能删除，现有数据已保留：{exc}") from exc
    try:
        accounts = _list_decrypted_accounts()
    except Exception as exc:
        logger.exception("Account was deleted but refreshing the account list failed")
        raise HTTPException(status_code=500, detail={
            "code": "account_deleted_list_failed", "deleted_account": account_dir.name,
            "message": f"账号已删除，但更新账号列表失败：{exc}",
            "removed_paths": result["removed_paths"],
        }) from exc
    return {**result, "accounts": accounts, "default_account": accounts[0] if accounts else None}


def _resolve_account_delete_root(account: str) -> Path:
    """Deletion must remain possible when a source DB or pointer is damaged."""
    from ..chat_accounts import _safe_account_name
    from ..snapshot_registry import SnapshotRegistryError, _check_ancestors

    selected = _safe_account_name(account)
    canonical = _safe_account_name(canonical_account_name(account))
    if not canonical or not (selected or canonical != account):
        raise HTTPException(status_code=400, detail="Invalid account.")
    parent = get_output_dir().absolute() / "databases"
    _check_ancestors(parent)
    # Canonical and source-suffix aliases are an explicitly shared deletion
    # family; no database availability or directory-name similarity is used.
    for name in dict.fromkeys((canonical, selected)):
        if not name:
            continue
        candidate = parent / name
        _check_ancestors(candidate)
        if candidate.exists():
            if not candidate.is_dir():
                raise SnapshotRegistryError(f"Account deletion target is not a directory: {candidate}")
            return candidate
    raise HTTPException(status_code=404, detail="Account directory not found.")


def _account_delete_targets(account_dir: Path, requested_account_name: str):
    """Resolve every owned target before the first destructive operation."""
    from ..snapshot_registry import SnapshotRegistryError, _check_ancestors, _read_json, _reject_link
    from ..key_store import load_account_keys_store
    import stat

    output = get_output_dir().absolute()
    root = Path(account_dir).absolute()
    family = canonical_account_name(root.name)
    if root.parent != output / "databases" or not family or is_internal_account_directory_name(root.name):
        raise SnapshotRegistryError("Account deletion must target a stable output account directory")
    _check_ancestors(output)
    cleanup_names = list(dict.fromkeys((root.name, requested_account_name, family)))
    targets: list[Path] = []
    backups: list[Path] = []
    generations: list[Path] = []
    protected_sources: list[Path] = []
    planned_snapshots: dict[str, dict[str, Any]] = {}

    def protect_source(value):
        if not isinstance(value, str) or not value or not Path(value).is_absolute():
            raise SnapshotRegistryError("Account source metadata has no absolute source path")
        protected_sources.append(Path(value).resolve())

    plan_path = root / "_account_delete_plan.json"
    if plan_path.exists():
        plan = _read_json(plan_path)
        if (type(plan.get("version")) is not int or plan["version"] != 1
                or plan.get("account") != root.name or plan.get("family") != family
                or not isinstance(plan.get("snapshot_targets"), list)
                or not isinstance(plan.get("protected_sources"), list)):
            raise SnapshotRegistryError("Invalid persisted account deletion plan")
        for item in plan["snapshot_targets"]:
            if (not isinstance(item, dict) or item.get("account") != family
                    or not isinstance(item.get("path"), str)
                    or not re.fullmatch(r"verified_snapshots/(?:generation-[0-9a-f]{32}|\.snapshot-[A-Za-z0-9_-]+)", item["path"])
                    or type(item.get("device")) is not int or item["device"] < 0
                    or type(item.get("inode")) is not int or item["inode"] <= 0
                    or item["path"] in planned_snapshots):
                raise SnapshotRegistryError("Invalid snapshot target in persisted account deletion plan")
            planned_snapshots[item["path"]] = item
        for value in plan["protected_sources"]:
            protect_source(value)

    for category in ("databases", "exports", "account_backups", "avatar_cache"):
        parent = output / category
        _check_ancestors(parent)
        if not parent.exists():
            continue
        for candidate in parent.iterdir():
            if canonical_account_name(candidate.name) != family:
                continue
            if candidate.name not in cleanup_names:
                cleanup_names.append(candidate.name)
            targets.append(candidate)
            if category == "account_backups" or is_internal_account_directory_name(candidate.name):
                backups.append(candidate)

    parent = output / "verified_snapshots"
    _check_ancestors(parent)
    if parent.exists():
        for candidate in parent.iterdir():
            candidate_info = candidate.lstat()
            _reject_link(candidate, candidate_info)
            if not candidate.is_dir():
                raise SnapshotRegistryError(f"Unexpected snapshot entry; ownership is unknown: {candidate}")
            if not (re.fullmatch(r"generation-[0-9a-f]{32}", candidate.name) or candidate.name.startswith(".snapshot-")):
                raise SnapshotRegistryError(f"Unknown snapshot directory: {candidate}")
            owner_path = candidate / "ownership.json"
            manifest_path = candidate / "manifest.json"
            planned = planned_snapshots.get(candidate.relative_to(output).as_posix())
            if planned is not None and (planned["device"], planned["inode"]) != (candidate_info.st_dev, candidate_info.st_ino):
                raise SnapshotRegistryError(f"Persisted deletion target was replaced: {candidate}")
            if owner_path.exists():
                owner = _read_json(owner_path)
                if type(owner.get("version")) is not int or owner["version"] != 1:
                    raise SnapshotRegistryError(f"Unsupported snapshot ownership: {candidate}")
                name = owner.get("account")
                protect_source(owner.get("source_db_storage_path"))
            elif manifest_path.exists() and candidate.name.startswith("generation-"):
                owner = _read_json(manifest_path)
                name = owner.get("account")
                if owner.get("generation_path") != str(candidate) or owner.get("account_path") != str(candidate / "databases" / str(name)):
                    raise SnapshotRegistryError(f"Snapshot manifest path does not match: {candidate}")
            elif planned is not None:
                # rmtree can remove all metadata before Windows rejects the
                # final rmdir. Only the previously verified directory identity
                # permits retry; arbitrary empty candidates remain unknown.
                name = planned["account"]
            else:
                raise SnapshotRegistryError(f"Snapshot ownership is unknown; retained for inspection: {candidate}")
            if not isinstance(name, str) or not name or Path(name).name != name or any(x in name for x in ("/", "\\", ":")):
                raise SnapshotRegistryError(f"Invalid snapshot account owner: {candidate}")
            if planned is not None and canonical_account_name(name) != family:
                raise SnapshotRegistryError(f"Persisted deletion target belongs to another account: {candidate}")
            if owner_path.exists() and manifest_path.exists():
                manifest = _read_json(manifest_path)
                if manifest.get("account") != name or manifest.get("generation_path") != str(candidate):
                    raise SnapshotRegistryError(f"Snapshot ownership and manifest disagree: {candidate}")
            if canonical_account_name(name) == family:
                targets.append(candidate)
                generations.append(candidate)

    def walk_error(exc):
        raise exc

    for target in targets:
        _check_ancestors(target)
        if target.resolve().parent not in {
            output / "databases", output / "exports", output / "account_backups",
            output / "avatar_cache", output / "verified_snapshots",
        }:
            raise SnapshotRegistryError(f"Account deletion target escaped its permitted directory: {target}")
        if not target.is_dir():
            raise SnapshotRegistryError(f"Account data target is not a directory: {target}")
        for directory, dirs, files in os.walk(target, followlinks=False, onerror=walk_error):
            for name in dirs + files:
                entry = Path(directory) / name
                info = entry.lstat()
                _reject_link(entry, info)
                if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                    raise SnapshotRegistryError(f"Account data contains an unsupported file type: {entry}")
                if name == "_source.json" and name in files:
                    source = _read_json(entry)
                    for key in ("db_storage_path", "wxid_dir"):
                        if source.get(key):
                            protect_source(source[key])

    # Strict preflight prevents a corrupt key store masquerading as an empty one.
    store = load_account_keys_store(strict=True)
    for name, entry in store.items():
        if not isinstance(entry, dict):
            raise SnapshotRegistryError(f"Invalid account key-store entry: {name}")
        for key in ("db_key_source_db_storage_path", "db_key_source_wxid_dir"):
            if entry.get(key):
                protect_source(entry[key])
    for target in targets:
        if any(source == target or source.is_relative_to(target) for source in protected_sources):
            raise SnapshotRegistryError(f"Account deletion would include original source data: {target}")
    # Keep a selectable stable root until every external artifact has gone, so
    # an explicit retry can finish a partially failed filesystem deletion.
    targets.sort(key=lambda path: (path.parent == output / "databases", path == root))
    snapshot_targets = []
    for path in generations:
        info = path.lstat()
        if info.st_ino <= 0:
            raise SnapshotRegistryError(f"Cannot identify snapshot directory for a recoverable deletion: {path}")
        snapshot_targets.append({"path": path.relative_to(output).as_posix(), "account": family,
                                 "device": info.st_dev, "inode": info.st_ino})
    plan = {"version": 1, "account": root.name, "family": family, "snapshot_targets": snapshot_targets,
            "protected_sources": sorted({str(path) for path in protected_sources})}
    return cleanup_names, targets, backups, generations, plan


def _persist_account_delete_plan(account_dir: Path, plan: dict[str, Any]) -> None:
    """Persist validated ownership outside the trees being removed first."""
    import tempfile

    descriptor, temporary_name = tempfile.mkstemp(prefix=".account-delete-", suffix=".json", dir=account_dir)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(plan, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, account_dir / "_account_delete_plan.json")
    finally:
        temporary_path.unlink(missing_ok=True)


def _delete_chat_account_data(account_dir: Path, requested_account_name: str):
    from ..ai.service import get_ai_service
    from ..snapshot_registry import SnapshotRegistryError

    try:
        cleanup_names, targets, backups, generations, plan = _account_delete_targets(account_dir, requested_account_name)
    except SnapshotRegistryError:
        raise
    except (OSError, ValueError) as exc:
        raise SnapshotRegistryError(f"Account deletion preflight failed: {exc}") from exc
    removed: list[str] = []
    purged: list[str] = []
    removed_key_accounts: list[str] = []
    stage = "persist_plan"
    try:
        _persist_account_delete_plan(account_dir, plan)
        stage = "purge_ai"
        for cleanup_name in cleanup_names:
            get_ai_service().purge_account(cleanup_name)
            purged.append(cleanup_name)
        stage = "disconnect"
        stage = "remove_keys"
        removed_key_accounts = remove_account_family_keys_from_store(account_dir.name)
        stage = "remove_files"
        for target in targets:
            shutil.rmtree(target)
            removed.append(str(target))
    except Exception as exc:
        logger.exception("Account deletion failed at %s for %s", stage, account_dir.name)
        raise HTTPException(status_code=500, detail={
            "code": "account_delete_failed", "stage": stage, "message": str(exc),
            "removed_paths": removed, "purged_ai_accounts": purged,
            "removed_key_accounts": removed_key_accounts,
        }) from exc

    return {
        "status": "success", "deleted_account": account_dir.name,
        "removed_key_cache": bool(removed_key_accounts), "removed_key_accounts": removed_key_accounts,
        "removed_backups": [str(path) for path in backups],
        "removed_generations": [str(path) for path in generations],
        "removed_paths": removed,
    }


@router.get("/api/chat/sessions", summary="获取会话列表（聊天左侧列表）")
def list_chat_sessions(
    request: Request,
    account: Optional[str] = None,
    limit: int = 400,
    include_hidden: bool = False,
    include_official: bool = False,
    preview: str = "latest",
    source: Optional[str] = None,
):
    """从 session.db + contact.db 读取会话列表，用于前端聊天界面动态渲染联系人"""
    if limit <= 0:
        raise HTTPException(status_code=400, detail="Invalid limit.")
    if limit > 2000:
        limit = 2000

    account_dir = _resolve_account_dir(account)
    source_requested = _normalize_chat_source(source)
    source_norm = _normalize_chat_source(source_requested)
    contact_db_path = resolve_account_database_dir(account_dir) / "contact.db"
    head_image_db_path = resolve_account_database_dir(account_dir) / "head_image.db"
    base_url = str(request.base_url).rstrip("/")
    _trace_id, trace = create_perf_trace(
        logger,
        "chat.sessions",
        account=account_dir.name,
        source=source_norm or "default",
        limit=int(limit),
        includeHidden=bool(include_hidden),
        includeOfficial=bool(include_official),
        preview=str(preview or ""),
    )
    trace("request:start")

    rows: list[Any]

    session_db_path = resolve_account_database_dir(account_dir) / "session.db"
    sconn = sqlite3.connect(str(session_db_path))
    sconn.row_factory = sqlite3.Row
    try:
        try:
            rows = sconn.execute(
                """
                SELECT
                    username,
                    unread_count,
                    is_hidden,
                    summary,
                    draft,
                    last_timestamp,
                    sort_timestamp,
                    last_msg_locald_id,
                    last_msg_type,
                    last_msg_sub_type,
                    last_msg_sender,
                    last_sender_display_name
                FROM SessionTable
                ORDER BY sort_timestamp DESC
                """
            ).fetchall()
        except sqlite3.OperationalError:
            rows = sconn.execute(
                """
                SELECT
                    username,
                    unread_count,
                    is_hidden,
                    summary,
                    draft,
                    last_timestamp,
                    sort_timestamp,
                    last_msg_type,
                    last_msg_sub_type
                FROM SessionTable
                ORDER BY sort_timestamp DESC
                """
            ).fetchall()
    finally:
        sconn.close()

    trace(
        "rows:loaded",
        rawCount=len(rows or []),

    )

    filtered: list[Any] = []
    for r in rows:
        username = _session_row_get(r, "username", "") or ""
        if not username:
            continue
        if not include_hidden and int((_session_row_get(r, "is_hidden", 0) or 0)) == 1:
            continue
        if not _should_keep_session(username, include_official=include_official):
            continue
        filtered.append(r)

    trace(
        "rows:filtered",
        filteredCount=len(filtered),
    )

    raw_usernames = [str(_session_row_get(r, "username", "") or "").strip() for r in filtered]
    session_last_meta = _load_session_last_message_meta(account_dir, raw_usernames)
    trace(
        "session-last-message:loaded",
        metaCount=len(session_last_meta),
    )

    top_flags = _load_contact_top_flags(contact_db_path, raw_usernames)
    trace(
        "top-flags:loaded",
        usernameCount=len(raw_usernames),
        topCount=sum(1 for value in top_flags.values() if value),
    )

    def _to_int(v: Any) -> int:
        try:
            return int(v or 0)
        except Exception:
            return 0

    def _session_sort_key(row: Any) -> tuple[int, int, int, int]:
        username = str(_session_row_get(row, "username", "") or "").strip()
        sort_ts = _to_int(_session_row_get(row, "sort_timestamp", 0))
        last_ts = _to_int(_session_row_get(row, "last_timestamp", 0))
        latest_meta = session_last_meta.get(username) or {}
        msg_ts = _to_int(latest_meta.get("create_time"))
        msg_sort_seq = _to_int(latest_meta.get("sort_seq"))
        msg_local_id = _to_int(latest_meta.get("local_id"))
        return (
            1 if bool(top_flags.get(username, False)) else 0,
            max(sort_ts, last_ts, msg_ts),
            max(msg_sort_seq, sort_ts, last_ts),
            msg_local_id,
        )

    filtered.sort(key=_session_sort_key, reverse=True)
    if len(filtered) > int(limit):
        filtered = filtered[: int(limit)]

    usernames: list[str] = []
    for r in filtered:
        username = str(_session_row_get(r, "username", "") or "").strip()
        if username:
            usernames.append(username)

    enterprise_groups: set[str] = set()
    group_usernames = [u for u in usernames if u.endswith("@chatroom")]
    if group_usernames:
        quoted_groups = ",".join("'" + u.replace("'", "''") + "'" for u in group_usernames)
        # chat_room_status_ bit 17 marks WeCom interoperability groups.
        group_sql = (
            "SELECT username_ AS username FROM chat_room_info_detail "
            "WHERE (chat_room_status_ & 131072) != 0 "
            f"AND username_ IN ({quoted_groups})"
        )
        try:
            group_rows = []
            if contact_db_path.exists():
                group_conn = sqlite3.connect(str(contact_db_path))
                group_conn.row_factory = sqlite3.Row
                try:
                    group_rows = group_conn.execute(group_sql).fetchall()
                finally:
                    group_conn.close()
            enterprise_groups = {
                str(_session_row_get(row, "username", "") or "") for row in group_rows
            }
        except Exception as exc:
            logger.warning("[sessions] failed to read enterprise group flags: %s", exc)

    contact_rows = _load_contact_rows(contact_db_path, usernames)
    enterprise_contacts = _load_enterprise_contact_info(contact_db_path, usernames)
    local_avatar_usernames = _query_head_image_usernames(head_image_db_path, usernames)
    trace(
        "contacts:loaded",
        usernameCount=len(usernames),
        contactRowCount=len(contact_rows),
        localAvatarCount=len(local_avatar_usernames),
    )

    # Some sessions (notably enterprise groups / openim-related IDs) may be missing from decrypted contact.db.


    trace(
        "contacts:loaded",
        avatarUrlCount=0,
    )

    preview_mode = str(preview or "").strip().lower()
    if preview_mode not in {"latest", "index", "session", "db", "none"}:
        preview_mode = "latest"
    if preview_mode == "index":
        preview_mode = "latest"

    last_previews: dict[str, str] = {}
    # Offline snapshots are read directly: a prior cache must not hide a damaged shard.

    def _is_generic_location_preview(value: Any) -> bool:
        text = re.sub(r"\s+", " ", str(value or "").strip()).strip()
        if not text:
            return False
        lowered = text.lower()
        return lowered in {"[location]", "[位置]"} or lowered.endswith(": [location]") or lowered.endswith(": [位置]")

    if preview_mode in {"latest", "db"}:
        targets = (
            usernames
            if preview_mode == "db"
            else [u for u in usernames if u and ((u not in last_previews) or _is_generic_location_preview(last_previews.get(u)))]
        )
        if targets:
            try:
                legacy = _load_latest_message_previews(account_dir, targets)
            except Exception:
                raise
            for u, v in legacy.items():
                if v:
                    last_previews[u] = v

    group_sender_display_names: dict[str, str] = _build_group_sender_display_name_map(
        contact_db_path,
        last_previews,
    )
    unresolved = []
    for conv_username, preview_text in last_previews.items():
        if not str(conv_username or "").endswith("@chatroom"):
            continue
        sender_username = _extract_group_preview_sender_username(preview_text)
        if sender_username and sender_username not in group_sender_display_names:
            unresolved.append(sender_username)
    unresolved = list(dict.fromkeys(unresolved))

    trace(
        "previews:resolved",
        previewMode=preview_mode,
        previewCount=len(last_previews),
        groupSenderDisplayCount=len(group_sender_display_names),
        unresolvedGroupSenderCount=len(unresolved),
    )

    sessions: list[dict[str, Any]] = []
    for r in filtered:
        username = r["username"]
        latest_meta = session_last_meta.get(str(username or "").strip()) or {}
        latest_meta_ts = _to_int(latest_meta.get("create_time"))
        row_sort_ts = _to_int(_session_row_get(r, "sort_timestamp", 0))
        row_last_ts = _to_int(_session_row_get(r, "last_timestamp", 0))
        latest_meta_preview = str(latest_meta.get("preview") or "").strip()
        c_row = contact_rows.get(username)

        display_name = _pick_display_name(c_row, username)

        # Prefer local head_image avatars when available: decrypted contact.db URLs can be stale

        avatar_url = base_url + _avatar_url_unified(
            account_dir=account_dir,
            username=username,
            local_avatar_usernames=local_avatar_usernames,
        )

        last_message = ""
        if preview_mode == "session":
            draft_text = _decode_sqlite_text(r["draft"]).strip()
            if draft_text:
                draft_text = re.sub(r"\s+", " ", draft_text).strip()
                last_message = f"[草稿] {draft_text}" if draft_text else "[草稿]"
            else:
                summary_text = _decode_sqlite_text(r["summary"]).strip()
                summary_text = re.sub(r"\s+", " ", summary_text).strip()
                if summary_text:
                    last_message = summary_text
                else:
                    last_message = _infer_last_message_brief(r["last_msg_type"], r["last_msg_sub_type"])
        elif preview_mode in {"latest", "db"}:
            if str(last_previews.get(username) or "").strip():
                last_message = str(last_previews.get(username) or "").strip()
            elif preview_mode != "none":
                summary_text = _decode_sqlite_text(r["summary"]).strip()
                summary_text = re.sub(r"\s+", " ", summary_text).strip()
                if summary_text:
                    last_message = summary_text
                else:
                    last_message = _infer_last_message_brief(r["last_msg_type"], r["last_msg_sub_type"])
        elif preview_mode != "none":
            summary_text = _decode_sqlite_text(r["summary"]).strip()
            summary_text = re.sub(r"\s+", " ", summary_text).strip()
            if summary_text:
                last_message = summary_text
            else:
                last_message = _infer_last_message_brief(r["last_msg_type"], r["last_msg_sub_type"])

        # SessionTable can lag behind message_*.db. If the parsed latest-message cache is newer,
        # prefer it for the sidebar preview in session preview mode.
        if (
            preview_mode != "none"
            and latest_meta_preview
            and latest_meta_ts >= max(row_sort_ts, row_last_ts)
            and (latest_meta_ts > max(row_sort_ts, row_last_ts) or not str(last_message or "").strip())
            and not str(last_message or "").startswith("[草稿]")
        ):
            last_message = latest_meta_preview

        # 合并转发聊天记录：左侧会话列表统一显示为 [聊天记录]
        if preview_mode != "none" and not str(last_message or "").startswith("[草稿]"):
            try:
                last_msg_type = int(r["last_msg_type"] or 0)
            except Exception:
                last_msg_type = 0
            try:
                last_msg_sub_type = int(r["last_msg_sub_type"] or 0)
            except Exception:
                last_msg_sub_type = 0
            if last_msg_type == 81604378673 or (last_msg_type == 49 and last_msg_sub_type == 19):
                last_message = "[聊天记录]"
            elif last_msg_type == 48:
                text = re.sub(r"\s+", " ", str(last_message or "").strip()).strip()
                text = re.sub(r"^\[location\]", "", text, flags=re.IGNORECASE).strip()
                text = re.sub(r"^\[位置\]", "", text).strip()
                last_message = f"[位置]{text}" if text else "[位置]"

        last_message = _normalize_session_preview_text(
            last_message,
            is_group=bool(str(username or "").endswith("@chatroom")),
            sender_display_names=group_sender_display_names,
        )
        if str(username or "").endswith("@chatroom") and str(last_message or "") and not str(last_message).startswith("[草稿]"):

            # `last_sender_display_name`, but we may still get a summary that doesn't include "sender:".
            # Also guard against URL schemes like "https://..." being mis-parsed as "https: //...".
            raw_sender_display = ""
            try:
                raw_sender_display = r["last_sender_display_name"]
            except Exception:
                try:
                    raw_sender_display = r.get("last_sender_display_name", "")
                except Exception:
                    raw_sender_display = ""
            sender_display = _decode_sqlite_text(raw_sender_display).strip()
            if sender_display:
                text = re.sub(r"\s+", " ", str(last_message or "").strip()).strip()
                match = re.match(r"^([^:\n]{1,128}):\s*(.+)$", text)
                if match:
                    prefix = str(match.group(1) or "").strip()
                    body = re.sub(r"\s+", " ", str(match.group(2) or "").strip()).strip()
                    if prefix.lower() in {"http", "https"} and body.startswith("//"):
                        last_message = f"{sender_display}: {text}"
                    else:
                        last_message = f"{sender_display}: {body}"
                else:
                    last_message = f"{sender_display}: {text}"

        last_time = _format_session_time(max(row_sort_ts, row_last_ts, latest_meta_ts))

        sessions.append(
            {
                "id": username,
                "username": username,
                "name": display_name,
                "avatar": avatar_url,
                "lastMessage": last_message,
                "lastMessageTime": last_time,
                "unreadCount": int(r["unread_count"] or 0),
                "isGroup": bool(username.endswith("@chatroom")),
                "isEnterpriseGroup": username in enterprise_groups,
                "enterpriseName": enterprise_contacts.get(username, {}).get("enterpriseName", ""),
                "isTop": bool(top_flags.get(str(username or "").strip(), False)),
            }
        )

    trace(
        "response:ready",
        sessionCount=len(sessions),
    )
    return {
        "status": "success",
        "account": account_dir.name,
        "source": source_norm,

        "total": len(sessions),
        "sessions": sessions,
    }


def _resolve_message_self_rowid(
    conn: sqlite3.Connection,
    account_dir: Path,
) -> tuple[Optional[int], str]:
    """Resolve the persisted identity, including account-directory collision suffixes."""

    rowid, matched_username = resolve_account_self_rowid(conn, account_dir)
    if rowid is not None:
        return rowid, matched_username
    source_username = resolve_account_username(account_dir)
    if not source_username:
        return rowid, matched_username
    source_rowid, source_match = resolve_account_self_rowid(
        conn,
        account_dir,
        candidates=(source_username,),
    )
    if source_rowid is not None:
        return source_rowid, source_match
    return rowid, matched_username


def _collect_chat_messages(
    *,
    username: str,
    account_dir: Path,
    db_paths: list[Path],
    resource_conn: Optional[sqlite3.Connection],
    resource_chat_id: Optional[int],
    take: int,
    want_types: Optional[set[str]],
) -> tuple[list[dict[str, Any]], bool, list[str], list[str], set[str]]:
    is_group = bool(username.endswith("@chatroom"))
    self_username = resolve_account_self_username(account_dir)
    take = int(take)
    if take < 0:
        take = 0
    take_probe = take + 1

    merged: list[dict[str, Any]] = []
    sender_usernames: list[str] = []
    quote_usernames: list[str] = []
    pat_usernames: set[str] = set()
    has_more_any = False

    contact_conn: Optional[sqlite3.Connection] = None
    alias_cache: dict[str, str] = {}
    if is_group:
        try:
            contact_db_path = resolve_account_database_dir(account_dir) / "contact.db"
            if contact_db_path.exists():
                contact_conn = sqlite3.connect(str(contact_db_path))
        except Exception:
            contact_conn = None

    for db_path in db_paths:
        conn: Optional[sqlite3.Connection] = None
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            table_name = _resolve_msg_table_name(conn, username)
            if not table_name:
                continue

            my_rowid, _matched_self_username = _resolve_message_self_rowid(
                conn,
                account_dir,
            )
            db_self_username = _matched_self_username or self_username

            quoted_table = _quote_ident(table_name)
            has_packed_info_data = False
            has_msg_source = False
            try:
                cols = conn.execute(f"PRAGMA table_info({quoted_table})").fetchall()
                col_names = {str(c[1] or "").strip().lower() for c in cols}
                has_packed_info_data = "packed_info_data" in col_names
                has_msg_source = "source" in col_names
            except Exception:
                has_packed_info_data = False
                has_msg_source = False

            packed_select = (
                "m.packed_info_data AS packed_info_data, " if has_packed_info_data else "NULL AS packed_info_data, "
            )
            source_select = "m.source AS msg_source, " if has_msg_source else "NULL AS msg_source, "
            sql_with_join = (
                "SELECT "
                "m.local_id, m.server_id, m.local_type, m.sort_seq, m.real_sender_id, m.create_time, "
                "m.message_content, m.compress_content, "
                + packed_select
                + source_select
                + "n.user_name AS sender_username "
                f"FROM {quoted_table} m "
                "LEFT JOIN Name2Id n ON m.real_sender_id = n.rowid "
                "ORDER BY m.create_time DESC, m.sort_seq DESC, m.local_id DESC "
                "LIMIT ?"
            )
            sql_no_join = (
                "SELECT "
                "m.local_id, m.server_id, m.local_type, m.sort_seq, m.real_sender_id, m.create_time, "
                "m.message_content, m.compress_content, "
                + packed_select
                + source_select
                + "'' AS sender_username "
                f"FROM {quoted_table} m "
                "ORDER BY m.create_time DESC, m.sort_seq DESC, m.local_id DESC "
                "LIMIT ?"
            )

            # Force sqlite3 to return TEXT as raw bytes for this query, so we can zstd-decompress
            # compress_content reliably.
            conn.text_factory = bytes

            try:
                rows = conn.execute(sql_with_join, (take_probe,)).fetchall()
            except Exception:
                raise
            if len(rows) > take:
                has_more_any = True
                rows = rows[:take]

            for r in rows:
                local_id = int(r["local_id"] or 0)
                create_time = int(r["create_time"] or 0)
                sort_seq = int(r["sort_seq"] or 0) if r["sort_seq"] is not None else 0
                local_type = int(r["local_type"] or 0)
                native_voice_transcript = (
                    _extract_voice_transcript_from_packed_info(_row_get_value(r, "packed_info_data"))
                    if local_type == 34
                    else ""
                )
                sender_username = _decode_sqlite_text(r["sender_username"]).strip()
                raw_text = _decode_message_content(r["compress_content"], r["message_content"]).strip()
                # Native pat/video records may store their actor in XML rather than Name2Id.
                sender_from_xml = local_type in (266287972401, 43) and not sender_username
                if sender_from_xml:
                    sender_username = _extract_sender_from_group_xml(raw_text, local_type=local_type)

                if local_type != 10000 and (not sender_username):
                    raise sqlite3.DatabaseError(
                        f"Missing Name2Id sender mapping for local_id={local_id}, "
                        f"real_sender_id={r['real_sender_id']}."
                    )

                is_sent = False
                if sender_from_xml or local_type == 266287972401:
                    is_sent = sender_username == db_self_username
                elif my_rowid is not None:
                    try:
                        is_sent = int(r["real_sender_id"] or 0) == int(my_rowid)
                    except Exception:
                        is_sent = False

                at_usernames = _extract_at_usernames_from_source(_row_get_value(r, "msg_source", "source"))

                sender_prefix = ""
                if is_group and raw_text and (not raw_text.startswith("<")) and (not raw_text.startswith('"<')):
                    sender_alias = ""
                    sep = raw_text.find(":\n")
                    if sep > 0:
                        prefix = raw_text[:sep].strip()
                        if prefix and sender_username and prefix != sender_username:
                            strong_hint = prefix.startswith("wxid_") or prefix.endswith("@chatroom") or "@" in prefix
                            if not strong_hint:
                                body_probe = raw_text[sep + 2 :].lstrip("\n").lstrip()
                                body_is_xml = body_probe.startswith("<") or body_probe.startswith('"<')
                                if not body_is_xml:
                                    sender_alias = _lookup_contact_alias(contact_conn, alias_cache, sender_username)
                    sender_prefix, raw_text = _split_group_sender_prefix(raw_text, sender_username, sender_alias)

                if is_group and sender_prefix and (not sender_username):
                    sender_username = sender_prefix

                if is_group and (not sender_username) and (raw_text.startswith("<") or raw_text.startswith('"<')):
                    xml_sender = _extract_sender_from_group_xml(raw_text)
                    if xml_sender:
                        sender_username = xml_sender

                if is_sent:
                    sender_username = db_self_username
                elif (not is_group) and (not sender_username):
                    sender_username = username

                render_type = "text"
                content_text = raw_text
                title = ""
                url = ""
                from_name = ""
                from_username = ""
                record_item = ""
                image_md5 = ""
                emoji_md5 = ""
                emoji_url = ""
                thumb_url = ""
                image_url = ""
                image_file_id = ""
                image_group_type = ""
                image_group_id = ""
                image_group_count = 0
                video_md5 = ""
                video_thumb_md5 = ""
                video_file_id = ""
                video_thumb_file_id = ""
                video_url = ""
                video_thumb_url = ""
                voice_length = ""
                quote_username = ""
                quote_title = ""
                quote_content = ""
                quote_thumb_url = ""
                link_type = ""
                link_style = ""
                object_id = ""
                object_nonce_id = ""
                quote_server_id = ""
                quote_type = ""
                quote_voice_length = ""
                amount = ""
                cover_url = ""
                file_size = ""
                pay_sub_type = ""
                transfer_status = ""
                file_md5 = ""
                transfer_id = ""
                voip_type = ""
                location_lat: Optional[float] = None
                location_lng: Optional[float] = None
                location_poiname = ""
                location_label = ""

                revoked_server_id = ""
                revoked_local_id = 0
                message_revoke_time = 0
                revoke_original_status = ""
                if local_type == 10000:
                    render_type = "system"
                    content_text = _parse_system_message_content(raw_text)
                    if "revokemsg" in raw_text.lower():
                        try:
                            parsed_rev = parse_revoke_xml(raw_text)
                            if parsed_rev:
                                revoked_server_id = str(parsed_rev.get("newmsgid") or "")
                                message_revoke_time = int(parsed_rev.get("revoketime") or create_time or 0)
                                revoked = process_revocation_event(
                                    account_dir=account_dir,
                                    account_name=account_dir.name,
                                    username=username,
                                    xml_text=raw_text,
                                    revoke_time=create_time or int(time.time()),
                                    server_id=str(r["server_id"] or "0"),
                                    local_id=local_id,
                                    source_db=db_path.stem,
                                    source_table=table_name,
                                )
                                revoke_original_status = "captured" if revoked is not None else "unresolved"
                                if revoked is not None:
                                    revoked_server_id = str(revoked["server_id"] or "")
                                    revoked_local_id = int(revoked["local_id"] or 0)
                                    message_revoke_time = int(revoked["revoke_time"])
                        except Exception as e:
                            logger.exception("[anti_revoke] process_revocation_event failed account=%s user=%s local_id=%s: %s", account_dir.name, username, local_id, e)
                            raise
                elif local_type == 49:
                    parsed = _parse_app_message(raw_text)
                    render_type = str(parsed.get("renderType") or "text")
                    content_text = str(parsed.get("content") or "")
                    title = str(parsed.get("title") or "")
                    url = str(parsed.get("url") or "")
                    from_name = str(parsed.get("from") or "")
                    from_username = str(parsed.get("fromUsername") or "")
                    record_item = str(parsed.get("recordItem") or "")
                    quote_title = str(parsed.get("quoteTitle") or "")
                    quote_content = str(parsed.get("quoteContent") or "")
                    quote_thumb_url = str(parsed.get("quoteThumbUrl") or "")
                    link_type = str(parsed.get("linkType") or "")
                    link_style = str(parsed.get("linkStyle") or "")
                    object_id = str(parsed.get("objectId") or "")
                    object_nonce_id = str(parsed.get("objectNonceId") or "")
                    quote_username = str(parsed.get("quoteUsername") or "")
                    quote_server_id = str(parsed.get("quoteServerId") or "")
                    quote_type = str(parsed.get("quoteType") or "")
                    quote_voice_length = str(parsed.get("quoteVoiceLength") or "")
                    amount = str(parsed.get("amount") or "")
                    cover_url = str(parsed.get("coverUrl") or "")
                    thumb_url = str(parsed.get("thumbUrl") or "")
                    file_size = str(parsed.get("size") or "")
                    pay_sub_type = str(parsed.get("paySubType") or "")
                    file_md5 = str(parsed.get("fileMd5") or "")
                    transfer_id = str(parsed.get("transferId") or "")

                    if render_type == "transfer":
                        # 直接从原始 XML 提取 transferid（可能在 wcpayinfo 内）
                        if not transfer_id:
                            transfer_id = _extract_xml_tag_or_attr(raw_text, "transferid") or ""
                        transfer_status = _infer_transfer_status_text(
                            is_sent=is_sent,
                            paysubtype=pay_sub_type,
                            receivestatus=str(parsed.get("receiveStatus") or ""),
                            sendertitle=str(parsed.get("senderTitle") or ""),
                            receivertitle=str(parsed.get("receiverTitle") or ""),
                            senderdes=str(parsed.get("senderDes") or ""),
                            receiverdes=str(parsed.get("receiverDes") or ""),
                        )
                        if not content_text:
                            content_text = transfer_status or "转账"
                elif local_type == 266287972401:
                    render_type = "system"
                    template = _extract_xml_tag_text(raw_text, "template")
                    if template:
                        # import re
                        pat_usernames.update({m.group(1) for m in re.finditer(r"\$\{([^}]+)\}", template) if m.group(1)})
                        content_text = "[拍一拍]"
                    else:
                        content_text = "[拍一拍]"
                elif local_type == 244813135921:
                    render_type = "quote"
                    parsed = _parse_app_message(raw_text)
                    content_text = str(parsed.get("content") or "[引用消息]")
                    quote_title = str(parsed.get("quoteTitle") or "")
                    quote_content = str(parsed.get("quoteContent") or "")
                    quote_thumb_url = str(parsed.get("quoteThumbUrl") or "")
                    link_type = str(parsed.get("linkType") or "")
                    link_style = str(parsed.get("linkStyle") or "")
                    quote_username = str(parsed.get("quoteUsername") or "")
                    quote_server_id = str(parsed.get("quoteServerId") or "")
                    quote_type = str(parsed.get("quoteType") or "")
                    quote_voice_length = str(parsed.get("quoteVoiceLength") or "")
                elif local_type == 3:
                    render_type = "image"
                    image_group_info = _extract_image_group_info(raw_text)
                    image_group_type = str(image_group_info.get("type") or "")
                    image_group_id = str(image_group_info.get("id") or "")
                    image_group_count = int(image_group_info.get("count") or 0)
                    xml_md5s = _extract_image_md5_candidates(raw_text)
                    image_md5 = xml_md5s[0] if xml_md5s else ''

                    # Prefer message_resource.db md5 for local files: XML md5 frequently differs from the on-disk *.dat basename
                    # (especially for *_t.dat thumbnails), causing the media endpoint to 404.
                    if resource_conn is not None:
                        try:
                            resource_md5 = _lookup_resource_md5(
                                resource_conn,
                                resource_chat_id,
                                message_local_type=local_type,
                                server_id=int(r["server_id"] or 0),
                                local_id=local_id,
                                create_time=create_time,
                            )
                        except Exception:
                            resource_md5 = ""
                        resource_md5 = str(resource_md5 or "").strip().lower()
                        if len(resource_md5) == 32 and all(c in "0123456789abcdef" for c in resource_md5):
                            image_md5 = resource_md5

                    packed_md5 = _extract_md5_from_packed_info(r["packed_info_data"])
                    if packed_md5:
                        image_md5 = packed_md5

                    image_url, image_file_id, _ = _extract_image_cdn_refs(raw_text)
                    content_text = "[图片]"
                elif local_type == 34:
                    render_type = "voice"
                    duration = _extract_xml_attr(raw_text, "voicelength")
                    voice_length = duration
                    content_text = f"[语音 {duration}秒]" if duration else "[语音]"
                elif local_type == 43 or local_type == 62:
                    render_type = "video"
                    video_md5 = _extract_xml_attr(raw_text, "md5")
                    video_thumb_md5 = _extract_xml_attr(raw_text, "cdnthumbmd5")
                    video_thumb_url_or_id = _extract_xml_attr(raw_text, "cdnthumburl") or _extract_xml_tag_text(
                        raw_text, "cdnthumburl"
                    )
                    video_url_or_id = _extract_xml_attr(raw_text, "cdnvideourl") or _extract_xml_tag_text(
                        raw_text, "cdnvideourl"
                    )

                    video_thumb_url = (
                        video_thumb_url_or_id
                        if str(video_thumb_url_or_id or "").strip().lower().startswith(("http://", "https://"))
                        else ""
                    )
                    video_url = (
                        video_url_or_id
                        if str(video_url_or_id or "").strip().lower().startswith(("http://", "https://"))
                        else ""
                    )
                    video_thumb_file_id = "" if video_thumb_url else (str(video_thumb_url_or_id or "").strip() or "")
                    video_file_id = "" if video_url else (str(video_url_or_id or "").strip() or "")
                    if (not video_thumb_md5) and resource_conn is not None:
                        video_thumb_md5 = _lookup_resource_md5(
                            resource_conn,
                            resource_chat_id,
                            message_local_type=local_type,
                            server_id=int(r["server_id"] or 0),
                            local_id=local_id,
                            create_time=create_time,
                        )

                    if not _is_hex_md5(video_thumb_md5):
                        packed_md5 = _extract_md5_from_packed_info(r["packed_info_data"])
                        if packed_md5:
                            video_thumb_md5 = packed_md5
                    # Match WeFlow video lookup: packed_info_data may be the local msg/video basename.
                    # Keep XML md5/file_id as fallback, but prefer the packed token for local playback.
                    try:
                        packed_val = r["packed_info_data"]
                    except Exception:
                        try:
                            packed_val = r.get("packed_info_data")  # type: ignore[attr-defined]
                        except Exception:
                            packed_val = None
                    packed_video_token = _extract_md5_from_packed_info(packed_val)
                    if packed_video_token:
                        video_md5 = packed_video_token
                        if not _is_hex_md5(video_thumb_md5):
                            video_thumb_md5 = packed_video_token
                            video_thumb_file_id = ""
                    content_text = "[视频]"
                elif local_type == 47:
                    render_type = "emoji"
                    emoji_md5 = _extract_xml_attr(raw_text, "md5")
                    if not emoji_md5:
                        emoji_md5 = _extract_xml_tag_text(raw_text, "md5")
                    emoji_url = _extract_xml_attr(raw_text, "cdnurl")
                    if not emoji_url:
                        emoji_url = _extract_xml_tag_text(raw_text, "cdn_url")
                    if (not emoji_md5) and resource_conn is not None:
                        emoji_md5 = _lookup_resource_md5(
                            resource_conn,
                            resource_chat_id,
                            message_local_type=local_type,
                            server_id=int(r["server_id"] or 0),
                            local_id=local_id,
                            create_time=create_time,
                        )
                    content_text = "[表情]"
                elif local_type == 48:
                    parsed = _parse_location_message(raw_text)
                    render_type = str(parsed.get("renderType") or "location")
                    content_text = str(parsed.get("content") or "[Location]")
                    location_lat = parsed.get("locationLat")
                    location_lng = parsed.get("locationLng")
                    location_poiname = str(parsed.get("locationPoiname") or "")
                    location_label = str(parsed.get("locationLabel") or "")
                elif local_type == 50:
                    render_type = "voip"
                    try:
                        # import re
                        block = raw_text
                        m_voip = re.search(
                            r"(<VoIPBubbleMsg[^>]*>.*?</VoIPBubbleMsg>)",
                            raw_text,
                            flags=re.IGNORECASE | re.DOTALL,
                        )
                        if m_voip:
                            block = m_voip.group(1) or raw_text
                        room_type = str(_extract_xml_tag_text(block, "room_type") or "").strip()
                        if room_type == "0":
                            voip_type = "video"
                        elif room_type == "1":
                            voip_type = "audio"

                        voip_msg = str(_extract_xml_tag_text(block, "msg") or "").strip()
                        content_text = voip_msg or "通话"
                    except Exception:
                        content_text = "通话"
                elif local_type != 1:
                    if not content_text:
                        content_text = _infer_message_brief_by_local_type(local_type)
                    else:
                        if content_text.startswith("<") or content_text.startswith('"<'):
                            parsed_special = False
                            if "<appmsg" in content_text.lower():
                                parsed = _parse_app_message(content_text)
                                rt = str(parsed.get("renderType") or "")
                                if rt and rt != "text":
                                    parsed_special = True
                                    render_type = rt
                                    content_text = str(parsed.get("content") or content_text)
                                    title = str(parsed.get("title") or title)
                                    url = str(parsed.get("url") or url)
                                    from_name = str(parsed.get("from") or from_name)
                                    from_username = str(parsed.get("fromUsername") or from_username)
                                    record_item = str(parsed.get("recordItem") or record_item)
                                    quote_title = str(parsed.get("quoteTitle") or quote_title)
                                    quote_content = str(parsed.get("quoteContent") or quote_content)
                                    quote_thumb_url = str(parsed.get("quoteThumbUrl") or quote_thumb_url)
                                    link_type = str(parsed.get("linkType") or link_type)
                                    link_style = str(parsed.get("linkStyle") or link_style)
                                    object_id = str(parsed.get("objectId") or object_id)
                                    object_nonce_id = str(parsed.get("objectNonceId") or object_nonce_id)
                                    amount = str(parsed.get("amount") or amount)
                                    cover_url = str(parsed.get("coverUrl") or cover_url)
                                    thumb_url = str(parsed.get("thumbUrl") or thumb_url)
                                    file_size = str(parsed.get("size") or file_size)
                                    pay_sub_type = str(parsed.get("paySubType") or pay_sub_type)
                                    file_md5 = str(parsed.get("fileMd5") or file_md5)
                                    transfer_id = str(parsed.get("transferId") or transfer_id)
                                    quote_username = str(parsed.get("quoteUsername") or quote_username)
                                    quote_server_id = str(parsed.get("quoteServerId") or quote_server_id)
                                    quote_type = str(parsed.get("quoteType") or quote_type)
                                    quote_voice_length = str(parsed.get("quoteVoiceLength") or quote_voice_length)

                                    if render_type == "transfer":
                                        # 如果 transferId 仍为空，尝试从原始 XML 提取
                                        if not transfer_id:
                                            transfer_id = _extract_xml_tag_or_attr(content_text, "transferid") or ""
                                        transfer_status = _infer_transfer_status_text(
                                            is_sent=is_sent,
                                            paysubtype=pay_sub_type,
                                            receivestatus=str(parsed.get("receiveStatus") or ""),
                                            sendertitle=str(parsed.get("senderTitle") or ""),
                                            receivertitle=str(parsed.get("receiverTitle") or ""),
                                            senderdes=str(parsed.get("senderDes") or ""),
                                            receiverdes=str(parsed.get("receiverDes") or ""),
                                        )
                                        if not content_text:
                                            content_text = transfer_status or "转账"

                            if not parsed_special:
                                t = _extract_xml_tag_text(content_text, "title")
                                d = _extract_xml_tag_text(content_text, "des")
                                content_text = t or d or _infer_message_brief_by_local_type(local_type)

                if not content_text:
                    content_text = _infer_message_brief_by_local_type(local_type)

                if want_types is not None:
                    rt_key = _normalize_render_type_key(render_type)
                    if rt_key not in want_types:
                        continue

                if sender_username:
                    sender_usernames.append(sender_username)
                if quote_username:
                    quote_usernames.append(str(quote_username).strip())

                merged.append(
                    {
                        "id": f"{db_path.stem}:{table_name}:{local_id}",
                        "localId": local_id,
                        "serverId": int(r["server_id"] or 0),
                        "serverIdStr": str(int(r["server_id"] or 0)) if int(r["server_id"] or 0) else "",
                        "type": local_type,
                        "createTime": create_time,
                        "sortSeq": sort_seq,
                        "senderUsername": sender_username,
                        "isSent": bool(is_sent),
                        "renderType": render_type,
                        "content": content_text,
                        "atUsernames": at_usernames,
                        "atUsers": [],
                        "title": title,
                        "url": url,
                        "linkType": link_type,
                        "linkStyle": link_style,
                        "objectId": object_id,
                        "objectNonceId": object_nonce_id,
                        "from": from_name,
                        "fromUsername": from_username,
                        "recordItem": record_item,
                        "imageMd5": image_md5,
                        "imageFileId": image_file_id,
                        "imageGroupType": image_group_type,
                        "imageGroupId": image_group_id,
                        "imageGroupCount": image_group_count,
                        "emojiMd5": emoji_md5,
                        "emojiUrl": emoji_url,
                        "thumbUrl": thumb_url,
                        "imageUrl": image_url,
                        "videoMd5": video_md5,
                        "videoThumbMd5": video_thumb_md5,
                        "videoFileId": video_file_id,
                        "videoThumbFileId": video_thumb_file_id,
                        "videoUrl": video_url,
                        "videoThumbUrl": video_thumb_url,
                        "voiceLength": voice_length,
                        "voiceTranscript": native_voice_transcript,
                        "voiceTranscriptStatus": "success" if native_voice_transcript else "idle",
                        "voiceTranscriptError": "",
                        "voiceTranscriptLanguage": "",
                        "voiceTranscriptModel": "wechat-native" if native_voice_transcript else "",
                        "voipType": voip_type,
                        "quoteUsername": str(quote_username).strip(),
                        "quoteServerId": str(quote_server_id).strip(),
                        "quoteType": str(quote_type).strip(),
                        "quoteVoiceLength": str(quote_voice_length).strip(),
                        "quoteTitle": quote_title,
                        "quoteContent": quote_content,
                        "quoteThumbUrl": quote_thumb_url,
                        "amount": amount,
                        "coverUrl": cover_url,
                        "fileSize": file_size,
                        "fileMd5": file_md5,
                        "paySubType": pay_sub_type,
                        "transferStatus": transfer_status,
                        "transferId": transfer_id,
                        "locationLat": location_lat,
                        "locationLng": location_lng,
                        "locationPoiname": location_poiname,
                        "locationLabel": location_label,
                        "revokedServerId": revoked_server_id,
                        "revokedLocalId": revoked_local_id,
                        "revokeTime": message_revoke_time,
                        "revokeOriginalStatus": revoke_original_status,
                        "_rawText": raw_text if local_type in (10000, 266287972401) else "",
                    }
                )
        except sqlite3.DatabaseError as e:
            if contact_conn is not None:
                contact_conn.close()
            raise sqlite3.DatabaseError(f"Cannot read offline message database {db_path}: {e}") from e
            # 单个解密库损坏时不要让整个聊天详情接口 500；保留诊断日志，继续尝试其他 message_*.db。
        finally:
            if conn is not None:
                conn.close()

    if contact_conn is not None:
        try:
            contact_conn.close()
        except Exception:
            pass

    return merged, has_more_any, sender_usernames, quote_usernames, pat_usernames
















def _parse_message_anchor_local_id(anchor_id: str) -> tuple[str, str, int]:
    parts = str(anchor_id or "").split(":", 2)
    if len(parts) != 3:
        raise HTTPException(status_code=400, detail="Invalid anchor_id.")
    db_stem = str(parts[0] or "").strip()
    table_name = str(parts[1] or "").strip()
    try:
        local_id = int(parts[2])
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid anchor_id.")
    if not db_stem or local_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid anchor_id.")
    return db_stem, table_name, local_id


@router.get("/api/chat/messages/daily_counts", summary="获取某月每日消息数（热力图）")
def get_chat_message_daily_counts(
    username: str,
    year: int,
    month: int,
    account: Optional[str] = None,
    source: Optional[str] = None,
):
    username = str(username or "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="Missing username.")
    try:
        y = int(year)
        m = int(month)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid year or month.")
    if m < 1 or m > 12:
        raise HTTPException(status_code=400, detail="Invalid month.")

    try:
        start_ts, end_ts = _local_month_range_epoch_seconds(year=y, month=m)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid year or month.")

    _, log_perf = create_perf_trace(logger, "chat.daily_counts", account=account, year=y, month=m)
    metrics: dict[str, Any] = {"stage": "connect", "lockWaitMs": 0.0, "discoveryMs": 0.0, "aggregateMs": 0.0}
    log_perf("request:start")
    try:
        account_dir = _resolve_account_dir(account)
        source_requested = _normalize_chat_source(source)
        source_norm = _normalize_chat_source(source_requested)
        log_perf("source:resolved", source=source_norm)
        counts: dict[str, int] = {}
        metrics["stage"] = "discovery"
        discovery_started = time.perf_counter()
        db_paths = _iter_message_db_paths(account_dir)
        metrics["discoveryMs"] += (time.perf_counter() - discovery_started) * 1000
        metrics["candidateDatabases"] = len(db_paths)
        for db_path in db_paths:
            metrics["stage"] = "discovery"
            discovery_started = time.perf_counter()
            # 只读打开，分库丢失时不能创建空库并返回错误的零计数。
            conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                try:
                    table_name = _resolve_msg_table_name(conn, username)
                    if not table_name:
                        continue
                    quoted_table = _quote_ident(table_name)
                    columns = conn.execute(f"PRAGMA table_info({quoted_table})").fetchall()
                    time_type = next((str(col[2]).upper() for col in columns if str(col[1]).lower() == "create_time"), "")
                    time_expr = "create_time" if "INT" in time_type else "CAST(create_time AS INTEGER)"
                finally:
                    metrics["discoveryMs"] += (time.perf_counter() - discovery_started) * 1000
                metrics["stage"] = "aggregate"
                aggregate_started = time.perf_counter()
                try:
                    rows = conn.execute(
                        f"SELECT strftime('%Y-%m-%d', {time_expr}, 'unixepoch', 'localtime') AS day, "
                        f"COUNT(*) AS c FROM {quoted_table} "
                        f"WHERE {time_expr} >= ? AND {time_expr} < ? GROUP BY day",
                        (int(start_ts), int(end_ts)),
                    ).fetchall()
                    metrics["returnedRows"] = metrics.get("returnedRows", 0) + len(rows)
                    for day, count in rows:
                        if not day or int(count) <= 0:
                            raise ValueError("Invalid daily count")
                        counts[str(day)] = counts.get(str(day), 0) + int(count)
                finally:
                    metrics["aggregateMs"] += (time.perf_counter() - aggregate_started) * 1000
            finally:
                conn.close()
        metrics["stage"] = "complete"
        log_perf("response:ready", **metrics, source=source_norm, activeDays=len(counts))
    except HTTPException:
        log_perf("request:failed", **metrics)
        raise
    except Exception as exc:
        # 不记录原生异常文本，避免底层查询内容进入日历日志。
        log_perf("request:failed", **metrics, errorType=type(exc).__name__)
        raise HTTPException(status_code=503, detail="无法完整加载日历，请稍后重试") from exc

    total = int(sum(int(v) for v in counts.values())) if counts else 0
    max_count = int(max(counts.values())) if counts else 0

    return {
        "status": "success",
        "account": account_dir.name,
        "username": username,
        "source": source_norm,
        **({}),

        "year": int(y),
        "month": int(m),
        "counts": counts,
        "total": total,
        "max": max_count,
    }


@router.get("/api/chat/messages/anchor", summary="获取定位锚点（某日第一条/会话顶部）")
def get_chat_message_anchor(
    username: str,
    kind: str,
    account: Optional[str] = None,
    date: Optional[str] = None,
    source: Optional[str] = None,
):
    username = str(username or "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="Missing username.")

    kind_norm = str(kind or "").strip().lower()
    if kind_norm not in {"day", "first"}:
        raise HTTPException(status_code=400, detail="Invalid kind.")

    date_norm: Optional[str] = None
    start_ts: Optional[int] = None
    end_ts: Optional[int] = None
    if kind_norm == "day":
        if not date:
            raise HTTPException(status_code=400, detail="Missing date.")
        try:
            start_ts, end_ts, date_norm = _local_day_range_epoch_seconds(date_str=str(date))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid date.")

    account_dir = _resolve_account_dir(account)
    source_requested = _normalize_chat_source(source)
    source_norm = _normalize_chat_source(source_requested)


    db_paths = _iter_message_db_paths(account_dir)

    best_key: Optional[tuple[int, int, int]] = None
    best_anchor_id = ""
    best_create_time = 0

    for db_path in db_paths:
        conn = sqlite3.connect(str(db_path))
        try:
            try:
                table_name = _resolve_msg_table_name(conn, username)
                if not table_name:
                    continue
                quoted_table = _quote_ident(table_name)

                if kind_norm == "first":
                    row = conn.execute(
                        "SELECT local_id, CAST(create_time AS INTEGER) AS create_time, "
                        "COALESCE(CAST(sort_seq AS INTEGER), 0) AS sort_seq "
                        f"FROM {quoted_table} "
                        "ORDER BY CAST(create_time AS INTEGER) ASC, COALESCE(CAST(sort_seq AS INTEGER), 0) ASC, local_id ASC "
                        "LIMIT 1"
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT local_id, CAST(create_time AS INTEGER) AS create_time, "
                        "COALESCE(CAST(sort_seq AS INTEGER), 0) AS sort_seq "
                        f"FROM {quoted_table} "
                        "WHERE CAST(create_time AS INTEGER) >= ? AND CAST(create_time AS INTEGER) < ? "
                        "ORDER BY CAST(create_time AS INTEGER) ASC, COALESCE(CAST(sort_seq AS INTEGER), 0) ASC, local_id ASC "
                        "LIMIT 1",
                        (int(start_ts or 0), int(end_ts or 0)),
                    ).fetchone()

                if not row:
                    continue
                try:
                    local_id = int(row[0] or 0)
                    create_time = int(row[1] or 0)
                    sort_seq = int(row[2] or 0)
                except Exception:
                    continue
                if local_id <= 0:
                    continue

                key = (int(create_time), int(sort_seq), int(local_id))
                if (best_key is None) or (key < best_key):
                    best_key = key
                    best_create_time = int(create_time)
                    best_anchor_id = f"{db_path.stem}:{table_name}:{local_id}"
            except Exception:
                continue
        finally:
            conn.close()

    if not best_anchor_id:
        return {
            "status": "empty",
            "anchorId": "",
            "source": source_norm,

        }

    resp: dict[str, Any] = {
        "status": "success",
        "account": account_dir.name,
        "username": username,
        "source": source_norm,

        "kind": kind_norm,
        "anchorId": best_anchor_id,
        "createTime": int(best_create_time),
    }
    if date_norm is not None:
        resp["date"] = date_norm
    return resp


def _merge_anti_revoke_into_messages(
    merged: list[dict[str, Any]],
    *,
    account_dir: Path,
    username: str,
    want_types: Optional[set[str]] = None,
) -> None:
    try:
        revoked_map = get_revoked_messages_map(account_dir, username)
        revoked_list = get_revoked_messages_list(account_dir, username)
    except Exception as e:
        logger.exception(
            "[anti_revoke] Failed to read revoked messages account=%s user=%s: %s",
            account_dir.name,
            username,
            e,
        )
        raise

    if not revoked_map and not revoked_list:
        return

    seen_non_system_servers: set[str] = set()
    seen_non_system_locals: set[str] = set()

    for m in merged:
        sid = str(m.get("serverIdStr") or m.get("serverId") or "").strip()
        lid = int(m.get("localId") or 0)
        source_db, source_table = str(m.get("db") or ""), str(m.get("table") or "")
        local_identity = f"{source_db}:{source_table}:{lid}" if source_db or source_table else str(lid)
        m_type = int(m.get("type") or 0)
        is_system = (m_type == 10000 or m.get("renderType") == "system")

        if not is_system:
            if sid and sid != "0":
                seen_non_system_servers.add(sid)
            if lid > 0 and sid in ("", "0"):
                seen_non_system_locals.add(local_identity)

            match = None
            if sid and sid != "0":
                match = revoked_map.get(f"server:{sid}")
            elif lid > 0:
                local_match = revoked_map.get(f"local:{local_identity}")
                if local_match is not None:
                    match_sid = str(local_match.get("server_id") or "").strip()
                    if match_sid in ("", "0"):
                        match = local_match

            if match is not None:
                m["isRevoked"] = True
                m["revokeTime"] = int(match.get("revoke_time") or m.get("createTime") or 0)

    for rev_row in revoked_list:
        rsid = str(rev_row.get("server_id") or "").strip()
        rlid = int(rev_row.get("local_id") or 0)
        source_db, source_table = str(rev_row.get("source_db") or ""), str(rev_row.get("source_table") or "")
        local_identity = f"{source_db}:{source_table}:{rlid}" if source_db and source_table else str(rlid)
        if rsid and rsid != "0":
            already_present = rsid in seen_non_system_servers
        else:
            already_present = rlid > 0 and local_identity in seen_non_system_locals
        if already_present:
            continue

        chat_item = format_revoked_message_as_chat_item(rev_row, account_dir)
        if not chat_item:
            continue
        if want_types is not None:
            rt_key = _normalize_render_type_key(chat_item.get("renderType"))
            if rt_key not in want_types:
                continue

        merged.append(chat_item)
        if rsid and rsid != "0":
            seen_non_system_servers.add(rsid)
        if rlid > 0 and rsid in ("", "0"):
            seen_non_system_locals.add(local_identity)


@router.get("/api/chat/messages", summary="获取会话消息列表")
def list_chat_messages(
    request: Request,
    username: str,
    account: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    order: str = "asc",
    render_types: Optional[str] = None,
    filter_mode: Optional[str] = None,
    scan_offset: int = 0,
    scan_limit: int = 320,
    source: Optional[str] = None,
):
    handler_started_perf = time.perf_counter()
    handler_started_epoch_ms = time.time_ns() / 1_000_000
    request_perf = get_request_perf_context(request)

    if not username:
        raise HTTPException(status_code=400, detail="Missing username.")
    if limit <= 0:
        raise HTTPException(status_code=400, detail="Invalid limit.")
    if limit > 500:
        limit = 500
    if offset < 0:
        offset = 0
    if scan_offset < 0:
        scan_offset = 0
    if scan_limit <= 0:
        scan_limit = 320
    if scan_limit < 50:
        scan_limit = 50
    if scan_limit > 2000:
        scan_limit = 2000

    account_dir = _resolve_account_dir(account)
    database_dir = resolve_account_database_dir(account_dir)
    snapshot_generation = database_dir.parents[1].name if database_dir != account_dir else "legacy"
    source_requested = _normalize_chat_source(source)
    source_norm = _normalize_chat_source(source_requested)
    contact_db_path = resolve_account_database_dir(account_dir) / "contact.db"
    head_image_db_path = resolve_account_database_dir(account_dir) / "head_image.db"
    message_resource_db_path = resolve_account_database_dir(account_dir) / "message_resource.db"
    base_url = str(request.base_url).rstrip("/")
    request_perf_fields: dict[str, Any] = {}
    asgi_arrived_perf = request_perf.get("asgiArrivedPerf")
    if isinstance(asgi_arrived_perf, (int, float)):
        request_perf_fields = {
            "clientTraceId": request_perf.get("clientTraceId"),
            "clientSentEpochMs": request_perf.get("clientSentEpochMs"),
            "asgiArrivedEpochMs": request_perf.get("asgiArrivedEpochMs"),
            "handlerStartedEpochMs": round(handler_started_epoch_ms, 3),
            "asgiQueueMs": round(
                (handler_started_perf - float(asgi_arrived_perf)) * 1000.0,
                1,
            ),
        }
    request_trace_options = (
        {
            "trace_id": str(request_perf.get("traceId") or "") or None,
            "started_at": handler_started_perf,
        }
        if request_perf
        else {}
    )
    _trace_id, trace = create_perf_trace(
        logger,
        "chat.messages",
        **request_trace_options,
        account=account_dir.name,
        username=username,
        source=source_norm or "default",
        limit=int(limit),
        offset=int(offset),
        order=str(order or ""),
        renderTypes=str(render_types or ""),
        filterMode=str(filter_mode or ""),
        scanOffset=int(scan_offset),
        scanLimit=int(scan_limit),
        **request_perf_fields,
    )
    trace("request:start")

    db_paths: list[Path] = []
    db_paths = _iter_message_db_paths(account_dir)
    if not db_paths:
        trace("response:error", reason="no-message-dbs")
        return {
            "snapshotGeneration": snapshot_generation,
            "status": "error",
            "account": account_dir.name,
            "username": username,
            "source": source_norm,
            "total": 0,
            "messages": [],
            "message": "No message databases found for this account.",
        }

    resource_conn: Optional[sqlite3.Connection] = None
    resource_chat_id: Optional[int] = None
    try:
        if message_resource_db_path.exists():
            resource_conn = sqlite3.connect(str(message_resource_db_path))
            resource_conn.row_factory = sqlite3.Row
            resource_chat_id = _resource_lookup_chat_id(resource_conn, username)
    except Exception:
        if resource_conn is not None:
            try:
                resource_conn.close()
            except Exception:
                pass
        resource_conn = None
        resource_chat_id = None

    trace(
        "resource-db:resolved",
        hasResourceDb=bool(resource_conn is not None),
        resourceChatId=int(resource_chat_id or 0),
    )

    want_asc = str(order or "").lower() != "desc"

    want_types: Optional[set[str]] = None
    if render_types is not None:
        parts = [p.strip() for p in str(render_types or "").split(",") if p.strip()]
        want = {_normalize_render_type_key(p) for p in parts}
        want.discard("")
        if want and not ({"all", "any", "none"} & want):
            want_types = want

    progressive_filter = bool(
        want_types is not None
        and str(filter_mode or "").strip().lower() in {"progressive", "cursor", "scan"}
    )
    progressive_scan_offset = max(0, int(scan_offset or 0))
    progressive_scan_limit = max(50, min(2000, int(scan_limit or 320)))

    if progressive_filter:
        scan_take = progressive_scan_offset + progressive_scan_limit
    else:
        scan_take = int(limit) + int(offset)
    if scan_take < 0:
        scan_take = 0

    merged: list[dict[str, Any]] = []
    sender_usernames: list[str] = []
    quote_usernames: list[str] = []
    pat_usernames: set[str] = set()
    has_more_any = False


    if not db_paths:
        db_paths = _iter_message_db_paths(account_dir)
        if not db_paths:
            trace("response:error", reason="no-message-dbs")
            return {
                "snapshotGeneration": snapshot_generation,
                "status": "error",
                "account": account_dir.name,
                "username": username,
                "source": source_norm,
                "total": 0,
                "messages": [],
                "message": "No message databases found for this account.",
            }
    while True:
        (
            merged,
            has_more_any,
            sender_usernames,
            quote_usernames,
            pat_usernames,
        ) = _collect_chat_messages(
            username=username,
            account_dir=account_dir,
            db_paths=db_paths,
            resource_conn=resource_conn,
            resource_chat_id=resource_chat_id,
            take=scan_take,
            want_types=None if progressive_filter else want_types,
        )

        if progressive_filter:
            break

        if want_types is None:
            break

        if (len(merged) >= (int(offset) + int(limit))) or (not has_more_any):
            break

        next_take = scan_take * 2 if scan_take > 0 else (int(limit) + int(offset))
        if next_take <= scan_take:
            break
        scan_take = next_take

    trace(
        "messages:collected",
        scanTake=int(scan_take),
        mergedCount=len(merged),
        hasMoreAny=bool(has_more_any),
        senderUsernameCount=len(sender_usernames),
        quoteUsernameCount=len(quote_usernames),
        patUsernameCount=len(pat_usernames),
    )

    # Self-heal (default source only): if the decrypted snapshot has no conversation table yet (new session),


    r"""
    take = int(limit) + int(offset)
    take_probe = take + 1
    merged: list[dict[str, Any]] = []
    sender_usernames: list[str] = []
    quote_usernames: list[str] = []
    pat_usernames: set[str] = set()
    is_group = bool(username.endswith("@chatroom"))
    has_more_any = False

    for db_path in db_paths:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            table_name = _resolve_msg_table_name(conn, username)
            if not table_name:
                continue

            my_wxid = account_dir.name
            my_rowid = None
            try:
                r = conn.execute(
                    "SELECT rowid FROM Name2Id WHERE user_name = ? LIMIT 1",
                    (my_wxid,),
                ).fetchone()
                if r is not None:
                    my_rowid = int(r[0])
            except Exception:
                my_rowid = None

            quoted_table = _quote_ident(table_name)
            sql_with_join = (
                "SELECT "
                "m.local_id, m.server_id, m.local_type, m.sort_seq, m.real_sender_id, m.create_time, "
                "m.message_content, m.compress_content, n.user_name AS sender_username "
                f"FROM {quoted_table} m "
                "LEFT JOIN Name2Id n ON m.real_sender_id = n.rowid "
                "ORDER BY m.create_time DESC, m.sort_seq DESC, m.local_id DESC "
                "LIMIT ?"
            )
            sql_no_join = (
                "SELECT "
                "m.local_id, m.server_id, m.local_type, m.sort_seq, m.real_sender_id, m.create_time, "
                "m.message_content, m.compress_content, '' AS sender_username "
                f"FROM {quoted_table} m "
                "ORDER BY m.create_time DESC, m.sort_seq DESC, m.local_id DESC "
                "LIMIT ?"
            )

            # Force sqlite3 to return TEXT as raw bytes for this query, so we can zstd-decompress
            # compress_content reliably.
            conn.text_factory = bytes

            try:
                rows = conn.execute(sql_with_join, (take_probe,)).fetchall()
            except Exception:
                rows = conn.execute(sql_no_join, (take_probe,)).fetchall()
            if len(rows) > take:
                has_more_any = True
                rows = rows[:take]

            for r in rows:
                local_id = int(r["local_id"] or 0)
                create_time = int(r["create_time"] or 0)
                sort_seq = int(r["sort_seq"] or 0) if r["sort_seq"] is not None else 0
                local_type = int(r["local_type"] or 0)
                native_voice_transcript = (
                    _extract_voice_transcript_from_packed_info(_row_get_value(r, "packed_info_data"))
                    if local_type == 34
                    else ""
                )
                sender_username = _decode_sqlite_text(r["sender_username"]).strip()

                is_sent = False
                if my_rowid is not None:
                    try:
                        is_sent = int(r["real_sender_id"] or 0) == int(my_rowid)
                    except Exception:
                        is_sent = False

                raw_text = _decode_message_content(r["compress_content"], r["message_content"])
                raw_text = raw_text.strip()

                sender_prefix = ""
                if is_group and not raw_text.startswith("<") and not raw_text.startswith('"<'):
                    sender_prefix, raw_text = _split_group_sender_prefix(raw_text)

                if is_group and sender_prefix:
                    sender_username = sender_prefix

                if is_group and (not sender_username) and (raw_text.startswith("<") or raw_text.startswith('"<')):
                    xml_sender = _extract_sender_from_group_xml(raw_text)
                    if xml_sender:
                        sender_username = xml_sender

                if is_sent:
                    sender_username = account_dir.name
                elif (not is_group) and (not sender_username):
                    sender_username = username

                if sender_username:
                    sender_usernames.append(sender_username)

                render_type = "text"
                content_text = raw_text
                title = ""
                url = ""
                from_name = ""
                from_username = ""
                record_item = ""
                image_md5 = ""
                emoji_md5 = ""
                emoji_url = ""
                thumb_url = ""
                image_url = ""
                image_file_id = ""
                video_md5 = ""
                video_thumb_md5 = ""
                video_file_id = ""
                video_thumb_file_id = ""
                video_url = ""
                video_thumb_url = ""
                voice_length = ""
                quote_username = ""
                quote_title = ""
                quote_content = ""
                quote_thumb_url = ""
                link_type = ""
                link_style = ""
                object_id = ""
                object_nonce_id = ""
                quote_server_id = ""
                quote_type = ""
                quote_voice_length = ""
                amount = ""
                cover_url = ""
                file_size = ""
                pay_sub_type = ""
                transfer_status = ""
                file_md5 = ""
                transfer_id = ""
                voip_type = ""

                if local_type == 10000:
                    render_type = "system"
                    content_text = _parse_system_message_content(raw_text)
                elif local_type == 49:
                    parsed = _parse_app_message(raw_text)
                    render_type = str(parsed.get("renderType") or "text")
                    content_text = str(parsed.get("content") or "")
                    title = str(parsed.get("title") or "")
                    url = str(parsed.get("url") or "")
                    from_name = str(parsed.get("from") or "")
                    from_username = str(parsed.get("fromUsername") or "")
                    record_item = str(parsed.get("recordItem") or "")
                    quote_title = str(parsed.get("quoteTitle") or "")
                    quote_content = str(parsed.get("quoteContent") or "")
                    quote_thumb_url = str(parsed.get("quoteThumbUrl") or "")
                    link_type = str(parsed.get("linkType") or "")
                    link_style = str(parsed.get("linkStyle") or "")
                    object_id = str(parsed.get("objectId") or "")
                    object_nonce_id = str(parsed.get("objectNonceId") or "")
                    quote_username = str(parsed.get("quoteUsername") or "")
                    quote_server_id = str(parsed.get("quoteServerId") or "")
                    quote_type = str(parsed.get("quoteType") or "")
                    quote_voice_length = str(parsed.get("quoteVoiceLength") or "")
                    amount = str(parsed.get("amount") or "")
                    cover_url = str(parsed.get("coverUrl") or "")
                    thumb_url = str(parsed.get("thumbUrl") or "")
                    file_size = str(parsed.get("size") or "")
                    pay_sub_type = str(parsed.get("paySubType") or "")
                    file_md5 = str(parsed.get("fileMd5") or "")
                    transfer_id = str(parsed.get("transferId") or "")

                    if render_type == "transfer":
                        # 直接从原始 XML 提取 transferid（可能在 wcpayinfo 内）
                        if not transfer_id:
                            transfer_id = _extract_xml_tag_or_attr(raw_text, "transferid") or ""
                        transfer_status = _infer_transfer_status_text(
                            is_sent=is_sent,
                            paysubtype=pay_sub_type,
                            receivestatus=str(parsed.get("receiveStatus") or ""),
                            sendertitle=str(parsed.get("senderTitle") or ""),
                            receivertitle=str(parsed.get("receiverTitle") or ""),
                            senderdes=str(parsed.get("senderDes") or ""),
                            receiverdes=str(parsed.get("receiverDes") or ""),
                        )
                        if not content_text:
                            content_text = transfer_status or "转账"
                elif local_type == 266287972401:
                    render_type = "system"
                    template = _extract_xml_tag_text(raw_text, "template")
                    if template:
                        # import re

                        pat_usernames.update({m.group(1) for m in re.finditer(r"\$\{([^}]+)\}", template) if m.group(1)})
                        content_text = "[拍一拍]"
                    else:
                        content_text = "[拍一拍]"
                elif local_type == 244813135921:
                    render_type = "quote"
                    parsed = _parse_app_message(raw_text)
                    content_text = str(parsed.get("content") or "[引用消息]")
                    quote_title = str(parsed.get("quoteTitle") or "")
                    quote_content = str(parsed.get("quoteContent") or "")
                    quote_thumb_url = str(parsed.get("quoteThumbUrl") or "")
                    link_type = str(parsed.get("linkType") or "")
                    link_style = str(parsed.get("linkStyle") or "")
                    quote_username = str(parsed.get("quoteUsername") or "")
                    quote_server_id = str(parsed.get("quoteServerId") or "")
                    quote_type = str(parsed.get("quoteType") or "")
                    quote_voice_length = str(parsed.get("quoteVoiceLength") or "")
                elif local_type == 3:
                    render_type = "image"
                    xml_md5s = _extract_image_md5_candidates(raw_text)
                    image_md5 = xml_md5s[0] if xml_md5s else ''

                    # Prefer message_resource.db md5 for local files: XML md5 frequently differs from the on-disk *.dat basename
                    # (especially for *_t.dat thumbnails), causing the media endpoint to 404.
                    if resource_conn is not None:
                        try:
                            resource_md5 = _lookup_resource_md5(
                                resource_conn,
                                resource_chat_id,
                                message_local_type=local_type,
                                server_id=int(r["server_id"] or 0),
                                local_id=local_id,
                                create_time=create_time,
                            )
                        except Exception:
                            resource_md5 = ""
                        resource_md5 = str(resource_md5 or "").strip().lower()
                        if len(resource_md5) == 32 and all(c in "0123456789abcdef" for c in resource_md5):
                            image_md5 = resource_md5

                    image_url, image_file_id, _ = _extract_image_cdn_refs(raw_text)
                    content_text = "[图片]"
                elif local_type == 34:
                    render_type = "voice"
                    duration = _extract_xml_attr(raw_text, "voicelength")
                    voice_length = duration
                    content_text = f"[语音 {duration}秒]" if duration else "[语音]"
                elif local_type == 43 or local_type == 62:
                    render_type = "video"
                    video_md5 = _extract_xml_attr(raw_text, "md5")
                    video_thumb_md5 = _extract_xml_attr(raw_text, "cdnthumbmd5")
                    video_thumb_url_or_id = _extract_xml_attr(raw_text, "cdnthumburl") or _extract_xml_tag_text(
                        raw_text, "cdnthumburl"
                    )
                    video_url_or_id = _extract_xml_attr(raw_text, "cdnvideourl") or _extract_xml_tag_text(
                        raw_text, "cdnvideourl"
                    )

                    video_thumb_url = (
                        video_thumb_url_or_id
                        if str(video_thumb_url_or_id or "").strip().lower().startswith(("http://", "https://"))
                        else ""
                    )
                    video_url = (
                        video_url_or_id
                        if str(video_url_or_id or "").strip().lower().startswith(("http://", "https://"))
                        else ""
                    )
                    video_thumb_file_id = "" if video_thumb_url else (str(video_thumb_url_or_id or "").strip() or "")
                    video_file_id = "" if video_url else (str(video_url_or_id or "").strip() or "")
                    if (not video_thumb_md5) and resource_conn is not None:
                        video_thumb_md5 = _lookup_resource_md5(
                            resource_conn,
                            resource_chat_id,
                            message_local_type=local_type,
                            server_id=int(r["server_id"] or 0),
                            local_id=local_id,
                            create_time=create_time,
                        )
                    # Match WeFlow video lookup: packed_info_data may be the local msg/video basename.
                    # Keep XML md5/file_id as fallback, but prefer the packed token for local playback.
                    try:
                        packed_val = r["packed_info_data"]
                    except Exception:
                        try:
                            packed_val = r.get("packed_info_data")  # type: ignore[attr-defined]
                        except Exception:
                            packed_val = None
                    packed_video_token = _extract_md5_from_packed_info(packed_val)
                    if packed_video_token:
                        video_md5 = packed_video_token
                        if not _is_hex_md5(video_thumb_md5):
                            video_thumb_md5 = packed_video_token
                            video_thumb_file_id = ""
                    content_text = "[视频]"
                elif local_type == 47:
                    render_type = "emoji"
                    emoji_md5 = _extract_xml_attr(raw_text, "md5")
                    if not emoji_md5:
                        emoji_md5 = _extract_xml_tag_text(raw_text, "md5")
                    emoji_url = _extract_xml_attr(raw_text, "cdnurl")
                    if not emoji_url:
                        emoji_url = _extract_xml_tag_text(raw_text, "cdn_url")
                    if (not emoji_md5) and resource_conn is not None:
                        emoji_md5 = _lookup_resource_md5(
                            resource_conn,
                            resource_chat_id,
                            message_local_type=local_type,
                            server_id=int(r["server_id"] or 0),
                            local_id=local_id,
                            create_time=create_time,
                        )
                    content_text = "[表情]"
                elif local_type == 50:
                    render_type = "voip"
                    try:
                        # import re

                        block = raw_text
                        m_voip = re.search(
                            r"(<VoIPBubbleMsg[^>]*>.*?</VoIPBubbleMsg>)",
                            raw_text,
                            flags=re.IGNORECASE | re.DOTALL,
                        )
                        if m_voip:
                            block = m_voip.group(1) or raw_text
                        room_type = str(_extract_xml_tag_text(block, "room_type") or "").strip()
                        if room_type == "0":
                            voip_type = "video"
                        elif room_type == "1":
                            voip_type = "audio"

                        voip_msg = str(_extract_xml_tag_text(block, "msg") or "").strip()
                        content_text = voip_msg or "通话"
                    except Exception:
                        content_text = "通话"
                elif local_type != 1:
                    if not content_text:
                        content_text = _infer_message_brief_by_local_type(local_type)
                    else:
                        if content_text.startswith("<") or content_text.startswith('"<'):
                            parsed_special = False
                            if "<appmsg" in content_text.lower():
                                parsed = _parse_app_message(content_text)
                                rt = str(parsed.get("renderType") or "")
                                if rt and rt != "text":
                                    parsed_special = True
                                    render_type = rt
                                    content_text = str(parsed.get("content") or content_text)
                                    title = str(parsed.get("title") or title)
                                    url = str(parsed.get("url") or url)
                                    from_name = str(parsed.get("from") or from_name)
                                    record_item = str(parsed.get("recordItem") or record_item)
                                    quote_title = str(parsed.get("quoteTitle") or quote_title)
                                    quote_content = str(parsed.get("quoteContent") or quote_content)
                                    quote_thumb_url = str(parsed.get("quoteThumbUrl") or quote_thumb_url)
                                    link_type = str(parsed.get("linkType") or link_type)
                                    link_style = str(parsed.get("linkStyle") or link_style)
                                    object_id = str(parsed.get("objectId") or object_id)
                                    object_nonce_id = str(parsed.get("objectNonceId") or object_nonce_id)
                                    amount = str(parsed.get("amount") or amount)
                                    cover_url = str(parsed.get("coverUrl") or cover_url)
                                    thumb_url = str(parsed.get("thumbUrl") or thumb_url)
                                    file_size = str(parsed.get("size") or file_size)
                                    pay_sub_type = str(parsed.get("paySubType") or pay_sub_type)
                                    file_md5 = str(parsed.get("fileMd5") or file_md5)
                                    transfer_id = str(parsed.get("transferId") or transfer_id)

                                    if render_type == "transfer":
                                        # 如果 transferId 仍为空，尝试从原始 XML 提取
                                        if not transfer_id:
                                            transfer_id = _extract_xml_tag_or_attr(content_text, "transferid") or ""
                                        transfer_status = _infer_transfer_status_text(
                                            is_sent=is_sent,
                                            paysubtype=pay_sub_type,
                                            receivestatus=str(parsed.get("receiveStatus") or ""),
                                            sendertitle=str(parsed.get("senderTitle") or ""),
                                            receivertitle=str(parsed.get("receiverTitle") or ""),
                                            senderdes=str(parsed.get("senderDes") or ""),
                                            receiverdes=str(parsed.get("receiverDes") or ""),
                                        )
                                        if not content_text:
                                            content_text = transfer_status or "转账"

                            if not parsed_special:
                                t = _extract_xml_tag_text(content_text, "title")
                                d = _extract_xml_tag_text(content_text, "des")
                                content_text = t or d or _infer_message_brief_by_local_type(local_type)

                if not content_text:
                    content_text = _infer_message_brief_by_local_type(local_type)

                if quote_username:
                    quote_usernames.append(str(quote_username).strip())

                merged.append(
                    {
                        "id": f"{db_path.stem}:{table_name}:{local_id}",
                        "localId": local_id,
                        "serverId": int(r["server_id"] or 0),
                        "serverIdStr": str(int(r["server_id"] or 0)) if int(r["server_id"] or 0) else "",
                        "type": local_type,
                        "createTime": create_time,
                        "sortSeq": sort_seq,
                        "senderUsername": sender_username,
                        "isSent": bool(is_sent),
                        "renderType": render_type,
                        "content": content_text,
                        "title": title,
                        "url": url,
                        "linkType": link_type,
                        "linkStyle": link_style,
                        "objectId": object_id,
                        "objectNonceId": object_nonce_id,
                        "from": from_name,
                        "fromUsername": from_username,
                        "recordItem": record_item,
                        "imageMd5": image_md5,
                        "imageFileId": image_file_id,
                        "emojiMd5": emoji_md5,
                        "emojiUrl": emoji_url,
                        "thumbUrl": thumb_url,
                        "imageUrl": image_url,
                        "videoMd5": video_md5,
                        "videoThumbMd5": video_thumb_md5,
                        "videoFileId": video_file_id,
                        "videoThumbFileId": video_thumb_file_id,
                        "videoUrl": video_url,
                        "videoThumbUrl": video_thumb_url,
                        "voiceLength": voice_length,
                        "voiceTranscript": native_voice_transcript,
                        "voiceTranscriptStatus": "success" if native_voice_transcript else "idle",
                        "voiceTranscriptError": "",
                        "voiceTranscriptLanguage": "",
                        "voiceTranscriptModel": "wechat-native" if native_voice_transcript else "",
                        "voipType": voip_type,
                        "quoteUsername": str(quote_username).strip(),
                        "quoteServerId": str(quote_server_id).strip(),
                        "quoteType": str(quote_type).strip(),
                        "quoteVoiceLength": str(quote_voice_length).strip(),
                        "quoteTitle": quote_title,
                        "quoteContent": quote_content,
                        "quoteThumbUrl": quote_thumb_url,
                        "amount": amount,
                        "coverUrl": cover_url,
                        "fileSize": file_size,
                        "fileMd5": file_md5,
                        "paySubType": pay_sub_type,
                        "transferStatus": transfer_status,
                        "transferId": transfer_id,
                        "_rawText": raw_text if local_type in (10000, 266287972401) else "",
                    }
                )
        finally:
            conn.close()

    """
    if resource_conn is not None:
        try:
            resource_conn.close()
        except Exception:
            pass

    # Guard against duplicate message ids.
    # Duplicate ids break Vue list rendering (duplicate keys) and can cause incorrect message display.
    if merged:
        seen_ids: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for m in merged:
            mid = str(m.get("id") or "")
            if not mid:
                deduped.append(m)
                continue
            if mid in seen_ids:
                continue
            seen_ids.add(mid)
            deduped.append(m)
        merged = deduped

    _postprocess_transfer_messages(merged)
    _merge_anti_revoke_into_messages(
        merged,
        account_dir=account_dir,
        username=username,
        want_types=want_types,
    )

    def sort_key(m: dict[str, Any]) -> tuple[int, int, int, int]:
        sseq = int(m.get("sortSeq") or 0)
        cts = int(m.get("createTime") or 0)
        lid = int(m.get("localId") or 0)
        is_sys = 1 if (m.get("renderType") == "system" or int(m.get("type") or 0) == 10000) else 0
        return (cts, sseq, lid, is_sys)

    merged.sort(key=sort_key, reverse=True)
    next_scan_offset: Optional[int] = None
    next_filter_offset: Optional[int] = None
    if progressive_filter:
        raw_start = max(0, int(progressive_scan_offset))
        raw_end = raw_start + int(progressive_scan_limit)
        raw_window = merged[raw_start:raw_end] if raw_start < len(merged) else []
        filtered_window = [
            m for m in raw_window
            if _normalize_render_type_key(m.get("renderType")) in (want_types or set())
        ]
        match_offset = max(0, int(offset))
        page = filtered_window[match_offset: match_offset + int(limit)]
        has_more_matches_in_window = (match_offset + int(limit)) < len(filtered_window)
        has_more_global = bool(has_more_matches_in_window or has_more_any or (len(merged) > raw_end))
        if has_more_matches_in_window:
            next_scan_offset = raw_start
            next_filter_offset = match_offset + int(limit)
        else:
            next_scan_offset = raw_end
            next_filter_offset = 0
    else:
        has_more_global = bool(has_more_any or (len(merged) > (int(offset) + int(limit))))
        page = merged[int(offset) : int(offset) + int(limit)]
    if want_asc:
        page = list(reversed(page))

    trace(
        "page:sliced",
        mergedCount=len(merged),
        pageCount=len(page),
        hasMore=bool(has_more_global),
        orderAsc=bool(want_asc),
        progressive=bool(progressive_filter),
        nextScanOffset=next_scan_offset,
        nextFilterOffset=next_filter_offset,
    )

    # Hot path optimization: only enrich the page we return.
    if not page:
        trace("response:ready", pageCount=0)
        return {
            "snapshotGeneration": snapshot_generation,
            "status": "success",
            "account": account_dir.name,
            "username": username,
            "source": source_norm,

            "total": int(offset) + (1 if has_more_global else 0),
            "hasMore": bool(has_more_global),
            "filterMode": "progressive" if progressive_filter else "",
            "nextScanOffset": next_scan_offset,
            "nextFilterOffset": next_filter_offset,
            "messages": [],
        }

    messages_window = page

    # Some appmsg payloads provide only `from` (sourcedisplayname) but not `fromUsername` (sourceusername).
    # Recover `fromUsername` via contact.db so the frontend can render the publisher avatar.
    missing_from_names = [
        str(m.get("from") or "").strip()
        for m in messages_window
        if str(m.get("renderType") or "").strip() == "link"
        and str(m.get("from") or "").strip()
        and not str(m.get("fromUsername") or "").strip()
    ]
    if missing_from_names:
        name_to_username = _load_usernames_by_display_names(contact_db_path, missing_from_names)
        if name_to_username:
            for m in messages_window:
                if str(m.get("fromUsername") or "").strip():
                    continue
                if str(m.get("renderType") or "").strip() != "link":
                    continue
                fn = str(m.get("from") or "").strip()
                if fn and fn in name_to_username:
                    m["fromUsername"] = name_to_username[fn]

    pat_usernames_in_page: set[str] = set()
    for m in messages_window:
        if int(m.get("type") or 0) != 266287972401:
            continue
        raw = str(m.get("_rawText") or "")
        if not raw:
            continue
        template = _extract_xml_tag_text(raw, "template")
        if not template:
            continue
        pat_usernames_in_page.update({mm.group(1) for mm in re.finditer(r"\$\{([^}]+)\}", template) if mm.group(1)})

    system_usernames_in_page: set[str] = set()
    for m in messages_window:
        if int(m.get("type") or 0) != 10000:
            continue
        meta = _extract_chatroom_top_message_metadata(str(m.get("_rawText") or ""))
        operator_username = str(meta.get("operatorUsername") or "").strip()
        if operator_username:
            system_usernames_in_page.add(operator_username)

    from_usernames = [str(m.get("fromUsername") or "").strip() for m in messages_window]
    sender_usernames_in_page = [str(m.get("senderUsername") or "").strip() for m in messages_window]
    quote_usernames_in_page = [str(m.get("quoteUsername") or "").strip() for m in messages_window]
    uniq_senders = list(
        dict.fromkeys(
            [
                u
                for u in (
                    sender_usernames_in_page
                    + list(pat_usernames_in_page)
                    + quote_usernames_in_page
                    + from_usernames
                    + list(system_usernames_in_page)
                )
                if u
            ]
        )
    )
    sender_contact_rows = _load_contact_rows(contact_db_path, uniq_senders)
    local_sender_avatars = _query_head_image_usernames(head_image_db_path, uniq_senders)
    trace(
        "senders:loaded",
        uniqSenderCount=len(uniq_senders),
        senderContactRowCount=len(sender_contact_rows),
        localSenderAvatarCount=len(local_sender_avatars),
    )



    group_nicknames = _load_group_nickname_map(
        account_dir=account_dir,
        contact_db_path=contact_db_path,
        chatroom_id=username,
        sender_usernames=uniq_senders,

    )
    trace(
        "sender-fallbacks:loaded",
        groupNicknameCount=len(group_nicknames),
    )

    enterprise_contacts = _load_enterprise_contact_info(
        contact_db_path, sender_usernames_in_page,

    )
    for m in messages_window:
        # If appmsg doesn't provide sourcedisplayname, try mapping sourceusername to display name.
        if (not str(m.get("from") or "").strip()) and str(m.get("fromUsername") or "").strip():
            fu = str(m.get("fromUsername") or "").strip()
            frow = sender_contact_rows.get(fu)
            if frow is not None:
                m["from"] = _pick_display_name(frow, fu)

        su = str(m.get("senderUsername") or "")
        if su:
            m["senderEnterpriseName"] = enterprise_contacts.get(su, {}).get("enterpriseName", "")
            m["senderDisplayName"] = _resolve_sender_display_name(
                sender_username=su,
                sender_contact_rows=sender_contact_rows,

                group_nicknames=group_nicknames,
            )
            avatar_url = base_url + _avatar_url_unified(
                account_dir=account_dir,
                username=su,
                local_avatar_usernames=local_sender_avatars,
            )
            m["senderAvatar"] = avatar_url

        qu = str(m.get("quoteUsername") or "").strip()
        if qu:
            qrow = sender_contact_rows.get(qu)
            qt = str(m.get("quoteTitle") or "").strip()
            if qrow is not None:
                remark = ""
                try:
                    remark = str(qrow["remark"] or "").strip()
                except Exception:
                    remark = ""
                if remark:
                    m["quoteTitle"] = remark
                elif not qt:
                    title = _pick_display_name(qrow, qu)
                    m["quoteTitle"] = title
            elif not qt:
                m["quoteTitle"] = qu

        # Media URL fallback: if CDN URLs missing, use local media endpoints.
        try:
            rt = str(m.get("renderType") or "")
            if rt == "image":
                if not str(m.get("imageUrl") or ""):
                    md5 = str(m.get("imageMd5") or "").strip()
                    file_id = str(m.get("imageFileId") or "").strip()
                    if md5:
                        m["imageUrl"] = (
                            base_url
                            + f"/api/chat/media/image?account={quote(account_dir.name)}&md5={quote(md5)}&username={quote(username)}"
                        )
                    elif file_id:
                        m["imageUrl"] = (
                            base_url
                            + f"/api/chat/media/image?account={quote(account_dir.name)}&file_id={quote(file_id)}&username={quote(username)}"
                        )
            elif rt == "emoji":
                md5 = str(m.get("emojiMd5") or "")
                if md5:
                    existing_local: Optional[Path] = None

                    # Preserve the remote URL only for the user's explicit download.
                    try:
                        # import re
                        cur = str(m.get("emojiUrl") or "")
                        if cur and re.match(r"^https?://", cur, flags=re.I) and ("/api/chat/media/emoji" not in cur):
                            m["emojiRemoteUrl"] = cur
                    except Exception:
                        pass

                    m["emojiUrl"] = (
                        base_url
                        + f"/api/chat/media/emoji?account={quote(account_dir.name)}&md5={quote(md5)}&username={quote(username)}"
                    )
            elif rt == "video":
                video_thumb_url = str(m.get("videoThumbUrl") or "").strip()
                video_thumb_md5 = str(m.get("videoThumbMd5") or "").strip()
                video_thumb_file_id = str(m.get("videoThumbFileId") or "").strip()
                if (not video_thumb_url) or (
                    not video_thumb_url.lower().startswith(("http://", "https://"))
                ):
                    if video_thumb_md5:
                        m["videoThumbUrl"] = (
                            base_url
                            + f"/api/chat/media/video_thumb?account={quote(account_dir.name)}&md5={quote(video_thumb_md5)}&username={quote(username)}"
                            + (f"&file_id={quote(video_thumb_file_id)}" if video_thumb_file_id else "")
                        )
                    elif video_thumb_file_id:
                        m["videoThumbUrl"] = (
                            base_url
                            + f"/api/chat/media/video_thumb?account={quote(account_dir.name)}&file_id={quote(video_thumb_file_id)}&username={quote(username)}"
                        )

                video_url = str(m.get("videoUrl") or "").strip()
                video_md5 = str(m.get("videoMd5") or "").strip()
                video_file_id = str(m.get("videoFileId") or "").strip()
                if (not video_url) or (not video_url.lower().startswith(("http://", "https://"))):
                    if video_md5:
                        m["videoUrl"] = (
                            base_url
                            + f"/api/chat/media/video?account={quote(account_dir.name)}&md5={quote(video_md5)}&username={quote(username)}"
                            + (f"&file_id={quote(video_file_id)}" if video_file_id else "")
                        )
                    elif video_file_id:
                        m["videoUrl"] = (
                            base_url
                            + f"/api/chat/media/video?account={quote(account_dir.name)}&file_id={quote(video_file_id)}&username={quote(username)}"
                        )
            elif rt == "link":
                thumb_url = str(m.get("thumbUrl") or "").strip()
                if thumb_url and (not thumb_url.lower().startswith(("http://", "https://"))):
                    try:
                        lid = int(m.get("localId") or 0)
                    except Exception:
                        lid = 0
                    try:
                        ct = int(m.get("createTime") or 0)
                    except Exception:
                        ct = 0
                    if lid > 0 and ct > 0:
                        file_id = f"{lid}_{ct}"
                        m["thumbUrl"] = (
                            base_url
                            + f"/api/chat/media/image?account={quote(account_dir.name)}&file_id={quote(file_id)}&username={quote(username)}"
                        )
            elif rt == "voice":
                if str(m.get("serverId") or ""):
                    sid = int(m.get("serverId") or 0)
                    if sid:
                        m["voiceUrl"] = base_url + f"/api/chat/media/voice?account={quote(account_dir.name)}&server_id={sid}"
        except Exception:
            pass

        _postprocess_special_message_content(
            message=m,
            sender_contact_rows=sender_contact_rows,

        )

    trace(
        "response:ready",
        pageCount=len(page),
        total=int(offset) + len(page) + (1 if has_more_global else 0),
        hasMore=bool(has_more_global),
        progressive=bool(progressive_filter),
        nextScanOffset=next_scan_offset,
        nextFilterOffset=next_filter_offset,
    )
    return {
        "snapshotGeneration": snapshot_generation,
        "status": "success",
        "account": account_dir.name,
        "username": username,
        "source": source_norm,

        "total": int(offset) + len(page) + (1 if has_more_global else 0),
        "hasMore": bool(has_more_global),
        "filterMode": "progressive" if progressive_filter else "",
        "nextScanOffset": next_scan_offset,
        "nextFilterOffset": next_filter_offset,
        "messages": page,
    }


def _chat_search_env_int(name: str, default: int, *, min_value: int, max_value: int) -> int:
    raw = os.environ.get(name)
    try:
        value = int(str(raw or "").strip() or default)
    except Exception:
        value = int(default)
    return max(int(min_value), min(int(max_value), int(value)))


def _single_char_recent_probe_min_docs() -> int:
    return _chat_search_env_int(
        "WECHAT_CHAT_SEARCH_SINGLE_CHAR_RECENT_MIN_DOCS",
        2000,
        min_value=1,
        max_value=1_000_000,
    )


def _index_table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (str(table_name or "").strip(),),
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _single_char_recent_probe_token(q: str, tokens: list[str]) -> str:
    if len(tokens or []) != 1:
        return ""
    token = str((tokens or [""])[0] or "").strip().lower()
    if len(token) != 1:
        return ""
    token_text = _to_char_token_text(token)
    if len(token_text) != 1:
        return ""
    if str(q or "").strip().lower() != token:
        return ""
    return token_text


def _should_use_single_char_recent_probe(conn: sqlite3.Connection, token: str) -> bool:
    if not token:
        return False
    if not _index_table_exists(conn, "message_meta") or not _index_table_exists(conn, "message_token_stats"):
        return False
    try:
        row = conn.execute("SELECT doc_count FROM message_token_stats WHERE token=? LIMIT 1", (token,)).fetchone()
        doc_count = int((row[0] if row else 0) or 0)
    except Exception:
        return False
    return doc_count >= _single_char_recent_probe_min_docs()


async def _search_chat_messages_via_fts(request: Request, *, q: str, account: Optional[str], username: Optional[str], sender: Optional[str], session_type: Optional[str], limit: int, offset: int, start_time: Optional[int], end_time: Optional[int], render_types: Optional[str], include_hidden: bool, include_official: bool, source: Optional[str]=None) -> dict[str, Any]:
    tokens = _make_search_tokens(q)

    if limit <= 0:
        raise HTTPException(status_code=400, detail="Invalid limit.")
    if limit > 200:
        limit = 200
    if offset < 0:
        offset = 0

    start_ts = int(start_time) if start_time is not None else None
    end_ts = int(end_time) if end_time is not None else None
    if start_ts is not None and start_ts < 0:
        start_ts = 0
    if end_ts is not None and end_ts < 0:
        end_ts = 0

    want_types: Optional[set[str]] = None
    if render_types is not None:
        parts = [p.strip() for p in str(render_types or "").split(",") if p.strip()]
        want_types = {p for p in parts if p}
        if not want_types:
            want_types = None

    username = str(username).strip() if username else None
    if not username:
        username = None

    sender = str(sender).strip() if sender else None
    if not sender:
        sender = None

    session_type_norm = _normalize_session_type(session_type)
    trace_id = f"msg-search-{int(time.time() * 1000)}-{threading.get_ident()}"
    logger.info(
        "[%s] chat search start account=%s scope=%s username=%s sender=%s q_len=%s token_count=%s limit=%s offset=%s start_time=%s end_time=%s render_types=%s include_hidden=%s include_official=%s",
        trace_id,
        str(account or "").strip(),
        "conversation" if username else "global",
        str(username or "").strip(),
        str(sender or "").strip(),
        len(str(q or "")),
        len(tokens),
        int(limit),
        int(offset),
        "" if start_ts is None else int(start_ts),
        "" if end_ts is None else int(end_ts),
        str(render_types or "").strip(),
        bool(include_hidden),
        bool(include_official),
    )

    source_requested = _normalize_chat_source(source)
    # 搜索索引恢复为旧模式：索引只从 output/databases/{account} 下的解密 SQLite

    index_source = "decrypted"
    account_dir = _resolve_account_dir(account)
    contact_db_path = resolve_account_database_dir(account_dir) / "contact.db"
    head_image_db_path = resolve_account_database_dir(account_dir) / "head_image.db"
    base_url = str(request.base_url).rstrip("/")

    index_status = get_chat_search_index_status(account_dir, source=index_source)
    index = dict(index_status.get("index") or {})
    build = dict(index.get("build") or {})

    index_exists = bool(index.get("exists"))
    index_ready = bool(index.get("ready"))
    build_status = str(build.get("status") or "").strip()

    if (not index_ready) and build_status not in {"building", "error"}:
        start_chat_search_index_build(account_dir, rebuild=bool(index_exists), source=index_source)
        index_status = get_chat_search_index_status(account_dir, source=index_source)
        index = dict(index_status.get("index") or {})
        build = dict(index.get("build") or {})
        build_status = str(build.get("status") or "").strip()
        index_exists = bool(index.get("exists"))
        index_ready = bool(index.get("ready"))

    if build_status == "error":
        logger.warning(
            "[%s] chat search index_error account=%s scope=%s username=%s message=%s",
            trace_id,
            account_dir.name,
            "conversation" if username else "global",
            str(username or "").strip(),
            str(build.get("error") or "Search index build failed."),
        )
        return {
            "status": "index_error",
            "account": account_dir.name,
            "q": q,
            "tokens": tokens,
            "scope": "conversation" if username else "global",
            "username": username,
            "offset": int(offset),
            "limit": int(limit),
            "baseUrl": base_url,
            "total": 0,
            "hasMore": False,
            "hits": [],
            "index": index,
            "message": str(build.get("error") or "Search index build failed."),
        }

    if not index_ready:
        logger.info(
            "[%s] chat search index_building account=%s scope=%s username=%s build_status=%s",
            trace_id,
            account_dir.name,
            "conversation" if username else "global",
            str(username or "").strip(),
            build_status,
        )
        return {
            "status": "index_building",
            "account": account_dir.name,
            "q": q,
            "tokens": tokens,
            "scope": "conversation" if username else "global",
            "username": username,
            "offset": int(offset),
            "limit": int(limit),
            "baseUrl": base_url,
            "total": 0,
            "hasMore": False,
            "hits": [],
            "index": index,
            "message": "Search index is building. Please retry in a moment.",
        }

    fts_query = _build_fts_query(q)
    if tokens and not fts_query:
        raise HTTPException(status_code=400, detail="Missing q.")

    index_db_path = get_chat_search_index_db_path(account_dir)
    conn = sqlite3.connect(str(index_db_path))
    conn.row_factory = sqlite3.Row
    index_query_mode = "fts"
    try:
        try:
            single_char_token = _single_char_recent_probe_token(q, tokens)
            if not tokens or _should_use_single_char_recent_probe(conn, single_char_token):
                # 无关键词的类型浏览也使用现有时间索引，避免扫描 FTS 全表再排序。
                # 高频单字（如“奶”）在 FTS 命中后再按时间排序会把大量命中项全部取出排序。
                # 新索引同步维护 message_meta 的时间顺序索引；对高频单字改为从最近消息向前探测，
                # 只取 limit+1 条，避免单字搜索被全量排序拖慢。低频/旧索引仍走 FTS。
                index_query_mode = "single_char_recent_probe" if tokens else "type_filter"
                where_parts: list[str] = ["instr(m.text, ?) > 0"] if tokens else []
                params: list[Any] = [single_char_token] if tokens else []

                if username:
                    where_parts.append("m.username = ?")
                    params.append(str(username))
                elif session_type_norm == "group":
                    where_parts.append("m.username LIKE ?")
                    params.append("%@chatroom")
                elif session_type_norm == "single":
                    where_parts.append("m.username NOT LIKE ?")
                    params.append("%@chatroom")

                if sender:
                    where_parts.append("m.sender_username = ?")
                    params.append(str(sender))

                if want_types is not None:
                    types_sorted = sorted(want_types)
                    placeholders = ",".join(["?"] * len(types_sorted))
                    where_parts.append(f"m.render_type IN ({placeholders})")
                    params.extend(types_sorted)

                if start_ts is not None:
                    where_parts.append("m.create_time >= ?")
                    params.append(int(start_ts))
                if end_ts is not None:
                    where_parts.append("m.create_time <= ?")
                    params.append(int(end_ts))

                if not include_hidden:
                    where_parts.append("m.is_hidden = 0")
                if not include_official:
                    where_parts.append("m.is_official = 0")

                where_sql = " AND ".join(where_parts)
                rows_probe = conn.execute(
                    f"""
                    SELECT
                        f.username,
                        f.db_stem,
                        f.table_name,
                        f.local_id,
                        f.payload_json,
                        f.render_type,
                        f.create_time,
                        f.sort_seq,
                        f.server_id,
                        f.local_type,
                        f.sender_username
                    FROM message_meta m
                    JOIN message_fts f ON f.rowid = m.rowid
                    WHERE {where_sql}
                    ORDER BY
                        m.create_time DESC,
                        m.sort_seq DESC,
                        m.local_id DESC
                    LIMIT ? OFFSET ?
                    """,
                    params + [int(limit) + 1, int(offset)],
                ).fetchall()
            else:
                where_parts = ["message_fts MATCH ?"]
                params = [fts_query]

                if username:
                    where_parts.append("username = ?")
                    params.append(str(username))
                elif session_type_norm == "group":
                    where_parts.append("username LIKE ?")
                    params.append("%@chatroom")
                elif session_type_norm == "single":
                    where_parts.append("username NOT LIKE ?")
                    params.append("%@chatroom")

                if sender:
                    where_parts.append("sender_username = ?")
                    params.append(str(sender))

                if want_types is not None:
                    types_sorted = sorted(want_types)
                    placeholders = ",".join(["?"] * len(types_sorted))
                    where_parts.append(f"render_type IN ({placeholders})")
                    params.extend(types_sorted)

                if start_ts is not None:
                    where_parts.append("CAST(create_time AS INTEGER) >= ?")
                    params.append(int(start_ts))
                if end_ts is not None:
                    where_parts.append("CAST(create_time AS INTEGER) <= ?")
                    params.append(int(end_ts))

                if not include_hidden:
                    where_parts.append("CAST(is_hidden AS INTEGER) = 0")
                if not include_official:
                    where_parts.append("CAST(is_official AS INTEGER) = 0")

                where_sql = " AND ".join(where_parts)
                # 单字高频词（如“奶”）的精确 COUNT(*) 会强制 FTS 扫完所有命中项；
                # 搜索侧只需要当前页和是否还有下一页，所以用 limit+1 作为快路径。
                rows_probe = conn.execute(
                    f"""
                    SELECT
                        username,
                        db_stem,
                        table_name,
                        local_id,
                        payload_json,
                        render_type,
                        create_time,
                        sort_seq,
                        server_id,
                        local_type,
                        sender_username
                    FROM message_fts
                    WHERE {where_sql}
                    ORDER BY
                        CAST(create_time AS INTEGER) DESC,
                        CAST(sort_seq AS INTEGER) DESC,
                        CAST(local_id AS INTEGER) DESC
                    LIMIT ? OFFSET ?
                    """,
                    params + [int(limit) + 1, int(offset)],
                ).fetchall()
            has_more_index = len(rows_probe) > int(limit)
            rows = rows_probe[: int(limit)]
            total = int(offset) + len(rows) + (1 if has_more_index else 0)
        except Exception as e:
            logger.exception(
                "[%s] chat search index query failed account=%s scope=%s username=%s",
                trace_id,
                account_dir.name,
                "conversation" if username else "global",
                str(username or "").strip(),
            )
            return {
                "status": "index_error",
                "account": account_dir.name,
                "q": q,
                "tokens": tokens,
                "scope": "conversation" if username else "global",
                    "username": username,
                "offset": int(offset),
                "limit": int(limit),
                "baseUrl": base_url,
                "total": 0,
                "hasMore": False,
                "hits": [],
                "index": index,
                "message": str(e),
            }
    finally:
        conn.close()

    db_paths = _iter_message_db_paths(account_dir)
    stem_to_path = {p.stem: p for p in db_paths}

    groups: dict[tuple[Path, str, str], list[int]] = {}
    ordered_keys: list[tuple[Any, str, str, int]] = []
    hit_by_key: dict[tuple[Any, str, str, int], dict[str, Any]] = {}
    for r in rows:
        conv_username = str(r["username"] or "").strip()
        db_stem = str(r["db_stem"] or "").strip()
        table_name = str(r["table_name"] or "").strip()
        local_id = int(r["local_id"] or 0)
        if not conv_username or not db_stem or not table_name or local_id <= 0:
            continue
        db_path = stem_to_path.get(db_stem)
        if db_path is None:
            payload_str = str(r["payload_json"] if "payload_json" in r.keys() else "")
            if payload_str:
                try:
                    payload_hit = json.loads(payload_str)
                    if not payload_hit.get("conversationUsername"):
                        payload_hit["conversationUsername"] = conv_username
                    if not payload_hit.get("username"):
                        payload_hit["username"] = conv_username
                    key4 = (Path(db_stem), table_name, conv_username, local_id)
                    snippet_src = (
                        str(payload_hit.get("content") or "").strip()
                        or str(payload_hit.get("title") or "").strip()
                    )
                    payload_hit["snippet"] = _make_snippet(snippet_src, tokens)
                    hit_by_key[key4] = payload_hit
                    ordered_keys.append(key4)
                except Exception:
                    pass
            continue
        key4 = (db_path, table_name, conv_username, local_id)
        groups.setdefault((db_path, table_name, conv_username), []).append(local_id)
        ordered_keys.append(key4)

    self_username = resolve_account_username(account_dir)

    for (db_path, table_name, conv_username), local_ids in groups.items():
        uniq_local_ids = list(dict.fromkeys([int(x) for x in local_ids if int(x) > 0]))
        if not uniq_local_ids:
            continue

        msg_conn = sqlite3.connect(str(db_path))
        msg_conn.row_factory = sqlite3.Row
        msg_conn.text_factory = bytes
        try:
            my_rowid, _matched_self_username = _resolve_message_self_rowid(
                msg_conn,
                account_dir,
            )
            db_self_username = _matched_self_username or self_username

            placeholders = ",".join(["?"] * len(uniq_local_ids))
            quoted_table = _quote_ident(table_name)

            sql_with_join = (
                "SELECT "
                "m.local_id, m.server_id, m.local_type, m.sort_seq, m.real_sender_id, m.create_time, "
                "m.message_content, m.compress_content, n.user_name AS sender_username "
                f"FROM {quoted_table} m "
                "LEFT JOIN Name2Id n ON m.real_sender_id = n.rowid "
                f"WHERE m.local_id IN ({placeholders})"
            )
            sql_no_join = (
                "SELECT "
                "m.local_id, m.server_id, m.local_type, m.sort_seq, m.real_sender_id, m.create_time, "
                "m.message_content, m.compress_content, '' AS sender_username "
                f"FROM {quoted_table} m "
                f"WHERE m.local_id IN ({placeholders})"
            )

            try:
                try:
                    msg_rows = msg_conn.execute(sql_with_join, uniq_local_ids).fetchall()
                except Exception:
                    msg_rows = msg_conn.execute(sql_no_join, uniq_local_ids).fetchall()
            except Exception:
                continue

            is_group = bool(conv_username.endswith("@chatroom"))
            for rr in msg_rows:
                local_id = int(rr["local_id"] or 0)
                if local_id <= 0:
                    continue
                try:
                    hit = _row_to_search_hit(
                        rr,
                        db_path=db_path,
                        table_name=table_name,
                        username=conv_username,
                        account_dir=account_dir,
                        is_group=is_group,
                        my_rowid=my_rowid,
                        self_username=db_self_username,
                    )
                except Exception:
                    continue

                hay_items = [
                    str(hit.get("content") or ""),
                    str(hit.get("title") or ""),
                    str(hit.get("url") or ""),
                    str(hit.get("quoteTitle") or ""),
                    str(hit.get("quoteContent") or ""),
                    str(hit.get("amount") or ""),
                ]
                haystack = "\n".join([x for x in hay_items if x.strip()])
                snippet_src = (
                    str(hit.get("content") or "").strip()
                    or str(hit.get("title") or "").strip()
                    or haystack
                )
                key4 = (db_path, table_name, conv_username, local_id)
                hit["snippet"] = _make_snippet(snippet_src, tokens)
                hit_by_key[key4] = hit
        finally:
            msg_conn.close()

    hits: list[dict[str, Any]] = []
    for k in ordered_keys:
        h = hit_by_key.get(k)
        if h is not None:
            hits.append(h)

    scope = "conversation" if username else "global"

    if username:
        system_usernames = [
            str(_extract_chatroom_top_message_metadata(str(x.get("_rawText") or "")).get("operatorUsername") or "").strip()
            for x in hits
            if int(x.get("type") or 0) == 10000
        ]
        uniq_usernames = list(
            dict.fromkeys([username] + [str(x.get("senderUsername") or "") for x in hits] + system_usernames)
        )
        contact_rows = _load_contact_rows(contact_db_path, uniq_usernames)
        local_avatar_usernames = _query_head_image_usernames(head_image_db_path, uniq_usernames)


        conv_row = contact_rows.get(username)
        conv_name = _pick_display_name(conv_row, username)
        conv_avatar = base_url + _avatar_url_unified(
            account_dir=account_dir,
            username=username,
            local_avatar_usernames=local_avatar_usernames,
        )
        group_nicknames = _load_group_nickname_map(
            account_dir=account_dir,
            contact_db_path=contact_db_path,
            chatroom_id=username,
            sender_usernames=[str(x.get("senderUsername") or "") for x in hits],

        )

        for h in hits:
            su = str(h.get("senderUsername") or "").strip()
            h["conversationName"] = conv_name
            h["conversationAvatar"] = conv_avatar
            if su:
                h["senderDisplayName"] = _resolve_sender_display_name(
                    sender_username=su,
                    sender_contact_rows=contact_rows,

                    group_nicknames=group_nicknames,
                )
                avatar_url = base_url + _avatar_url_unified(
                    account_dir=account_dir,
                    username=su,
                    local_avatar_usernames=local_avatar_usernames,
                )
                h["senderAvatar"] = avatar_url
            _postprocess_special_message_content(
                message=h,
                sender_contact_rows=contact_rows,

            )
    else:
        system_usernames = [
            str(_extract_chatroom_top_message_metadata(str(x.get("_rawText") or "")).get("operatorUsername") or "").strip()
            for x in hits
            if int(x.get("type") or 0) == 10000
        ]
        uniq_contacts = list(
            dict.fromkeys(
                [str(x.get("username") or "") for x in hits]
                + [str(x.get("senderUsername") or "") for x in hits]
                + system_usernames
            )
        )
        contact_rows = _load_contact_rows(contact_db_path, uniq_contacts)
        local_avatar_usernames = _query_head_image_usernames(head_image_db_path, uniq_contacts)


        group_senders_by_room: dict[str, list[str]] = {}
        for h in hits:
            cu = str(h.get("username") or "").strip()
            su = str(h.get("senderUsername") or "").strip()
            if (not cu.endswith("@chatroom")) or (not su):
                continue
            group_senders_by_room.setdefault(cu, []).append(su)

        group_nickname_cache: dict[str, dict[str, str]] = {}
        for cu, senders in group_senders_by_room.items():
            group_nickname_cache[cu] = _load_group_nickname_map(
                account_dir=account_dir,
                contact_db_path=contact_db_path,
                chatroom_id=cu,
                sender_usernames=senders,

            )

        for h in hits:
            cu = str(h.get("username") or "").strip()
            su = str(h.get("senderUsername") or "").strip()
            crow = contact_rows.get(cu)
            conv_name = _pick_display_name(crow, cu) if cu else ""
            h["conversationName"] = conv_name or cu
            conv_avatar = base_url + _avatar_url_unified(
                account_dir=account_dir,
                username=cu,
                local_avatar_usernames=local_avatar_usernames,
            )
            h["conversationAvatar"] = conv_avatar
            if su:
                h["senderDisplayName"] = _resolve_sender_display_name(
                    sender_username=su,
                    sender_contact_rows=contact_rows,

                    group_nicknames=group_nickname_cache.get(cu, {}),
                )
                avatar_url = base_url + _avatar_url_unified(
                    account_dir=account_dir,
                    username=su,
                    local_avatar_usernames=local_avatar_usernames,
                )
                h["senderAvatar"] = avatar_url
            _postprocess_special_message_content(
                message=h,
                sender_contact_rows=contact_rows,

            )

    rev_map = get_revoked_messages_map(account_dir, username)
    for h in hits:
        # The archive is authoritative even when an older index payload still
        # carries a revoked flag or the current confirmed set is empty.
        h["isRevoked"] = False
        h.pop("revokeTime", None)
        if int(h.get("type") or 0) == 10000 or h.get("renderType") == "system":
            continue
        sid = str(h.get("serverIdStr") or h.get("serverId") or "").strip()
        lid = int(h.get("localId") or 0)
        conv = str(h.get("conversationUsername") or h.get("username") or username or "").strip()
        m = None
        if sid and sid != "0":
            m = rev_map.get(f"server:{sid}")
        elif lid > 0 and conv:
            source_db, source_table = str(h.get("db") or ""), str(h.get("table") or "")
            if source_db and source_table:
                m = rev_map.get(f"local:{conv}:{source_db}:{source_table}:{lid}")
            elif not source_db and not source_table:
                m = rev_map.get(f"local:{conv}:{lid}")
            if m is not None and str(m.get("server_id") or "0").strip() != "0":
                m = None
        if m is not None:
            h["isRevoked"] = True
            h["revokeTime"] = int(m.get("revoke_time") or h.get("createTime") or 0)

    response = {
        "status": "success",
        "account": account_dir.name,
        "scope": scope,
        "username": username,
        "q": q,
        "tokens": tokens,
        "offset": int(offset),
        "limit": int(limit),
        "baseUrl": base_url,
        "total": int(total),
        "totalExact": False,
        "hasMore": bool(has_more_index),
        "index": index,
        "indexQueryMode": index_query_mode,
        "hits": hits,
    }
    logger.info(
        "[%s] chat search done account=%s scope=%s username=%s sender=%s total=%s hits=%s has_more=%s rows=%s",
        trace_id,
        account_dir.name,
        scope,
        str(username or "").strip(),
        str(sender or "").strip(),
        int(total),
        len(hits),
        bool(response["hasMore"]),
        len(rows),
    )
    return response



@router.get("/api/chat/search", summary="搜索聊天记录（消息）")
async def search_chat_messages(request: Request, q: str, account: Optional[str]=None, username: Optional[str]=None, sender: Optional[str]=None, session_type: Optional[str]=None, limit: int=50, offset: int=0, start_time: Optional[int]=None, end_time: Optional[int]=None, render_types: Optional[str]=None, include_hidden: bool=False, include_official: bool=False, source: Optional[str]=None, session_limit: int=200, per_chat_scan: int=200, scan_limit: int=20000, retrieval_mode: str='keyword', search_ticket: Optional[str]=None, strict_paging: bool=False):
    source_requested = _normalize_chat_source(source)

    if retrieval_mode not in {'keyword', 'hybrid'}:
        raise HTTPException(400, '不支持的检索方式')
    if not str(q).strip():
        if retrieval_mode == 'hybrid':
            raise HTTPException(400, '智能搜索需要关键词；仅按类型筛选请使用关键词模式')
        types = {part.strip() for part in str(render_types or '').split(',') if part.strip()}
        if not types:
            raise HTTPException(400, '请指定关键词或消息类型')
        if not types <= {'text', 'image', 'emoji', 'video', 'voice', 'file', 'link', 'quote',
                         'chatHistory', 'transfer', 'redPacket', 'location', 'voip', 'system'}:
            raise HTTPException(400, '不支持的消息类型')
    requested_mode = retrieval_mode
    if limit <= 0 or (strict_paging and offset < 0):
        raise HTTPException(400, '无效的检索分页参数')
    limit = min(limit, 200)
    offset = max(offset, 0)
    if strict_paging and search_ticket and retrieval_mode != 'hybrid':
        raise HTTPException(409, {'code': 'search_ticket_invalid', 'message': '检索方式已改变，请重新开始搜索；已取得证据仍保留'})
    requested_hybrid = retrieval_mode == 'hybrid'
    if requested_hybrid:
        from ..local_search.service import get_local_search
        local_config = get_local_search().config(_resolve_account_dir(account).name)
        if not strict_paging and (not local_config['enabled'] or not local_config.get('active')):
            retrieval_mode = 'keyword'

    # 严格续页只使用票据候选；基础全文索引的重建不能使已取得的候选失效。
    if strict_paging and requested_hybrid and (offset or search_ticket):
        response = {}
    elif retrieval_mode == 'hybrid' and _make_search_tokens(q) and not _build_fts_query(q):
        # 基础 FTS 的查询语法不能表达纯引号；派生 FTS 保留这些字符并独立复核原文。
        response = {'hits': [], 'total': 0, 'baseKeywordCoverage': {
            'status': 'unsupported_query', 'message': '基础全文索引不支持该查询，字面匹配来自已升级的本地语义索引'}}
    else:
        response = await _search_chat_messages_via_fts(
            request, q=q, account=account, username=username, sender=sender, session_type=session_type,
            limit=200 if retrieval_mode == 'hybrid' else limit,
            offset=0 if retrieval_mode == 'hybrid' else offset,
            start_time=start_time, end_time=end_time, render_types=render_types,
            include_hidden=include_hidden, include_official=include_official, source=source_requested)
    if strict_paging and response.get('status') in {'index_building', 'index_error'}:
        raise HTTPException(409 if response['status'] == 'index_building' else 503,
                            {'code': response['status'], 'message': response['message']})
    if isinstance(response, dict):
        response.update(requestedRetrievalMode=requested_mode, retrievalMode='keyword')
        if source_requested in {"auto", "decrypted"}:
            response.setdefault("source", "decrypted_index")
            response.setdefault(
                "freshness",
                {
                    "kind": "snapshot",
                    "latestRealtimeIncluded": False,
                    "message": "Chat search index is built from the local decrypted SQLite snapshot.",
                },
            )
    if retrieval_mode == 'hybrid':
        from ..local_search.service import get_local_search, RetrievalFailure
        from ..chat_export_service import get_chat_export_targets_preview
        account_dir = _resolve_account_dir(account)
        account_id = account_dir.name
        targets = await account_to_thread(account_dir, get_chat_export_targets_preview, account=account_id,
            include_hidden=include_hidden, include_official=include_official)
        usernames = [x['username'] for x in targets['targets'] if not username or x['username'] == username]
        if session_type == 'group': usernames = [u for u in usernames if u.endswith('@chatroom')]
        elif session_type == 'single': usernames = [u for u in usernames if not u.endswith('@chatroom')]
        try:
            combined = await get_local_search().hybrid(account_id, response, q, usernames, start_time, end_time,
                sender, render_types.split(',') if render_types else None, offset, limit, search_ticket,
                strict_paging=strict_paging, paging_context=(source_requested, session_type, include_hidden, include_official))
        except RetrievalFailure as error:
            raise HTTPException(error.status_code, {'code': error.code, 'message': str(error),
                                                    'coverage': error.coverage}) from error
        if combined.get('retrievalMode') == 'keyword':
            # 降级后恢复原关键词分页，不能在仅有 200 条的召回窗口中继续切片。
            fallback = await _search_chat_messages_via_fts(request, q=q, account=account, username=username,
                sender=sender, session_type=session_type, limit=limit, offset=offset,
                start_time=start_time, end_time=end_time, render_types=render_types,
                include_hidden=include_hidden, include_official=include_official,
                source=source_requested)
            combined = {**fallback, 'retrievalMode': 'keyword', 'coverage': combined['coverage']}
        return {**combined, 'requestedRetrievalMode': requested_mode}
    if requested_hybrid and isinstance(response,dict):
        response = {**response, 'retrievalMode':'keyword','coverage':{'message':'本地语义检索尚未就绪，当前展示关键词结果'}}
    return response



@router.get("/api/chat/messages/around", summary="定位到某条消息并返回上下文")
async def get_chat_messages_around(
    request: Request,
    username: str,
    anchor_id: str,
    account: Optional[str] = None,
    before: int = 20,
    after: int = 20,
    source: Optional[str] = None,
):
    if not username:
        raise HTTPException(status_code=400, detail="Missing username.")
    if not anchor_id:
        raise HTTPException(status_code=400, detail="Missing anchor_id.")

    if before < 0:
        before = 0
    if after < 0:
        after = 0
    if before > 200:
        before = 200
    if after > 200:
        after = 200

    trace_id = f"msg-around-{int(time.time() * 1000)}-{threading.get_ident()}"
    logger.info(
        "[%s] chat messages around start account=%s username=%s anchor_id=%s before=%s after=%s",
        trace_id,
        str(account or "").strip(),
        str(username or "").strip(),
        str(anchor_id or "").strip(),
        int(before),
        int(after),
    )

    try:
        anchor_db_stem, anchor_table_name_in, anchor_local_id = _parse_message_anchor_local_id(anchor_id)
    except HTTPException:
        logger.warning("[%s] chat messages around invalid anchor format anchor_id=%s", trace_id, str(anchor_id or "").strip())
        raise

    account_dir = _resolve_account_dir(account)
    database_dir = resolve_account_database_dir(account_dir)
    snapshot_generation = database_dir.parents[1].name if database_dir != account_dir else "legacy"
    contact_db_path = resolve_account_database_dir(account_dir) / "contact.db"
    head_image_db_path = resolve_account_database_dir(account_dir) / "head_image.db"
    message_resource_db_path = resolve_account_database_dir(account_dir) / "message_resource.db"
    base_url = str(request.base_url).rstrip("/")
    source_requested = _normalize_chat_source(source)
    source_norm = _normalize_chat_source(source_requested)


    db_paths = _iter_message_db_paths(account_dir)

    anchor_db_path: Optional[Path] = None
    for p in db_paths:
        if p.stem == anchor_db_stem:
            anchor_db_path = p
            break
    if anchor_db_path is None:
        logger.warning(
            "[%s] chat messages around anchor db missing account=%s username=%s anchor_db=%s",
            trace_id,
            account_dir.name,
            username,
            anchor_db_stem,
        )
        raise HTTPException(status_code=404, detail="Anchor database not found.")

    # Open resource DB once (optional), and reuse for all message DBs.
    resource_conn: Optional[sqlite3.Connection] = None
    resource_chat_id: Optional[int] = None
    try:
        if message_resource_db_path.exists():
            resource_conn = sqlite3.connect(str(message_resource_db_path))
            resource_conn.row_factory = sqlite3.Row
            resource_chat_id = _resource_lookup_chat_id(resource_conn, username)
    except Exception:
        if resource_conn is not None:
            try:
                resource_conn.close()
            except Exception:
                pass
        resource_conn = None
        resource_chat_id = None

    # Resolve anchor message tuple from its DB.
    anchor_ct = 0
    anchor_ss = 0
    anchor_table_name = str(anchor_table_name_in or "").strip()
    anchor_row: Optional[sqlite3.Row] = None
    anchor_packed_select = "NULL AS packed_info_data, "
    anchor_source_select = "NULL AS msg_source, "
    try:
        conn_a = sqlite3.connect(str(anchor_db_path))
        conn_a.row_factory = sqlite3.Row
        try:
            if not anchor_table_name:
                try:
                    anchor_table_name = _resolve_msg_table_name(conn_a, username) or ""
                except Exception:
                    anchor_table_name = ""
            if not anchor_table_name:
                raise HTTPException(status_code=404, detail="Anchor table not found.")

            # Normalize table name casing if needed
            try:
                trows = conn_a.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                lower_to_actual = {str(x[0]).lower(): str(x[0]) for x in trows if x and x[0]}
                anchor_table_name = lower_to_actual.get(anchor_table_name.lower(), anchor_table_name)
            except Exception:
                pass

            quoted_table_a = _quote_ident(anchor_table_name)
            has_packed_info_data = False
            has_msg_source = False
            try:
                cols = conn_a.execute(f"PRAGMA table_info({quoted_table_a})").fetchall()
                col_names = {str(c[1] or "").strip().lower() for c in cols}
                has_packed_info_data = "packed_info_data" in col_names
                has_msg_source = "source" in col_names
            except Exception:
                has_packed_info_data = False
                has_msg_source = False
            anchor_packed_select = (
                "m.packed_info_data AS packed_info_data, " if has_packed_info_data else "NULL AS packed_info_data, "
            )
            anchor_source_select = "m.source AS msg_source, " if has_msg_source else "NULL AS msg_source, "

            sql_anchor_with_join = (
                "SELECT "
                "m.local_id, m.server_id, m.local_type, m.sort_seq, m.real_sender_id, m.create_time, "
                "m.message_content, m.compress_content, "
                + anchor_packed_select
                + anchor_source_select
                + "n.user_name AS sender_username "
                f"FROM {quoted_table_a} m "
                "LEFT JOIN Name2Id n ON m.real_sender_id = n.rowid "
                "WHERE m.local_id = ? "
                "LIMIT 1"
            )
            sql_anchor_no_join = (
                "SELECT "
                "m.local_id, m.server_id, m.local_type, m.sort_seq, m.real_sender_id, m.create_time, "
                "m.message_content, m.compress_content, "
                + anchor_packed_select
                + anchor_source_select
                + "'' AS sender_username "
                f"FROM {quoted_table_a} m "
                "WHERE m.local_id = ? "
                "LIMIT 1"
            )

            conn_a.text_factory = bytes
            try:
                anchor_row = conn_a.execute(sql_anchor_with_join, (anchor_local_id,)).fetchone()
            except Exception:
                anchor_row = conn_a.execute(sql_anchor_no_join, (anchor_local_id,)).fetchone()

            if anchor_row is None:
                raise HTTPException(status_code=404, detail="Anchor message not found.")

            anchor_ct = int(anchor_row["create_time"] or 0)
            anchor_ss = int(anchor_row["sort_seq"] or 0) if anchor_row["sort_seq"] is not None else 0
        finally:
            conn_a.close()
    finally:
        pass

    anchor_id_canon = f"{anchor_db_stem}:{anchor_table_name}:{anchor_local_id}"

    merged: list[dict[str, Any]] = []
    sender_usernames_all: list[str] = []
    quote_usernames_all: list[str] = []
    pat_usernames_all: set[str] = set()
    is_group = bool(username.endswith("@chatroom"))
    self_username = resolve_account_username(account_dir)

    for db_path in db_paths:
        conn: Optional[sqlite3.Connection] = None
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row

            table_name = ""
            if db_path.stem == anchor_db_stem:
                table_name = anchor_table_name
            else:
                try:
                    table_name = _resolve_msg_table_name(conn, username) or ""
                except Exception:
                    table_name = ""
            if not table_name:
                continue

            my_rowid, _matched_self_username = _resolve_message_self_rowid(
                conn,
                account_dir,
            )
            db_self_username = _matched_self_username or self_username

            quoted_table = _quote_ident(table_name)
            has_packed_info_data = False
            has_msg_source = False
            try:
                cols = conn.execute(f"PRAGMA table_info({quoted_table})").fetchall()
                col_names = {str(c[1] or "").strip().lower() for c in cols}
                has_packed_info_data = "packed_info_data" in col_names
                has_msg_source = "source" in col_names
            except Exception:
                has_packed_info_data = False
                has_msg_source = False
            packed_select = (
                "m.packed_info_data AS packed_info_data, " if has_packed_info_data else "NULL AS packed_info_data, "
            )
            source_select = "m.source AS msg_source, " if has_msg_source else "NULL AS msg_source, "

            # Stable cross-db ordering: (create_time, sort_seq, db_stem, local_id)
            stem = db_path.stem
            if stem < anchor_db_stem:
                tie_before = "1"
                tie_before_params: tuple[Any, ...] = ()
                tie_after = "0"
                tie_after_params: tuple[Any, ...] = ()
            elif stem > anchor_db_stem:
                tie_before = "0"
                tie_before_params = ()
                tie_after = "1"
                tie_after_params = ()
            else:
                tie_before = "m.local_id < ?"
                tie_before_params = (int(anchor_local_id),)
                tie_after = "m.local_id > ?"
                tie_after_params = (int(anchor_local_id),)

            where_before = (
                "WHERE ("
                "m.create_time < ? "
                "OR (m.create_time = ? AND COALESCE(m.sort_seq, 0) < ?) "
                f"OR (m.create_time = ? AND COALESCE(m.sort_seq, 0) = ? AND {tie_before})"
                ")"
            )
            where_after = (
                "WHERE ("
                "m.create_time > ? "
                "OR (m.create_time = ? AND COALESCE(m.sort_seq, 0) > ?) "
                f"OR (m.create_time = ? AND COALESCE(m.sort_seq, 0) = ? AND {tie_after})"
                ")"
            )

            sql_before_with_join = (
                "SELECT "
                "m.local_id, m.server_id, m.local_type, m.sort_seq, m.real_sender_id, m.create_time, "
                "m.message_content, m.compress_content, "
                + packed_select
                + source_select
                + "n.user_name AS sender_username "
                f"FROM {quoted_table} m "
                "LEFT JOIN Name2Id n ON m.real_sender_id = n.rowid "
                f"{where_before} "
                "ORDER BY m.create_time DESC, COALESCE(m.sort_seq, 0) DESC, m.local_id DESC "
                "LIMIT ?"
            )
            sql_before_no_join = (
                "SELECT "
                "m.local_id, m.server_id, m.local_type, m.sort_seq, m.real_sender_id, m.create_time, "
                "m.message_content, m.compress_content, "
                + packed_select
                + source_select
                + "'' AS sender_username "
                f"FROM {quoted_table} m "
                f"{where_before} "
                "ORDER BY m.create_time DESC, COALESCE(m.sort_seq, 0) DESC, m.local_id DESC "
                "LIMIT ?"
            )

            sql_after_with_join = (
                "SELECT "
                "m.local_id, m.server_id, m.local_type, m.sort_seq, m.real_sender_id, m.create_time, "
                "m.message_content, m.compress_content, "
                + packed_select
                + source_select
                + "n.user_name AS sender_username "
                f"FROM {quoted_table} m "
                "LEFT JOIN Name2Id n ON m.real_sender_id = n.rowid "
                f"{where_after} "
                "ORDER BY m.create_time ASC, COALESCE(m.sort_seq, 0) ASC, m.local_id ASC "
                "LIMIT ?"
            )
            sql_after_no_join = (
                "SELECT "
                "m.local_id, m.server_id, m.local_type, m.sort_seq, m.real_sender_id, m.create_time, "
                "m.message_content, m.compress_content, "
                + packed_select
                + source_select
                + "'' AS sender_username "
                f"FROM {quoted_table} m "
                f"{where_after} "
                "ORDER BY m.create_time ASC, COALESCE(m.sort_seq, 0) ASC, m.local_id ASC "
                "LIMIT ?"
            )

            # Always fetch anchor row from anchor DB, but don't include anchor itself in before/after queries.
            anchor_rows: list[sqlite3.Row] = []
            if db_path.stem == anchor_db_stem:
                if anchor_row is None:
                    raise HTTPException(status_code=404, detail="Anchor message not found.")
                anchor_rows = [anchor_row]

            conn.text_factory = bytes

            before_rows: list[sqlite3.Row] = []
            if int(before) > 0:
                params_before = (
                    int(anchor_ct),
                    int(anchor_ct),
                    int(anchor_ss),
                    int(anchor_ct),
                    int(anchor_ss),
                    *tie_before_params,
                    int(before) + 1,
                )
                try:
                    before_rows = conn.execute(sql_before_with_join, params_before).fetchall()
                except Exception:
                    before_rows = conn.execute(sql_before_no_join, params_before).fetchall()

            after_rows: list[sqlite3.Row] = []
            if int(after) > 0:
                params_after = (
                    int(anchor_ct),
                    int(anchor_ct),
                    int(anchor_ss),
                    int(anchor_ct),
                    int(anchor_ss),
                    *tie_after_params,
                    int(after) + 1,
                )
                try:
                    after_rows = conn.execute(sql_after_with_join, params_after).fetchall()
                except Exception:
                    after_rows = conn.execute(sql_after_no_join, params_after).fetchall()

            # Dedup rows by message id within this DB.
            seen_ids: set[str] = set()
            combined: list[sqlite3.Row] = []
            for rr in list(before_rows) + list(anchor_rows) + list(after_rows):
                lid = int(rr["local_id"] or 0)
                mid = f"{db_path.stem}:{table_name}:{lid}"
                if mid in seen_ids:
                    continue
                seen_ids.add(mid)
                combined.append(rr)

            if not combined:
                continue

            _append_full_messages_from_rows(
                merged=merged,
                sender_usernames=sender_usernames_all,
                quote_usernames=quote_usernames_all,
                pat_usernames=pat_usernames_all,
                rows=combined,
                db_path=db_path,
                table_name=table_name,
                username=username,
                account_dir=account_dir,
                is_group=is_group,
                my_rowid=my_rowid,
                resource_conn=resource_conn,
                resource_chat_id=resource_chat_id,
                self_username=db_self_username,
            )
        except HTTPException:
            raise
        except Exception:
            # Skip broken DBs / missing tables gracefully.
            continue
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    if resource_conn is not None:
        try:
            resource_conn.close()
        except Exception:
            pass

    # Global dedupe + sort.
    if merged:
        seen_ids2: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for m in merged:
            mid = str(m.get("id") or "").strip()
            if mid and mid in seen_ids2:
                continue
            if mid:
                seen_ids2.add(mid)
            deduped.append(m)
        merged = deduped

    def sort_key_global(m: dict[str, Any]) -> tuple[int, int, str, int]:
        cts = int(m.get("createTime") or 0)
        sseq = int(m.get("sortSeq") or 0)
        lid = int(m.get("localId") or 0)
        mid = str(m.get("id") or "")
        stem2 = ""
        try:
            stem2 = mid.split(":", 1)[0] if ":" in mid else ""
        except Exception:
            stem2 = ""
        return (cts, sseq, stem2, lid)

    merged.sort(key=sort_key_global, reverse=False)

    anchor_index_all = -1
    for i, m in enumerate(merged):
        if str(m.get("id") or "") == str(anchor_id_canon):
            anchor_index_all = i
            break
    if anchor_index_all < 0:
        # Fallback: ignore table casing differences when matching anchor.
        for i, m in enumerate(merged):
            mid = str(m.get("id") or "")
            p2 = mid.split(":", 2)
            if len(p2) != 3:
                continue
            if p2[0] != anchor_db_stem:
                continue
            try:
                if int(p2[2] or 0) == int(anchor_local_id):
                    anchor_index_all = i
                    break
            except Exception:
                continue

    if anchor_index_all < 0:
        # Should not happen because we always include the anchor row, but keep defensive.
        anchor_index_all = 0

    start = max(0, int(anchor_index_all) - int(before))
    end = min(len(merged), int(anchor_index_all) + int(after) + 1)
    return_messages = merged[start:end]
    anchor_index = int(anchor_index_all) - start if 0 <= anchor_index_all < len(merged) else -1

    # Postprocess only the returned window to keep it fast.
    sender_usernames_win = [str(m.get("senderUsername") or "").strip() for m in return_messages if str(m.get("senderUsername") or "").strip()]
    quote_usernames_win = [str(m.get("quoteUsername") or "").strip() for m in return_messages if str(m.get("quoteUsername") or "").strip()]
    pat_usernames_win: set[str] = set()
    try:
        for m in return_messages:
            if int(m.get("type") or 0) != 266287972401:
                continue
            raw = str(m.get("_rawText") or "")
            if not raw:
                continue
            template = _extract_xml_tag_text(raw, "template")
            if not template:
                continue
            pat_usernames_win.update({mm.group(1) for mm in re.finditer(r"\$\{([^}]+)\}", template) if mm.group(1)})
    except Exception:
        pat_usernames_win = set()

    _postprocess_full_messages(
        merged=return_messages,
        sender_usernames=sender_usernames_win,
        quote_usernames=quote_usernames_win,
        pat_usernames=pat_usernames_win,
        account_dir=account_dir,
        username=username,
        base_url=base_url,
        contact_db_path=contact_db_path,
        head_image_db_path=head_image_db_path,
    )

    logger.info(
        "[%s] chat messages around done account=%s username=%s anchor_id=%s canonical_anchor=%s anchor_index=%s returned=%s merged_total=%s",
        trace_id,
        account_dir.name,
        username,
        str(anchor_id or "").strip(),
        anchor_id_canon,
        int(anchor_index),
        len(return_messages),
        len(merged),
    )

    return {
        "snapshotGeneration": snapshot_generation,
        "status": "success",
        "account": account_dir.name,
        "username": username,
        "source": source_norm,

        "anchorId": anchor_id_canon,
        "anchorIndex": anchor_index,
        "messages": return_messages,
    }


@router.get("/api/chat/chat_history/resolve", summary="解析嵌套合并转发聊天记录（通过 server_id）")
async def resolve_nested_chat_history(
    request: Request,
    server_id: int,
    account: Optional[str] = None,
):
    """Resolve a nested merged-forward chat history item (datatype=17) to its full recordItem XML.

    Some nested records inside a merged-forward recordItem only carry pointers like `fromnewmsgid` (server_id),
    while the full recordItem exists in the original app message (local_type=49, appmsg type=19) stored elsewhere.
    WeChat can open it by looking up the original message; we do the same here.
    """
    if not server_id:
        raise HTTPException(status_code=400, detail="Missing server_id.")

    account_dir = _resolve_account_dir(account)
    db_paths = _iter_message_db_paths(account_dir)
    base_url = str(request.base_url).rstrip("/")
    found_appmsg = False

    for db_path in db_paths:
        conn: Optional[sqlite3.Connection] = None
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            conn.text_factory = bytes

            try:
                table_rows = conn.execute(
                    # Some DBs use `Msg_...` (capital M). Use LOWER() to keep matching even if
                    # `PRAGMA case_sensitive_like=ON` is set.
                    "SELECT name FROM sqlite_master WHERE type='table' AND lower(name) LIKE 'msg_%'"
                ).fetchall()
            except Exception:
                table_rows = []

            # With `conn.text_factory = bytes`, sqlite_master.name comes back as bytes.
            # Decode it to the real table name, otherwise we'd end up querying a non-existent
            # table like "b'Msg_...'" and never find the message.
            table_names = [_decode_sqlite_text(r[0]).strip() for r in table_rows if r and r[0]]
            for table_name in table_names:
                quoted = _quote_ident(table_name)
                try:
                    row = conn.execute(
                        f"""
                        SELECT local_id, server_id, local_type, create_time, message_content, compress_content
                        FROM {quoted}
                        -- WeChat v4 can pack appmsg subtype into the high 32 bits of local_type:
                        --   local_type = base_type + (app_subtype << 32)
                        -- so a chatHistory appmsg can be 49 + (19<<32), not exactly 49.
                        WHERE server_id = ? AND (local_type & 4294967295) = 49
                        LIMIT 1
                        """,
                        (int(server_id),),
                    ).fetchone()
                except Exception:
                    row = None

                if row is None:
                    continue

                found_appmsg = True
                raw_text = _decode_message_content(row["compress_content"], row["message_content"]).strip()
                if not raw_text:
                    continue

                # If the stored payload is a zstd frame but we couldn't decode it into XML, it's
                # almost always because the optional `zstandard` dependency isn't installed.
                try:
                    blob = row["message_content"]
                    if isinstance(blob, memoryview):
                        blob = blob.tobytes()
                    if isinstance(blob, (bytes, bytearray)) and bytes(blob).startswith(b"\x28\xb5\x2f\xfd"):
                        lower = raw_text.lower()
                        if "<appmsg" not in lower and "<msg" not in lower:
                            raise HTTPException(
                                status_code=500,
                                detail="Failed to decode zstd-compressed message_content. Please install `zstandard` and restart the backend.",
                            )
                except HTTPException:
                    raise
                except Exception:
                    pass

                parsed = _parse_app_message(raw_text)
                if not isinstance(parsed, dict):
                    continue

                if str(parsed.get("renderType") or "") != "chatHistory":
                    # Found an app message, but not a merged-forward chat history.
                    continue

                record_item = str(parsed.get("recordItem") or "").strip()
                if not record_item:
                    continue

                return {
                    "status": "success",
                    "serverId": int(server_id),
                    "title": str(parsed.get("title") or "").strip(),
                    "content": str(parsed.get("content") or "").strip(),
                    "recordItem": record_item,
                    "baseUrl": base_url,
                }
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    if found_appmsg:
        raise HTTPException(status_code=404, detail="Target message is not a chat history.")
    raise HTTPException(status_code=404, detail="Message not found for server_id.")


@router.get("/api/chat/appmsg/resolve", summary="解析卡片/小程序等 App 消息（通过 server_id）")
async def resolve_app_message(
    request: Request,
    server_id: int,
    account: Optional[str] = None,
):
    """Resolve an app message (base local_type=49) by server_id.

    This is mainly used by merged-forward recordItem dataitems that only contain pointers like
    `fromnewmsgid` (server_id). WeChat can open the original card by looking up the appmsg in
    message DBs; we do the same and return the parsed appmsg fields.
    """
    if not server_id:
        raise HTTPException(status_code=400, detail="Missing server_id.")

    account_dir = _resolve_account_dir(account)
    db_paths = _iter_message_db_paths(account_dir)
    base_url = str(request.base_url).rstrip("/")

    found_appmsg = False
    for db_path in db_paths:
        conn: Optional[sqlite3.Connection] = None
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            conn.text_factory = bytes

            try:
                table_rows = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND lower(name) LIKE 'msg_%'"
                ).fetchall()
            except Exception:
                table_rows = []

            table_names = [_decode_sqlite_text(r[0]).strip() for r in table_rows if r and r[0]]
            for table_name in table_names:
                quoted = _quote_ident(table_name)
                try:
                    row = conn.execute(
                        f"""
                        SELECT local_id, server_id, local_type, create_time, message_content, compress_content
                        FROM {quoted}
                        WHERE server_id = ? AND (local_type & 4294967295) = 49
                        LIMIT 1
                        """,
                        (int(server_id),),
                    ).fetchone()
                except Exception:
                    row = None

                if row is None:
                    continue

                found_appmsg = True
                raw_text = _decode_message_content(row["compress_content"], row["message_content"]).strip()
                if not raw_text:
                    continue

                # Same zstd guard as chat_history/resolve.
                try:
                    blob = row["message_content"]
                    if isinstance(blob, memoryview):
                        blob = blob.tobytes()
                    if isinstance(blob, (bytes, bytearray)) and bytes(blob).startswith(b"\x28\xb5\x2f\xfd"):
                        lower = raw_text.lower()
                        if "<appmsg" not in lower and "<msg" not in lower:
                            raise HTTPException(
                                status_code=500,
                                detail="Failed to decode zstd-compressed message_content. Please install `zstandard` and restart the backend.",
                            )
                except HTTPException:
                    raise
                except Exception:
                    pass

                parsed = _parse_app_message(raw_text)
                if not isinstance(parsed, dict):
                    continue

                # Return a stable, explicit shape for the frontend.
                return {
                    "status": "success",
                    "serverId": int(server_id),
                    "renderType": str(parsed.get("renderType") or "text"),
                    "title": str(parsed.get("title") or "").strip(),
                    "content": str(parsed.get("content") or "").strip(),
                    "url": str(parsed.get("url") or "").strip(),
                    "thumbUrl": str(parsed.get("thumbUrl") or "").strip(),
                    "coverUrl": str(parsed.get("coverUrl") or "").strip(),
                    "from": str(parsed.get("from") or "").strip(),
                    "fromUsername": str(parsed.get("fromUsername") or "").strip(),
                    "linkType": str(parsed.get("linkType") or "").strip(),
                    "linkStyle": str(parsed.get("linkStyle") or "").strip(),
                    "objectId": str(parsed.get("objectId") or "").strip(),
                    "objectNonceId": str(parsed.get("objectNonceId") or "").strip(),
                    "size": str(parsed.get("size") or "").strip(),
                    "baseUrl": base_url,
                }
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    if found_appmsg:
        raise HTTPException(status_code=404, detail="App message decode failed.")
    raise HTTPException(status_code=404, detail="Message not found for server_id.")


def _normalize_table_name_case(conn: sqlite3.Connection, table_name: str) -> str:
    t = str(table_name or "").strip()
    if not t:
        return ""
    try:
        r = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND lower(name)=lower(?) LIMIT 1",
            (t,),
        ).fetchone()
        if r is not None and r[0]:
            # With `conn.text_factory = bytes`, sqlite_master.name can be returned as bytes.
            # Decode it to avoid querying a non-existent table like "b'Msg_...'".
            return _decode_sqlite_text(r[0]).strip()
    except Exception:
        pass
    return t




@router.get("/api/chat/messages/raw", summary="获取单条消息原始字段（output 解密库）")
def get_chat_message_raw(
    *,
    account: Optional[str] = None,
    username: str,
    message_id: str,
) -> dict[str, Any]:
    if not username:
        raise HTTPException(status_code=400, detail="Missing username.")
    if not message_id:
        raise HTTPException(status_code=400, detail="Missing message_id.")

    account_dir = _resolve_account_dir(account)
    parts = str(message_id or "").split(":", 2)
    try:
        if len(parts) != 3:
            raise ValueError
        db_stem = str(parts[0] or "").strip()
        table_name_in = str(parts[1] or "").strip()
        local_id = int(parts[2] or 0)
        if not db_stem or not table_name_in or local_id <= 0:
            raise ValueError
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid message_id.")

    db_path = resolve_account_database_dir(account_dir) / f"{db_stem}.db"
    if not db_path.exists():
        raise HTTPException(status_code=404, detail="Message database not found.")

    conn: Optional[sqlite3.Connection] = None
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.text_factory = bytes
        table_name = _normalize_table_name_case(conn, table_name_in)
        if not table_name:
            raise HTTPException(status_code=404, detail="Message table not found.")

        quoted_table = _quote_ident(table_name)
        row = conn.execute(f"SELECT * FROM {quoted_table} WHERE local_id = ? LIMIT 1", (int(local_id),)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Message not found.")

        out_row: dict[str, Any] = {}
        for k in row.keys():
            out_row[str(k)] = _jsonify_db_value(str(k), row[k])

        return {
            "status": "success",
            "account": account_dir.name,
            "username": username,
            "messageId": f"{db_stem}:{table_name}:{int(local_id)}",
            "row": out_row,
        }
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
