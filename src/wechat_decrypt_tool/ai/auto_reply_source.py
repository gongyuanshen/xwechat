"""Bounded automatic-reply reads from the existing verified snapshot service."""
from __future__ import annotations

import asyncio
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time

from .. import chat_helpers as chat
from ..account_identity import resolve_account_self_rowid
from ..account_workers import account_to_thread
from ..app_paths import get_output_dir
from ..independent_snapshot import _source_manifest
from ..snapshot_refresh import SNAPSHOT_REFRESH, SnapshotRefreshError
from ..snapshot_registry import (
    SnapshotRegistryError, require_account_available, resolve_account_database_dir, snapshot_read_scope,
)
from ..wechat_ui_bridge import WeChatBridgeError
from .messages import advance_cursor, iter_message_pages


class AutoReplySourceError(WeChatBridgeError):
    def __init__(self, stage: str, message: str, status_code: int = 409):
        super().__init__(message, code="AUTO_REPLY_SOURCE_" + stage.upper(), status_code=status_code)
        self.stage = stage


class AutoReplySource:
    def __init__(self, timeout_s: float = 10):
        self.timeout_s = timeout_s

    @staticmethod
    def _status(status, account):
        if status.get("error"):
            error = status["error"]
            raise AutoReplySourceError("refresh", f"自动回复快照同步失败：{error['stage']} · {error['message']}")
        if status["active_account"] is not None and (
                status["active_account"] != account or not status["enabled"]):
            raise AutoReplySourceError("busy", "快照同步正在处理其他任务，自动回复未替换正在运行的同步")

    @staticmethod
    def _binding(account):
        try:
            context, source, key = SNAPSHOT_REFRESH._inputs(account)
            require_account_available(context.account_dir)
            root_stat, source_stat = context.account_dir.stat(), source.stat()
        except (SnapshotRefreshError, SnapshotRegistryError, OSError) as exc:
            raise AutoReplySourceError("unavailable", "自动回复账号来源或密钥绑定不可用") from exc
        return context, source, {
            "account_dir": str(context.account_dir.resolve()), "source_dir": str(source),
            "account_device": root_stat.st_dev, "account_inode": root_stat.st_ino,
            "source_device": source_stat.st_dev, "source_inode": source_stat.st_ino,
            "key_digest": hashlib.sha256(key.encode("ascii")).hexdigest(),
        }

    def verify_binding(self, account, binding):
        """Short synchronous source/key check for the final submit callback."""
        _, _, current = self._binding(account)
        if current != binding:
            raise AutoReplySourceError("changed", "自动回复账号来源或数据库密钥已改变")

    def _snapshot(self, account, binding):
        context, source, current = self._binding(account)
        if binding is not None and current != binding:
            raise AutoReplySourceError("changed", "自动回复账号来源或数据库密钥已改变")
        directory = resolve_account_database_dir(context.account_dir)
        if directory == context.account_dir:
            raise AutoReplySourceError("snapshot", "自动回复需要已验证的独立快照，请先完成同步")
        ownership = json.loads((directory.parents[1] / "ownership.json").read_text(encoding="utf-8"))
        if Path(ownership["source_db_storage_path"]).resolve() != source:
            raise AutoReplySourceError("changed", "当前快照与自动回复账号来源不一致")
        return context.account_dir, directory, source, current

    async def _worker(self, account, function):
        cancelled = threading.Event()
        deadline = time.monotonic() + self.timeout_s

        def checkpoint():
            if cancelled.is_set():
                raise AutoReplySourceError("cancelled", "自动回复快照读取已取消")
            if time.monotonic() >= deadline:
                raise AutoReplySourceError("timeout", "自动回复快照读取超时", 504)

        # Account workers inherit a creation-time pin. Clear both at dispatch
        # and inside the worker so refresh never reads an older HTTP snapshot.
        def read():
            with snapshot_read_scope(independent=True) as pins:
                pins.clear()
                checkpoint()
                return function(checkpoint)
        try:
            account = SNAPSHOT_REFRESH._validate_account(account)
            with snapshot_read_scope(independent=True) as pins:
                pins.clear()
                return await account_to_thread(get_output_dir() / "databases" / account, read)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        except (SnapshotRefreshError, SnapshotRegistryError, OSError, sqlite3.Error, ValueError, KeyError) as exc:
            raise AutoReplySourceError("read", "自动回复账号快照读取失败，请检查来源与数据完整性", 502) from exc

    async def verify(self, account, binding):
        def verify(checkpoint):
            status = SNAPSHOT_REFRESH.status(account)
            self._status(status, account)
            if status['active_account'] != account or not status['enabled']:
                raise AutoReplySourceError('stopped', '自动回复所需的快照同步已停止，请重新启用自动回复')
            return self._snapshot(account, binding)
        await self._worker(account, verify)

    async def start(self, account):
        def launch():
            with snapshot_read_scope(independent=True) as pins:
                pins.clear()
                with SNAPSHOT_REFRESH._lock:
                    status = SNAPSHOT_REFRESH.status(account)
                    if status["active_account"] is not None:
                        self._status(status, account)
                        return status
                    return SNAPSHOT_REFRESH.start(account, resume=True)
        try:
            return await asyncio.to_thread(launch)
        except SnapshotRefreshError as exc:
            raise AutoReplySourceError("refresh", "无法启动自动回复所需的共享快照同步") from exc

    async def _refresh(self, account):
        deadline = time.monotonic() + self.timeout_s

        def request():
            with snapshot_read_scope(independent=True) as pins:
                pins.clear()
                with SNAPSHOT_REFRESH._lock:
                    self._status(SNAPSHOT_REFRESH.status(account), account)
                    status = SNAPSHOT_REFRESH.once(account)
                    if status.get("error"):
                        self._status(status, account)
                    return status["request_id"], None if status["enabled"] else SNAPSHOT_REFRESH._thread

        def ready(request_id):
            with snapshot_read_scope(independent=True) as pins:
                pins.clear()
                with SNAPSHOT_REFRESH._lock:
                    status = SNAPSHOT_REFRESH.status(account)
                    if status.get("error"):
                        self._status(status, account)
                    if status["active_account"] not in (None, account):
                        raise AutoReplySourceError("busy", "快照同步账号已改变")
                    # Archiving may belong to an older generation. Only a
                    # source check begun after this request can satisfy it.
                    if status["completed_request_id"] >= request_id:
                        return True
                    if status["active_account"] is None or status["phase"] == "stopping":
                        raise AutoReplySourceError("cancelled", "本次快照同步尚未完成就已停止，请重新应用自动回复设置")
                    return False

        try:
            request_id, worker = await asyncio.to_thread(request)
            if worker is not None:
                await asyncio.to_thread(worker.join, max(0, deadline - time.monotonic()))
            while time.monotonic() < deadline:
                if await asyncio.to_thread(ready, request_id):
                    return deadline
                await asyncio.sleep(min(.05, max(0, deadline - time.monotonic())))
        except SnapshotRefreshError as exc:
            raise AutoReplySourceError("refresh", "自动回复请求新鲜快照失败") from exc
        raise AutoReplySourceError("timeout", "自动回复未在限定时间内取得新鲜快照", 504)

    @staticmethod
    def _layout(account_dir, session_id, needs_mentions, checkpoint):
        expected = hashlib.md5(session_id.encode("utf-8")).hexdigest()
        self_id, newest, context_times = None, None, []
        for path in chat._iter_message_db_paths(account_dir):
            checkpoint()
            with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
                db.set_progress_handler(checkpoint, 5000)
                table = chat._resolve_msg_table_name(db, session_id)
                if table is None:
                    continue
                names = {row[0].lower() for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                exact = names & {"msg_" + expected, "chat_" + expected}
                if len(exact) != 1 or table.lower() not in exact:
                    raise AutoReplySourceError("conversation", "快照中的目标会话消息表无法精确定位")
                rowid, matched = resolve_account_self_rowid(db, account_dir)
                if rowid is None or (self_id is not None and matched != self_id):
                    raise AutoReplySourceError("identity", "消息分库没有一致的当前账号真实发送者身份")
                self_id = matched
                quoted = chat._quote_ident(table)
                columns = {row[1].lower() for row in db.execute(f"PRAGMA table_info({quoted})")}
                if needs_mentions and not columns.intersection({"source", "msg_source"}):
                    raise AutoReplySourceError("mentions", "目标群聊消息表缺少准确的 @ 字段，不能使用仅 @ 我")
                timestamp = db.execute(f"SELECT MAX(create_time) FROM {quoted}").fetchone()[0]
                if timestamp is not None:
                    newest = int(timestamp) if newest is None else max(newest, int(timestamp))
                # Locate at most ten ordinary-text timestamps per shard first.
                # The following descending parser never starts at history's beginning.
                context_times.extend(int(row[0]) for row in db.execute(
                    f"SELECT create_time FROM {quoted} WHERE local_type=1 ORDER BY create_time DESC LIMIT 10"))
        if self_id is None:
            raise AutoReplySourceError("conversation", "已验证快照中没有目标会话的准确消息表")
        return self_id, newest, min(context_times) if context_times else None

    async def read(self, account, session_id, *, cursor=None, fresh=False, baseline=False,
                   binding=None, needs_mentions=False):
        deadline = await self._refresh(account) if fresh else None

        def collect(checkpoint):
            account_dir, directory, source, current_binding = self._snapshot(account, binding)
            self_id, newest, context_start = self._layout(account_dir, session_id, needs_mentions, checkpoint)
            latest = {"time": 0, "ids": []}
            if newest is not None:
                with closing(iter_message_pages(account, session_id, newest, newest,
                        emit_cursor=True, checkpoint=checkpoint)) as pages:
                    for page in pages:
                        latest = advance_cursor(page["messages"], latest)
            context = []
            if context_start is not None:
                with closing(iter_message_pages(account, session_id, context_start, None,
                        descending=True, message_kind="text", page_size=10, checkpoint=checkpoint)) as pages:
                    for page in pages:
                        context.extend(m for m in page["messages"] if m["media"]["type"] == 1)
                        if len(context) >= 10:
                            break
                context = list(reversed(context[:10]))
            messages, consumed = [], {"time": 0, "ids": []} if cursor is None else cursor
            if baseline:
                consumed = latest
            else:
                with closing(iter_message_pages(account, session_id, None, None,
                        cursor=consumed, emit_cursor=True, checkpoint=checkpoint)) as pages:
                    for page in pages:
                        messages.extend(page["messages"])
                        consumed = page["cursor"]
            self.verify_binding(account, current_binding)
            checkpoint()
            if fresh:
                def fresh_checkpoint():
                    checkpoint()
                    if time.monotonic() >= deadline:
                        raise AutoReplySourceError("timeout", "自动回复新鲜快照核验超时", 504)
                fresh_checkpoint()
                manifest = json.loads((directory.parents[1] / "manifest.json").read_text(encoding="utf-8"))
                if _source_manifest(source, checkpoint=fresh_checkpoint) != manifest["manifest"]:
                    raise AutoReplySourceError("stale", "微信数据文件与当前快照不一致，无法确认消息已同步；请等待同步完成后重新应用自动回复设置")
                fresh_checkpoint()
            return {"binding": current_binding, "self_id": self_id, "messages": messages,
                    "cursor": consumed, "latest_cursor": latest, "context": context}

        return await self._worker(account, collect)
