"""Explicit, process-local refresh of verified independent snapshot generations.

One worker owns one selected account until stopped or failed. Completed and
cancelled generations are retained; each build checks available disk space.
Stopping never interrupts an in-progress decrypt, and shutdown really joins it.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import shutil
import threading
import time
from typing import Any

from .account_source_policy import source_metadata_is_imported_snapshot
from .account_identity import canonical_account_name
from .app_paths import get_output_dir
from .chat_accounts import resolve_chat_account_context
from .independent_snapshot import (
    _capture_scope, _check_ancestors, _reject_link, _shm_identities,
    _source_inventory, _source_manifest, SnapshotBuildError,
    build_verified_snapshot,
)
from .key_store import load_account_keys_store
from .logging_config import get_logger
from .snapshot_archive import archive_published_snapshot, read_archive_state
from .snapshot_events import notify_snapshot_change
from .snapshot_registry import (
    SnapshotRegistryError, get_current_snapshot, publish_snapshot,
    require_no_legacy_database_write, snapshot_publication_scope,
    begin_account_work, require_account_available,
)

logger = get_logger(__name__)


class SnapshotRefreshError(RuntimeError):
    def __init__(self, stage: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.stage = stage
        self.status_code = status_code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _retained_bytes(parent: Path) -> int:
    _check_ancestors(parent)
    if not parent.exists():
        return 0
    def raise_walk_error(exc: OSError) -> None:
        raise exc
    total = 0
    for directory, dirs, files in os.walk(parent, onerror=raise_walk_error, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            info = path.lstat()
            _reject_link(path, info)
            if name in files:
                total += info.st_size
    return total


def _source_change_token(source: Path) -> tuple:
    """Read small change hints; only the existing full capture can verify bytes.

    Live SHM headers are read through the same mapped, locked WAL protocol as a
    capture. Commit length plus the WAL header salt detects commits and resets
    even when memory-mapped SHM writes do not update filesystem timestamps.
    """
    root = source.stat()
    names = _source_inventory(source)
    identities = _shm_identities(source, names)
    entries = []
    with _capture_scope(source, names) as (pinned, boundaries), ExitStack() as opened:
        for name in names:
            path = source / name
            if path in pinned:
                stream, size = pinned[path]
            else:
                stream = opened.enter_context(path.open("rb"))
                size = os.fstat(stream.fileno()).st_size
            before = os.fstat(stream.fileno())
            stream.seek(0)
            head_size = min(size, 128)
            head = stream.read(head_size)
            after = os.fstat(stream.fileno())
            if (len(head) != head_size or not os.path.samestat(before, after)
                    or not os.path.samestat(after, path.stat())):
                raise SnapshotBuildError("source_changed", f"Source identity or length changed during probe: {name}")
            entries.append((name, before.st_dev, before.st_ino, before.st_size,
                            before.st_mtime_ns, after.st_size, after.st_mtime_ns, size, head))
        if names != _source_inventory(source) or identities != _shm_identities(source, names):
            raise SnapshotBuildError("source_changed", "Source inventory changed during probe")
        if not os.path.samestat(root, source.stat()):
            raise SnapshotBuildError("source_changed", "Source directory was replaced during probe")
    return ((root.st_dev, root.st_ino), tuple(entries), tuple(identities.items()),
            tuple((name, tuple(boundary.items())) for name, boundary in boundaries.items()))


class SnapshotRefreshService:
    def __init__(self) -> None:
        # ponytail: one process-local worker; parallel accounts need explicit scheduling.
        self._lock = threading.RLock()
        self._states: dict[str, dict[str, Any]] = {}
        self._manifests: dict[str, dict] = {}
        self._active: str | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._closed = False

    @staticmethod
    def _validate_account(account: str) -> str:
        if not isinstance(account, str) or not account.strip():
            raise SnapshotRefreshError("validation", "An explicit account is required")
        if any(char in account for char in ("/", "\\", ":", "\x00")) or account in {".", ".."}:
            raise SnapshotRefreshError("validation", "Invalid account")
        return account.strip()

    def _state(self, account: str) -> dict[str, Any]:
        if account in self._states:
            return self._states[account]
        context = resolve_chat_account_context(account)
        if context.name != account:
            raise SnapshotRefreshError("validation", "Select the canonical account explicitly")
        current = get_current_snapshot(context.account_dir)
        state = {
            "account": account, "enabled": False, "running": False, "phase": "disabled", "user_paused": False,
            "interval_seconds": 30, "last_checked_at": None, "last_success_at": None,
            "probe_interval_seconds": 0.5, "last_check_trigger": None,
            "request_id": 0, "completed_request_id": 0,
            "revision": 0, "generation": None, "guarantee": "stable_observation", "error": None,
            "retained_bytes": _retained_bytes(get_output_dir() / "verified_snapshots"),
            "source_path": context.db_storage_path,
            "restart_policy": "disabled",
            "archive_generation": None, "last_archive_at": None, "archive_progress": None,
        }
        archived = read_archive_state(context.account_dir)
        if archived is not None:
            state.update(archive_generation=archived["generation"], last_archive_at=archived["completed_at"])
        if current is not None:
            generation_path = get_output_dir() / current["generation_path"]
            result = json.loads((generation_path / "manifest.json").read_text(encoding="utf-8"))
            state.update(revision=current["revision"], generation=generation_path.name,
                         last_success_at=current["published_at"], guarantee=result["guarantee"])
            self._manifests[account] = result["manifest"]
        self._states[account] = state
        return state

    def _public(self, state: dict[str, Any]) -> dict[str, Any]:
        context = resolve_chat_account_context(state["account"])
        reason = ""
        if source_metadata_is_imported_snapshot(context.source_info):
            reason = "导入的历史记录，不连接本机微信"
        elif not context.db_storage_path:
            reason = "尚未绑定本机微信数据目录"
        return deepcopy({**state, "active_account": self._active,
                         "refresh_available": not reason, "unavailable_reason": reason})

    def status(self, account: str) -> dict[str, Any]:
        account = self._validate_account(account)
        with self._lock:
            return self._public(self._state(account))

    def _inputs(self, account: str):
        try:
            store = load_account_keys_store(strict=True)
        except Exception as exc:
            raise SnapshotRefreshError("key", f"Cannot read account key store: {exc}", 500) from exc
        context = resolve_chat_account_context(account)
        if context.name != account:
            raise SnapshotRefreshError("validation", "Selected account identity changed")
        if source_metadata_is_imported_snapshot(context.source_info):
            raise SnapshotRefreshError("source", "Imported snapshots cannot be refreshed from this computer")
        source = Path(context.db_storage_path)
        if not context.db_storage_path or not source.is_absolute() or source.name.lower() != "db_storage":
            raise SnapshotRefreshError("source", "Selected account has no explicit absolute db_storage source")
        _check_ancestors(source)
        source = source.resolve(strict=True)
        if not source.is_dir():
            raise SnapshotRefreshError("source", "Selected db_storage source is not a directory")
        entry = store.get(account)
        if not isinstance(entry, dict):
            raise SnapshotRefreshError("key", "Selected account has no saved database key")
        binding = entry.get("db_key_source_db_storage_path")
        if not isinstance(binding, str) or not binding or not Path(binding).is_absolute():
            raise SnapshotRefreshError("key", "Saved database key has no explicit source binding")
        if Path(binding).resolve(strict=True) != source:
            raise SnapshotRefreshError("key", "Saved database key source does not match selected account source")
        key = entry.get("db_key")
        if not isinstance(key, str) or re.fullmatch(r"[0-9a-fA-F]{64}", key) is None:
            raise SnapshotRefreshError("key", "Saved database key must contain exactly 64 hexadecimal characters")
        return context, source, key

    def start(self, account: str, *, interval_seconds: float = 30, resume: bool = False) -> dict[str, Any]:
        if isinstance(interval_seconds, bool) or not math.isfinite(interval_seconds) or interval_seconds < 1:
            raise SnapshotRefreshError("validation", "interval_seconds must be a finite number of at least 1")
        return self._launch(account, continuous=True, interval_seconds=interval_seconds, resume=resume)

    def once(self, account: str) -> dict[str, Any]:
        return self._launch(account, continuous=False)

    def _launch(self, account: str, *, continuous: bool, interval_seconds: float | None = None,
                resume: bool = False) -> dict[str, Any]:
        account = self._validate_account(account)
        with self._lock:
            if self._closed:
                raise SnapshotRefreshError("shutdown", "Snapshot refresh service is shutting down", 409)
            with snapshot_publication_scope():
                try:
                    account_dir = get_output_dir() / "databases" / account
                    require_account_available(account_dir)
                    require_no_legacy_database_write(account_dir)
                except SnapshotRegistryError as exc:
                    raise SnapshotRefreshError("busy", str(exc), 409) from exc
            state = self._state(account)
            if continuous and state["user_paused"] and not resume:
                raise SnapshotRefreshError("paused", "用户已暂停后台同步，请主动恢复后再启动。", 409)
            if self._active is not None:
                active_state = self._states[self._active]
                if self._active == account and not continuous and active_state["enabled"]:
                    active_state["request_id"] += 1
                    if not active_state["running"]:
                        active_state.update(phase="checking", running=True)
                    self._wake.set()
                    return self._public(active_state)
                raise SnapshotRefreshError("busy", f"Snapshot refresh is already active for {self._active}; stop it first", 409)
            if self._thread is not None and self._thread.is_alive():
                raise SnapshotRefreshError("busy", "Previous snapshot refresh worker is finishing", 409)
            if continuous:
                state.update(interval_seconds=interval_seconds)
            state.update(enabled=continuous, running=True, phase="checking", error=None)
            try:
                work_lease = begin_account_work(get_output_dir() / "databases" / account)
            except SnapshotRegistryError as exc:
                state.update(enabled=False, running=False, phase="disabled")
                raise SnapshotRefreshError("busy", str(exc), 409) from exc
            self._stop.clear()
            self._wake.clear()
            self._active = account
            state["request_id"] += 1
            self._thread = threading.Thread(target=self._run, args=(account, work_lease), name="independent-snapshot-refresh", daemon=True)
            try:
                self._thread.start()
            except Exception as exc:
                state["request_id"] -= 1
                work_lease.release()
                self._fail(state, "worker", exc)
                self._active = None
                raise SnapshotRefreshError("worker", str(exc), 500) from exc
            if continuous:
                state["user_paused"] = False
            return self._public(state)

    def stop_and_join(self, account: str) -> None:
        """Stop only this account family and wait outside the control lock."""
        family = canonical_account_name(account)
        with self._lock:
            thread = None
            if self._active is not None and canonical_account_name(self._active) == family:
                state = self._states[self._active]
                state.update(enabled=False, phase="stopping" if state["running"] else "stopped")
                self._stop.set()
                self._wake.set()
                thread = self._thread
            elif self._thread is not None and self._thread.is_alive() and self._active is None:
                # _run releases its reservation immediately after clearing active.
                thread = self._thread
        if thread is not None:
            thread.join()

    def forget_account(self, account: str) -> None:
        """Forget deleted metadata only after the real worker has exited."""
        family = canonical_account_name(account)
        with self._lock:
            if self._active is not None and canonical_account_name(self._active) == family:
                raise SnapshotRefreshError("busy", "Account refresh worker is still active", 409)
            for name in list(self._states):
                if canonical_account_name(name) == family:
                    self._states.pop(name)
                    self._manifests.pop(name, None)

    def stop(self, account: str, *, user_paused: bool = False) -> dict[str, Any]:
        account = self._validate_account(account)
        with self._lock:
            state = self._state(account)
            state["enabled"] = False
            if user_paused:
                state["user_paused"] = True
            if self._active == account:
                self._stop.set()
                self._wake.set()
                state["phase"] = "stopping" if state["running"] else "stopped"
            elif state["phase"] != "failed":
                state["phase"] = "stopped"
            return self._public(state)

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            thread = self._thread
            if self._active is not None:
                state = self._states[self._active]
                state.update(enabled=False, phase="stopping" if state["running"] else "stopped")
            self._stop.set()
            self._wake.set()
        if thread is not None:
            thread.join()

    def _fail(self, state: dict[str, Any], stage: str, exc: Exception) -> None:
        state.update(enabled=False, running=False, phase="failed", error={
            "stage": getattr(exc, "stage", stage), "type": type(exc).__name__, "message": str(exc),
        })
        logger.exception("[snapshot-refresh] account=%s stage=%s failed", state["account"], state["error"]["stage"])
        notify_snapshot_change(state["account"])

    def _archive(self, state, account_dir: Path, generation: Path) -> bool:
        if self._stop.is_set():
            return False
        with self._lock:
            state.update(phase="archiving", running=True, archive_progress=dict(
                initial=state["archive_generation"] is None,
                messages_processed=0, databases_done=0, databases_total=0))
        def progress(value):
            with self._lock:
                state["archive_progress"] = value
        completed = archive_published_snapshot(account_dir, generation,
            progress=progress, stopped=self._stop.is_set)
        if completed is None:
            return False
        with self._lock:
            state.update(archive_generation=completed["generation"],
                         last_archive_at=completed["completed_at"], phase="idle", running=False)
        notify_snapshot_change(state["account"])
        return True

    def _run(self, account: str, work_lease) -> None:
        state = self._states[account]
        source_at_start: Path | None = None
        source_identity: tuple[int, int] | None = None
        continuous = state["enabled"]
        trigger = "initial"
        stage = "source"
        try:
            while not self._stop.is_set():
                with self._lock:
                    # Consume only requests that preceded this check. Requests
                    # made during capture/decrypt remain set for a trailing check.
                    self._wake.clear()
                    request_id = state["request_id"]
                    state.update(running=True, phase="checking", last_check_trigger=trigger)
                stage = "source"
                context, source, key = self._inputs(account)
                if source_at_start is not None and source != source_at_start:
                    raise SnapshotRefreshError("source_changed", "Selected account source changed; start refresh again explicitly")
                source_at_start = source
                if continuous:
                    stage = "source_probe"
                    token = _source_change_token(source)
                    if source_identity is not None and token[0] != source_identity:
                        raise SnapshotRefreshError("source_changed", "Selected source directory was replaced")
                    source_identity = token[0]
                with self._lock:
                    state["source_path"] = str(source)
                current = get_current_snapshot(context.account_dir)
                if current is not None:
                    generation = get_output_dir() / current["generation_path"]
                    if state["archive_generation"] != generation.name:
                        stage = "archive"
                        if not self._archive(state, context.account_dir, generation):
                            break
                        with self._lock:
                            state.update(phase="checking", running=True)
                stage = "source_read"
                manifest = _source_manifest(source)
                with self._lock:
                    state["last_checked_at"] = _now()
                if self._stop.is_set():
                    break
                if manifest == self._manifests.get(account):
                    with self._lock:
                        if self._stop.is_set():
                            break
                        state.update(phase="unchanged", running=False, completed_request_id=request_id)
                else:
                    stage = "capacity"
                    parent = get_output_dir() / "verified_snapshots"
                    retained = _retained_bytes(parent)
                    estimated = 2 * sum(item["size"] for item in manifest.values())
                    with self._lock:
                        state["retained_bytes"] = retained
                    disk_path = parent
                    while not disk_path.exists():
                        disk_path = disk_path.parent
                    free = shutil.disk_usage(disk_path).free
                    if estimated > free:
                        raise SnapshotRefreshError("capacity", f"Insufficient disk space: estimated_build={estimated}, free={free}")
                    with self._lock:
                        if self._stop.is_set():
                            break
                        state["phase"] = "building"
                    stage = "build"
                    result = build_verified_snapshot(source, key, parent)
                    retained = _retained_bytes(parent)
                    with self._lock:
                        state["retained_bytes"] = retained
                        if self._stop.is_set():
                            break
                        # Source selection may change while decrypt runs. Validate
                        # again under the same lock that orders stop and publication.
                        with snapshot_publication_scope():
                            stage = "source_changed"
                            require_account_available(context.account_dir)
                            try:
                                current_context, current_source, current_key = self._inputs(account)
                            except Exception as exc:
                                raise SnapshotRefreshError("source_changed", f"Account source validation changed during build: {exc}") from exc
                            if current_context.account_dir != context.account_dir or current_source != source or current_key != key:
                                raise SnapshotRefreshError("source_changed", "Account source or saved key changed during build; candidate was not published")
                            if source_identity is not None:
                                current_info = current_source.stat()
                                if (current_info.st_dev, current_info.st_ino) != source_identity:
                                    raise SnapshotRefreshError("source_changed", "Source directory was replaced during build; candidate was not published")
                            stage = "publish"
                            pointer = publish_snapshot(context.account_dir, result)
                        self._manifests[account] = result["manifest"]
                        state.update(revision=pointer["revision"], generation=Path(pointer["generation_path"]).name,
                                     last_success_at=pointer["published_at"], guarantee=result["guarantee"],
                                     completed_request_id=request_id,
                                     phase="archiving", running=True, archive_progress=dict(
                                         initial=state["archive_generation"] is None,
                                         messages_processed=0, databases_done=0, databases_total=0))
                    notify_snapshot_change(account)
                    stage = "archive"
                    if not self._archive(state, context.account_dir, Path(result["generation_path"])):
                        break
                with self._lock:
                    if not state["enabled"] or self._stop.is_set():
                        break
                    state["phase"] = "waiting"
                    interval = state["interval_seconds"]
                reconciliation_at = time.monotonic() + interval
                while not self._stop.is_set():
                    remaining = max(0, reconciliation_at - time.monotonic())
                    if self._wake.wait(min(state["probe_interval_seconds"], remaining)):
                        trigger = "manual"
                        break
                    if self._stop.is_set():
                        break
                    stage = "source"
                    _, current_source, _ = self._inputs(account)
                    if current_source != source_at_start:
                        raise SnapshotRefreshError("source_changed", "Selected account source changed; start refresh again explicitly")
                    stage = "source_probe"
                    observed = _source_change_token(current_source)
                    if observed[0] != source_identity:
                        raise SnapshotRefreshError("source_changed", "Selected source directory was replaced")
                    if observed != token:
                        trigger = "change"
                        break
                    if time.monotonic() >= reconciliation_at:
                        trigger = "reconciliation"
                        break
        except Exception as exc:
            with self._lock:
                self._fail(state, stage, exc)
        finally:
            with self._lock:
                if self._stop.is_set() and state["phase"] != "failed":
                    state["phase"] = "stopped"
                state.update(enabled=False, running=False)
                self._active = None
            work_lease.release()


SNAPSHOT_REFRESH = SnapshotRefreshService()
