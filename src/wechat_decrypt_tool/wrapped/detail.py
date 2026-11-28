"""Local, source-addressable evidence for the annual report."""
from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import defaultdict
from contextlib import ExitStack, closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..chat_helpers import (
    _decode_message_content, _extract_xml_attr, _extract_xml_tag_text,
    _iter_message_db_paths, _load_contact_rows, _lookup_resource_md5, _pick_display_name, _quote_ident,
    _resolve_account_dir, _resource_lookup_chat_id, _should_keep_session,
)
from ..chat_search_index import (
    get_chat_search_index_db_path, get_chat_search_index_status, start_chat_search_index_build,
)
from ..snapshot_registry import resolve_account_database_dir
from . import WRAPPED_CONTRACT_VERSION, WrappedIdentityError, require_wrapped_self_rowid, resolve_wrapped_self_username
from .cards.card_01_cyber_schedule import _is_night_companion_session
from .cards.card_04_emoji_universe import (
    _build_local_emoji_url, _extract_packed_emoji_meta, _load_wechat_expression_catalog,
    _extract_unicode_emoji_tokens, _load_wechat_text_emoji_matcher, _normalize_index_text_for_emoji_match,
)
from .cards.card_05_keywords_wordcloud import _list_message_tables, _weflow_common_phrase_or_empty


class WrappedIndexError(RuntimeError):
    """The requested annual evidence cannot be read from a complete index."""


class WrappedDetailInputError(ValueError):
    """Invalid selection at the annual evidence API boundary."""


_TS = "CASE WHEN m.create_time > 1000000000000 THEN CAST(m.create_time/1000 AS INTEGER) ELSE m.create_time END"
_ORDER = f"{_TS} DESC, m.sort_seq DESC, m.local_id DESC, m.db_stem, m.table_name, m.rowid"


def prepare_wrapped_index(account_dir: Path, *, refresh: bool = False) -> dict[str, Any] | None:
    """Return explicit build progress, or None when a complete snapshot is readable."""
    index = get_chat_search_index_status(account_dir, source="auto")["index"]
    if index["ready"] and index["upToDate"]:
        return None
    build = index["build"]
    if build.get("status") == "error" and not refresh:
        raise WrappedIndexError(f"年度消息索引构建失败：{build['error']}")
    if build.get("status") != "building":
        index = start_chat_search_index_build(account_dir, rebuild=True, source="auto")["index"]
    if index["build"].get("status") == "error":
        raise WrappedIndexError(f"年度消息索引构建失败：{index['build']['error']}")
    return {key: index[key] for key in ("ready", "upToDate", "build")}


def _message(row: sqlite3.Row, self_username: str) -> dict[str, Any]:
    payload = json.loads(row["payload_json"])
    if not isinstance(payload, dict) or not isinstance(payload.get("content"), str):
        raise WrappedIndexError(f"年度详情消息正文损坏：rowid={row['rowid']}")
    anchor = {"dbStem": row["db_stem"], "table": row["table_name"], "localId": int(row["local_id"])}
    if not anchor["dbStem"] or not anchor["table"] or anchor["localId"] <= 0:
        raise WrappedIndexError(f"年度详情消息缺少来源定位：rowid={row['rowid']}")
    is_sent = row["sender_username"] == self_username
    if payload.get("isSent") is not is_sent:
        raise WrappedIdentityError(f"年度详情消息发送方向与本人身份冲突：sender={self_username}, rowid={row['rowid']}")
    timestamp = int(row["create_time"])
    if timestamp > 1_000_000_000_000:
        timestamp //= 1000
    return {
        **anchor, "source": anchor, "username": row["username"], "timestamp": timestamp,
        "isSent": is_sent, "text": payload["content"],
        "renderType": row["render_type"],
    }


def _reply_distribution(gaps: list[int]) -> dict[str, Any]:
    gaps.sort()
    count = len(gaps)
    buckets = [
        {"key": "underMinute", "label": "1 分钟内", "count": sum(g < 60 for g in gaps)},
        {"key": "oneToTenMinutes", "label": "1–10 分钟", "count": sum(60 <= g < 600 for g in gaps)},
        {"key": "tenToSixtyMinutes", "label": "10–60 分钟", "count": sum(600 <= g < 3600 for g in gaps)},
        {"key": "overHour", "label": "1 小时及以上", "count": sum(g >= 3600 for g in gaps)},
    ]
    return {
        "count": count, "p50Seconds": gaps[math.ceil(count * .5) - 1] if count else None,
        "p90Seconds": gaps[math.ceil(count * .9) - 1] if count else None, "buckets": buckets,
    }


def _sticker_matches(conn: sqlite3.Connection, *, account_dir: Path, where: str, params: list[Any], md5: str = "", expression_ids: set[int] | None = None) -> None:
    """Resolve only indexed outgoing stickers, using their existing source anchors."""
    sources = {path.stem: path for path in _iter_message_db_paths(account_dir)}
    groups: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in conn.execute(f"SELECT m.* FROM message_meta m WHERE {where}", params):
        groups[(row["db_stem"], row["table_name"])].append(row)
    conn.execute("CREATE TEMP TABLE wrapped_emoji_matches (rowid INTEGER PRIMARY KEY)")
    with ExitStack() as stack:
        resource_path = resolve_account_database_dir(account_dir) / "message_resource.db"
        resource_conn = stack.enter_context(closing(sqlite3.connect(resource_path.as_uri() + "?mode=ro", uri=True))) if resource_path.exists() else None
        connections: dict[str, sqlite3.Connection] = {}
        for (stem, table), rows in groups.items():
            if stem not in sources:
                raise WrappedIndexError(f"年度表情详情的来源库已不可用：account={account_dir.name}, database={stem}")
            if stem not in connections:
                connections[stem] = stack.enter_context(closing(sqlite3.connect(sources[stem].as_uri() + "?mode=ro", uri=True)))
                connections[stem].row_factory = sqlite3.Row
            source = connections[stem]
            columns = {row["name"] for row in source.execute(f"PRAGMA table_info({_quote_ident(table)})")}
            packed_column = "packed_info_data" if "packed_info_data" in columns else "NULL AS packed_info_data"
            for start in range(0, len(rows), 500):
                batch = rows[start:start + 500]
                anchors = {int(row["local_id"]): row for row in batch}
                placeholders = ",".join("?" for _ in anchors)
                records = source.execute(
                    f"SELECT local_id,server_id,create_time,message_content,compress_content,{packed_column} FROM {_quote_ident(table)} WHERE local_id IN ({placeholders})", list(anchors),
                ).fetchall()
                if {int(record["local_id"]) for record in records} != set(anchors):
                    raise WrappedIndexError(f"年度表情详情的来源消息已缺失：account={account_dir.name}, database={stem}, table={table}")
                for record in records:
                    anchor = anchors[int(record["local_id"])]
                    raw = _decode_message_content(record["compress_content"], record["message_content"])
                    sticker_md5 = _extract_xml_attr(raw, "md5") or _extract_xml_tag_text(raw, "md5")
                    # Some WeChat archives store the resource identifier in message_resource.db.
                    if not sticker_md5 and resource_conn is not None:
                        sticker_md5 = _lookup_resource_md5(
                            resource_conn, _resource_lookup_chat_id(resource_conn, anchor["username"]),
                            message_local_type=47, server_id=int(record["server_id"]),
                            local_id=int(record["local_id"]), create_time=int(record["create_time"]),
                        )
                    _, expression_id = _extract_packed_emoji_meta(record["packed_info_data"])
                    if (md5 and sticker_md5 and sticker_md5.lower() == md5) or (not sticker_md5 and expression_ids and expression_id in expression_ids):
                        conn.execute("INSERT INTO wrapped_emoji_matches(rowid) VALUES(?)", (anchor["rowid"],))


def build_wrapped_annual_detail(
    *, account: str, year: int, kind: str, value: str, period: str = "all",
    offset: int = 0, limit: int = 40, refresh: bool = False, month: int | None = None,
) -> dict[str, Any]:
    """Return a page of evidence; never replace a pending/failed index with zero counts."""
    if kind not in {"day", "hour", "phrase", "contact", "night", "emoji"}:
        raise WrappedDetailInputError("年度详情类型无效")
    if not 1970 <= year <= 9998 or not 0 <= offset or not 1 <= limit <= 100:
        raise WrappedDetailInputError("年度详情年份或分页参数无效")
    if period not in {"all", "weekday", "weekend"} or (kind != "hour" and period != "all"):
        raise WrappedDetailInputError("工作日/周末筛选仅用于小时详情")
    if month is not None and (type(month) is not int or not 1 <= month <= 12 or kind not in {"phrase", "contact"}):
        raise WrappedDetailInputError("月份必须为 1–12，且仅用于短句或联系人详情")
    if not value or len(value) > 512:
        raise WrappedDetailInputError("年度详情选择值不能为空或超过 512 字")
    start = datetime(year, 1, 1)
    end = datetime(year + 1, 1, 1)
    if month is not None:
        start = datetime(year, month, 1)
        end = datetime(year, month + 1, 1) if month < 12 else end
    if kind == "day":
        try:
            start = datetime.strptime(value, "%Y-%m-%d")
        except ValueError as exc:
            raise WrappedDetailInputError("所选日期格式无效") from exc
        if start.year != year or start.strftime("%Y-%m-%d") != value:
            raise WrappedDetailInputError("所选日期不属于年度或格式无效")
        end = start + timedelta(days=1)
    if kind == "hour" and (not value.isdecimal() or not 0 <= int(value) < 24):
        raise WrappedDetailInputError("小时必须为 0–23")
    if kind == "phrase" and _weflow_common_phrase_or_empty(value) != value:
        raise WrappedDetailInputError("短句必须为 2–20 字完整文本")
    if kind == "contact" and (value.endswith("@chatroom") or not _should_keep_session(value, include_official=False)):
        raise WrappedDetailInputError("回复详情仅适用于普通单聊联系人")
    if kind == "night" and not _is_night_companion_session(value):
        raise WrappedDetailInputError("深夜详情仅适用于普通微信单聊联系人")
    emoji_md5 = ""
    emoji_text = ""
    expression_ids: set[int] = set()
    expression_assets: dict[int, str] = {}
    if kind == "emoji":
        candidate = value.removeprefix("md5:")
        if re.fullmatch(r"[0-9a-fA-F]{32}", candidate):
            emoji_md5 = candidate.lower()
        elif value.startswith("md5:"):
            raise WrappedDetailInputError("表情 md5 必须为 32 位十六进制文本")
        elif value.startswith("expr:"):
            expression_value = value.removeprefix("expr:")
            if not expression_value.isdecimal() or int(expression_value) <= 0:
                raise WrappedDetailInputError("原生表情 ID 必须为正整数")
            expression_ids = {int(expression_value)}
            expression_assets, _ = _load_wechat_expression_catalog()
        else:
            emoji_text = value.removeprefix("text:")
            if not emoji_text:
                raise WrappedDetailInputError("文字表情不能为空")

    account_dir = _resolve_account_dir(account)
    self_username = resolve_wrapped_self_username(account_dir)
    if not self_username:
        raise WrappedIdentityError(f"年度详情无法确认本人身份：account={account_dir.name}")
    result: dict[str, Any] = {
        "account": account_dir.name, "year": year, "contractVersion": WRAPPED_CONTRACT_VERSION,
        "kind": kind, "value": value, "period": period, "month": month, "offset": offset, "limit": limit,
        "status": "building", "summary": None, "items": [], "total": None, "hasMore": False,
    }
    index = prepare_wrapped_index(account_dir, refresh=refresh)
    if index is not None:
        result["index"] = index
        return result

    # An index can outlive account metadata changes. Prove the selected sender
    # against the same shard identity tables used by the ordinary-text card.
    source_paths = [path for path in _iter_message_db_paths(account_dir) if not path.name.lower().startswith("biz_message")]
    has_source_identity = False
    for source_path in source_paths:
        with closing(sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)) as source_conn:
            if not _list_message_tables(source_conn):
                continue
            require_wrapped_self_rowid(source_conn, account_dir, database=source_path.name)
            has_source_identity = True

    path = get_chat_search_index_db_path(account_dir)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        if not has_source_identity and conn.execute("SELECT 1 FROM message_meta LIMIT 1").fetchone() is not None:
            raise WrappedIdentityError(f"年度详情缺少可验证的本人身份来源：account={account_dir.name}")
        conn.create_function("wrapped_keep_session", 1, lambda username: _should_keep_session(username, include_official=False), deterministic=True)
        # The same phrase normalization as the card: full text, never token/substring matching.
        conn.create_function("wrapped_phrase", 1, _weflow_common_phrase_or_empty, deterministic=True)
        if emoji_text:
            emoji_regex, canonical_keys = _load_wechat_text_emoji_matcher()
            def emoji_occurrences(body: str) -> int:
                normalized = _normalize_index_text_for_emoji_match(body)
                bracket_count = sum(canonical_keys[match.group(0)] == emoji_text for match in emoji_regex.finditer(normalized))
                return bracket_count + _extract_unicode_emoji_tokens(normalized).count(emoji_text)
            conn.create_function("wrapped_emoji_count", 1, emoji_occurrences, deterministic=True)
        # Keep timestamp columns bare so both second and millisecond archives
        # can seek existing B-tree time ranges rather than scanning every year.
        where = "((m.create_time >= ? AND m.create_time < ?) OR (m.create_time > 1000000000000 AND m.create_time >= ? AND m.create_time < ?)) AND m.db_stem NOT LIKE 'biz_message%'"
        start_ts, end_ts = int(start.timestamp()), int(end.timestamp())
        params: list[Any] = [start_ts, end_ts, start_ts * 1000, end_ts * 1000]
        if kind == "day":
            where += " AND wrapped_keep_session(m.username)"
        if kind in {"contact", "night"}:
            where += " AND m.username = ? AND m.local_type != 10000"
            params.append(value)
        if kind == "night":
            where += f" AND CAST(strftime('%H', {_TS}, 'unixepoch', 'localtime') AS INTEGER) < 6"
        unknown_sender = conn.execute(
            f"SELECT m.rowid FROM message_meta m WHERE {where} AND m.local_type != 10000 AND (m.sender_username IS NULL OR m.sender_username = '') LIMIT 1", params,
        ).fetchone()
        if unknown_sender:
            raise WrappedIdentityError(f"年度详情消息缺少发送者身份：account={account_dir.name}, rowid={unknown_sender[0]}")
        if kind in {"hour", "phrase", "emoji"}:
            where += " AND m.sender_username = ?"
            params.append(self_username)
        if kind == "hour":
            where += f" AND CAST(strftime('%H', {_TS}, 'unixepoch', 'localtime') AS INTEGER) = ?"
            params.append(int(value))
            if period != "all":
                where += f" AND strftime('%w', {_TS}, 'unixepoch', 'localtime') {'IN' if period == 'weekend' else 'NOT IN'} ('0','6')"
        source = "message_meta m JOIN message_fts f ON f.rowid=m.rowid"
        if kind == "phrase" or (kind == "emoji" and emoji_text):
            invalid = conn.execute(f"SELECT m.rowid FROM {source} WHERE {where} AND json_type(f.payload_json,'$.content') IS NOT 'text' LIMIT 1", params).fetchone()
            if invalid is not None:
                raise WrappedIndexError(f"年度详情消息正文损坏：rowid={invalid[0]}")
        if kind == "phrase":
            where += " AND m.local_type = 1 AND wrapped_phrase(json_extract(f.payload_json,'$.content')) = ?"
            params.append(value)
        if kind == "emoji":
            if emoji_md5 or expression_ids:
                where += " AND m.local_type = 47"
                _sticker_matches(conn, account_dir=account_dir, where=where, params=params, md5=emoji_md5, expression_ids=expression_ids)
                where += " AND m.rowid IN (SELECT rowid FROM wrapped_emoji_matches)"
            else:
                where += " AND m.render_type = 'text' AND wrapped_emoji_count(json_extract(f.payload_json,'$.content')) > 0"
        count_source = source if kind == "phrase" or (kind == "emoji" and emoji_text) else "message_meta m"
        # A selected contact has only one username. Avoid GROUP BY here: it
        # makes SQLite favor a full contact-history scan over the time ranges.
        grouping = "HAVING COUNT(*) > 0" if kind in {"contact", "night"} else "GROUP BY m.username ORDER BY total DESC,m.username"
        counts = conn.execute(
            f"SELECT m.username, COUNT(*) AS total, SUM(m.sender_username=?) AS sent FROM {count_source} WHERE {where} {grouping}", [self_username, *params],
        ).fetchall()
        conversations = [{"username": row["username"], "messageCount": int(row["total"]), "sent": int(row["sent"]), "received": int(row["total"] - row["sent"])} for row in counts]
        contacts = _load_contact_rows(resolve_account_database_dir(account_dir) / "contact.db", [row["username"] for row in counts])
        for conversation in conversations:
            conversation["displayName"] = _pick_display_name(contacts.get(conversation["username"]), conversation["username"])
        total = sum(row["messageCount"] for row in conversations)
        sent = sum(row["sent"] for row in conversations)
        summary: dict[str, Any] = {"sent": sent, "received": total - sent, "messageCount": total, "conversationCount": len(conversations)}
        if kind == "emoji":
            if emoji_text:
                occurrence_count = conn.execute(f"SELECT SUM(CASE WHEN m.render_type='text' THEN wrapped_emoji_count(json_extract(f.payload_json,'$.content')) ELSE 1 END) FROM {source} WHERE {where}", params).fetchone()[0]
                summary["occurrenceCount"] = int(occurrence_count) if total else 0
            else:
                summary["occurrenceCount"] = total
        if kind == "day":
            summary["conversations"] = conversations
        rows = conn.execute(f"SELECT m.*,f.payload_json FROM {source} WHERE {where} ORDER BY {_ORDER} LIMIT ? OFFSET ?", [*params, limit, offset]).fetchall()
        items = [_message(row, self_username) for row in rows]
        for item in items:
            item["displayName"] = _pick_display_name(contacts.get(item["username"]), item["username"])
        if emoji_md5:
            for item in items:
                item["mediaUrl"] = _build_local_emoji_url(account_name=account_dir.name, md5=emoji_md5, username="", emoji_remote_url="")
        elif expression_ids:
            for item in items:
                if item["renderType"] == "emoji" and len(expression_ids) == 1:
                    asset = expression_assets.get(next(iter(expression_ids)))
                    if asset is not None:
                        item["mediaUrl"] = f"/wxemoji/{asset}"
        if kind == "contact":
            gaps: dict[str, list[int]] = {"me": [], "them": []}
            page_ids = {row["rowid"] for row in rows}
            pair_rows = []
            previous = None
            for row in conn.execute(f"SELECT m.rowid,m.sender_username,{_TS} AS ts FROM message_meta m WHERE {where} ORDER BY {_TS},m.sort_seq,m.local_id,m.db_stem,m.table_name,m.rowid", params):
                if row["sender_username"] not in {self_username, value}:
                    raise WrappedIdentityError(f"单聊出现未知发送者：account={account_dir.name}, rowid={row['rowid']}")
                if previous is not None and previous["sender_username"] != row["sender_username"]:
                    direction = "me" if row["sender_username"] == self_username else "them"
                    seconds = int(row["ts"] - previous["ts"])
                    gaps[direction].append(seconds)
                    if row["rowid"] in page_ids:
                        pair_rows.append((direction, seconds, previous["rowid"], row["rowid"]))
                previous = row
            summary["reply"] = {direction: _reply_distribution(values) for direction, values in gaps.items()}
            scope = "月份" if month is not None else "年份"
            summary["replyRule"] = f"连续同向消息的最后一条 → 反向首条；双方消息均在所选{scope}；按最近秩计算 P50/P90"
            pair_ids = sorted({rowid for _, _, from_id, to_id in pair_rows for rowid in (from_id, to_id)})
            messages = {}
            if pair_ids:
                for row in conn.execute(f"SELECT m.*,f.payload_json FROM {source} WHERE m.rowid IN ({','.join('?' for _ in pair_ids)})", pair_ids):
                    messages[row["rowid"]] = _message(row, self_username)
            result["replyPairs"] = [{"direction": direction, "seconds": seconds, "from": messages[from_id], "to": messages[to_id]} for direction, seconds, from_id, to_id in reversed(pair_rows)]
            result["pairsTotal"] = sum(len(values) for values in gaps.values())
            result["pairsScope"] = "repliesWhoseResponseIsOnMessagePage"
            result["pairsHasMore"] = offset + len(items) < total
        result.update(status="ok", summary=summary, items=items, total=total, hasMore=offset + len(items) < total)
    return result
