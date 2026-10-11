from .snapshot_registry import (
    resolve_account_database_dir, snapshot_cache_dir, start_snapshot_thread,
    _check_ancestors, _generation_account, _validate_generation, SnapshotRegistryError,
)
from contextlib import closing
import os
import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .account_identity import account_identity_candidates, resolve_account_self_rowid
from .anti_revoke import (
    format_revoked_message_as_chat_item,
    get_all_revoked_messages,
    get_anti_revoke_db_path,
    get_revoked_messages_map,
)
from .chat_helpers import (
    _decode_sqlite_text,
    _decode_message_content,
    _quote_ident,
    _resolve_msg_table_name_by_map,
    _row_to_search_hit,
    _should_keep_session,
    _to_char_token_text,
    _iter_message_db_paths,
    _resource_lookup_chat_id,
)
from .logging_config import get_logger
from .account_identity import resolve_account_username
from .data_source import normalize_data_source

logger = get_logger(__name__)

_SCHEMA_VERSION = 6
_INDEX_DB_NAME = "chat_search_index.db"
_INDEX_DB_TMP_NAME = "chat_search_index.tmp.db"
_LEGACY_INDEX_DB_NAME = "message_fts.db"

_BUILD_LOCK = threading.Lock()
_BUILD_STATE: dict[str, dict[str, Any]] = {}

_DEFAULT_INSERT_BATCH_SIZE = 5000
_COMMIT_EVERY_MESSAGES = 100000


def _env_int(name: str, default: int, *, min_value: int, max_value: int) -> int:
    raw = os.environ.get(name)
    try:
        value = int(str(raw or "").strip() or default)
    except Exception:
        value = int(default)
    return max(int(min_value), min(int(max_value), int(value)))


def _insert_batch_size() -> int:
    return _env_int(
        "WECHAT_CHAT_SEARCH_INDEX_INSERT_BATCH",
        _DEFAULT_INSERT_BATCH_SIZE,
        min_value=500,
        max_value=50000,
    )


def _normalize_index_source(value: Optional[str], *, default: str = "decrypted") -> str:
    return normalize_data_source(value, default)


def _account_key(account_dir: Path) -> str:
    return str(snapshot_cache_dir(account_dir))


def _index_db_path(account_dir: Path) -> Path:
    return snapshot_cache_dir(account_dir) / _INDEX_DB_NAME


def _index_db_tmp_path(account_dir: Path) -> Path:
    return snapshot_cache_dir(account_dir) / _INDEX_DB_TMP_NAME


def get_chat_search_index_db_path(account_dir: Path) -> Path:
    """
    Preferred index file: {account}/chat_search_index.db
    Legacy (older builds): {account}/message_fts.db (only if it looks like our index schema).
    """

    preferred = snapshot_cache_dir(account_dir) / _INDEX_DB_NAME
    if preferred.exists():
        return preferred

    legacy = snapshot_cache_dir(account_dir) / _LEGACY_INDEX_DB_NAME
    if legacy.exists():
        insp = _inspect_index(legacy)
        if bool(insp.get("hasFtsTable")) and bool(insp.get("hasMetaTable")):
            return legacy

    return preferred


def _read_meta(index_path: Path) -> dict[str, str]:
    if not index_path.exists():
        return {}
    conn = sqlite3.connect(str(index_path))
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='meta'").fetchall()
        if not rows:
            return {}
        out: dict[str, str] = {}
        for k, v in conn.execute("SELECT key, value FROM meta").fetchall():
            if k is None:
                continue
            out[str(k)] = "" if v is None else str(v)
        return out
    except Exception:
        return {}
    finally:
        conn.close()


def _inspect_index(index_path: Path) -> dict[str, Any]:
    if not index_path.exists():
        return {
            "exists": False,
            "ready": False,
            "hasFtsTable": False,
            "hasMetaTable": False,
            "hasMessageMetaTable": False,
            "hasTokenStatsTable": False,
            "schemaVersion": None,
        }

    conn = sqlite3.connect(str(index_path))
    try:
        try:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        except Exception:
            rows = []
        names = {str(r[0]).lower() for r in rows if r and r[0]}

        has_meta = "meta" in names
        has_fts = "message_fts" in names
        has_message_meta = "message_meta" in names
        has_token_stats = "message_token_stats" in names

        schema_version: Optional[int] = None
        if has_meta:
            try:
                r = conn.execute("SELECT value FROM meta WHERE key='schema_version' LIMIT 1").fetchone()
                if r and r[0] is not None:
                    schema_version = int(str(r[0]).strip() or "0")
            except Exception:
                schema_version = None

        # Indexes that predate explicit schema metadata are kept readable for
        # compatibility with tests/hand-built analysis fixtures. Once a
        # schema_version is present, require the current schema so upgraded
        # installs rebuild and get the payload_json + single-char fast path.
        ready = bool(has_fts and (schema_version is None or schema_version >= _SCHEMA_VERSION))

        return {
            "exists": True,
            "ready": ready,
            "hasFtsTable": bool(has_fts),
            "hasMetaTable": bool(has_meta),
            "hasMessageMetaTable": bool(has_message_meta),
            "hasTokenStatsTable": bool(has_token_stats),
            "schemaVersion": schema_version,
        }
    except Exception:
        return {
            "exists": True,
            "ready": False,
            "hasFtsTable": False,
            "hasMetaTable": False,
            "hasMessageMetaTable": False,
            "hasTokenStatsTable": False,
            "schemaVersion": None,
        }
    finally:
        conn.close()


def _index_meta_source(meta: dict[str, str]) -> str:
    raw = str((meta or {}).get("source") or "").strip().lower()
    # Preserve historical provenance so an old index is not mistaken for the current source.
    if raw in {"realtime", "decrypted"}:
        return raw
    # Pre-migration indexes did not record a source and were built from local decrypted DBs.
    return "decrypted"


def _latest_decrypted_source_mtime(account_dir: Path) -> tuple[int, str]:
    """Return the newest mtime among SQLite files consumed by the index."""

    paths = [resolve_account_database_dir(account_dir) / "session.db", resolve_account_database_dir(account_dir) / "contact.db"]
    try:
        paths.extend(_iter_message_db_paths(account_dir))
    except Exception:
        pass

    latest_mtime_ns = 0
    latest_path = ""
    for path in paths:
        # 只看主库文件的 mtime。-wal / -shm 都不算数：
        #
        # 这两个边车文件会因为**读**而被改动——SQLite 在最后一个连接关闭时会自动
        # checkpoint，把 WAL 回写并重置它；实时同步、搜索、导出每碰一次这些库都会
        # 刷新 -wal 的 mtime。一旦把它算进「源数据是否更新」，一个刚建好的索引会在
        # 下一次读库之后立刻被判定为过期，而 discard_stale_chat_search_index 会把
        # 索引文件**直接删除**——用户看到的就是「明明构建过了，年度总结还说没构建」。
        #
        # 实测（2026-08-14 用户日志）：
        #   Discarded stale chat search index: source_latest=.../message_1.db-wal
        # 一个 1.3 GB、118 万条消息的完整索引就是这样被删掉的。
        #
        # 代价：若有新数据只提交进 WAL 而尚未 checkpoint，主库 mtime 不变，索引会被
        # 当作仍然新鲜，年度总结的统计会短暂落后到下一次 checkpoint。这个代价远小于
        # 误删整个索引——解密/同步流程重写分片时主库 mtime 一定会变，真正的「源数据
        # 换了」仍然能被抓到。
        try:
            mtime_ns = int(path.stat().st_mtime_ns)
        except (FileNotFoundError, OSError):
            continue
        if mtime_ns > latest_mtime_ns:
            latest_mtime_ns = mtime_ns
            latest_path = str(path)
    return latest_mtime_ns, latest_path


def get_chat_search_index_status(account_dir: Path, *, source: Optional[str] = None) -> dict[str, Any]:
    desired_source = _normalize_index_source(source, default="decrypted")
    key = _account_key(account_dir)
    index_path = get_chat_search_index_db_path(account_dir)
    inspect = _inspect_index(index_path)
    meta = _read_meta(index_path)
    actual_source = _index_meta_source(meta)
    try:
        index_mtime_ns = int(index_path.stat().st_mtime_ns)
    except (FileNotFoundError, OSError):
        index_mtime_ns = 0
    source_latest_mtime_ns, source_latest_path = _latest_decrypted_source_mtime(account_dir)
    stale_for_source_data = bool(
        inspect.get("ready")
        and actual_source == "decrypted"
        and index_mtime_ns > 0
        and source_latest_mtime_ns > index_mtime_ns
    )
    # Committed revoke changes can live only in WAL. Compare the confirmed
    # records themselves; archive writes and read-side checkpoints are not
    # evidence that a message's revoke status changed.
    stale_for_anti_revoke = bool(
        inspect.get("ready")
        and (get_anti_revoke_db_path(account_dir).exists() or "anti_revoke_signature" in meta)
        and meta.get("anti_revoke_signature") != _anti_revoke_signature(get_all_revoked_messages(account_dir))
    )
    # A completed, schema-compatible index remains usable when newer messages
    # arrive.  Freshness is advisory: rebuilding a multi-GB index is expensive,
    # and a live SQLite WAL will normally become newer again soon after a build.
    # Consumers that require a complete snapshot can check `upToDate` and fall
    # back to the source shards without destroying the usable index.
    ready = (
        bool(inspect.get("ready"))
        and actual_source == desired_source
        and not stale_for_anti_revoke
    )
    up_to_date = bool(ready and not stale_for_source_data)
    with _BUILD_LOCK:
        state = dict(_BUILD_STATE.get(key) or {})
    return {
        "status": "success",
        "account": account_dir.name,
        "index": {
            "path": str(index_path),
            "exists": bool(inspect.get("exists")),
            "ready": bool(ready),
            "upToDate": up_to_date,
            "source": actual_source,
            "desiredSource": desired_source,
            "staleForSource": bool(inspect.get("ready")) and actual_source != desired_source,
            "staleForSourceData": stale_for_source_data,
            "staleForAntiRevoke": stale_for_anti_revoke,
            "indexMtimeNs": index_mtime_ns,
            "sourceLatestMtimeNs": source_latest_mtime_ns,
            "sourceLatestPath": source_latest_path,
            "hasFtsTable": bool(inspect.get("hasFtsTable")),
            "hasMetaTable": bool(inspect.get("hasMetaTable")),
            "schemaVersion": inspect.get("schemaVersion"),
            "meta": meta,
            "build": state,
        },
    }

def start_chat_search_index_build(account_dir: Path, *, rebuild: bool = False, source: Optional[str] = None) -> dict[str, Any]:
    source_norm = _normalize_index_source(source, default="decrypted")
    key = _account_key(account_dir)
    now = int(time.time())
    with _BUILD_LOCK:
        st = _BUILD_STATE.get(key)
        already_building = bool(st and st.get("status") == "building")
        if not already_building:
            _BUILD_STATE[key] = {
                "status": "building",
                "rebuild": bool(rebuild),
                "source": source_norm,
                "startedAt": now,
                "finishedAt": None,
                "indexedMessages": 0,
                "fetchedMessages": 0,
                "fetchCalls": 0,
                "totalConversations": 0,
                "completedConversations": 0,
                "messagesPerSec": 0,
                "currentDb": "",
                "currentConversation": "",
                "error": "",
            }

    if already_building:
        return get_chat_search_index_status(account_dir, source=source_norm)

    try:
        start_snapshot_thread(account_dir, _build_worker,
                              args=(account_dir, bool(rebuild), source_norm), name=f"chat-search-index:{key}")
    except Exception as exc:
        _update_build_state(key, status="error", error=str(exc), finishedAt=int(time.time()))
        raise
    return get_chat_search_index_status(account_dir, source=source_norm)


def _update_build_state(account_key: str, **kwargs: Any) -> None:
    with _BUILD_LOCK:
        st = _BUILD_STATE.get(account_key)
        if not st:
            return
        st.update(kwargs)


def forget_account_search_index(account_dir: Path) -> None:
    """Clear progress after the caller exclusively quiesces and clears this account."""
    root = Path(account_dir)
    with _BUILD_LOCK:
        for key in list(_BUILD_STATE):
            if Path(key) == root or Path(key).parent == root / "_snapshot_cache":
                del _BUILD_STATE[key]


def _snapshot_index_sources(account_dir: Path, db_paths: list[Path], sessions: dict,
                            anti_revoke_signature: str) -> dict | None:
    """Reuse only verified generations, proving both captured and decrypted bytes."""
    from .snapshot_archive import _source_files

    database_dir = resolve_account_database_dir(account_dir)
    if database_dir == account_dir:
        return None
    generation = database_dir.parents[1]
    metadata = _validate_generation(account_dir, generation, _generation_account(account_dir, database_dir.name))
    shards, resource_identity = {}, None
    resource_path = database_dir / 'message_resource.db'
    for path in [*db_paths, *([resource_path] if resource_path.exists() else [])]:
        files = _source_files(metadata['manifest'], path.name)
        if not any(Path(name).name == path.name for name in files):
            raise SnapshotRegistryError(f"Snapshot source manifest is missing {path.name}")
        with path.open('rb') as stream:
            before = os.fstat(stream.fileno())
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            after = os.fstat(stream.fileno())
        if (not os.path.samestat(before, path.stat()) or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns):
            raise SnapshotRegistryError(f"Snapshot database changed while preparing index: {path}")
        identity = {
            'captured': {name: {field: entry[field] for field in ('size', 'sha256')}
                         for name, entry in files.items()},
            'decrypted_sha256': digest,
        }
        if path == resource_path:
            resource_identity = identity
        else:
            shards[path.stem] = identity
    return {'version': 1, 'shards': shards, 'sessions': sessions,
            'identity': [resolve_account_username(account_dir), *account_identity_candidates(account_dir)],
            'anti_revoke_signature': anti_revoke_signature, 'message_resource': resource_identity}


def _reuse_snapshot_index(account_dir: Path, tmp_path: Path, sources: dict | None,
                          rebuild: bool) -> set[str]:
    """Copy a compatible index privately; no prior generation becomes current/ready."""
    if rebuild or sources is None:
        return set()
    candidates = list((account_dir / '_snapshot_cache').glob(f'generation-*/{_INDEX_DB_NAME}'))
    candidates.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
    for path in candidates:
        _check_ancestors(path)
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as previous:
            previous.execute('BEGIN')
            meta = dict(previous.execute('SELECT key,value FROM meta'))
            if (meta.get('schema_version') != str(_SCHEMA_VERSION) or meta.get('source') != 'decrypted'
                    or 'snapshot_sources' not in meta):
                continue
            old = json.loads(meta['snapshot_sources'])
            if {k: v for k, v in old.items() if k != 'shards'} != {k: v for k, v in sources.items() if k != 'shards'}:
                continue
            unchanged = {name for name, value in sources['shards'].items() if old['shards'].get(name) == value}
            if not unchanged:
                continue
            # A copied index must already be complete. _init_index_db creates
            # tables for new builds and must never repair a damaged donor.
            tables = {row[0] for row in previous.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            missing = {'message_fts', 'message_meta', 'message_token_stats'} - tables
            if missing:
                raise sqlite3.DatabaseError(f"Cannot reuse incomplete chat search index {path}: missing tables {sorted(missing)}")
            try:
                expected = int(meta['message_count'])
                expected_tokens = int(meta['single_char_token_count'])
            except (KeyError, ValueError) as exc:
                raise sqlite3.DatabaseError(f"Cannot reuse incomplete chat search index {path}: invalid stored counts") from exc
            fts_count = previous.execute('SELECT count(*) FROM message_fts').fetchone()[0]
            meta_count = previous.execute('SELECT count(*) FROM message_meta').fetchone()[0]
            token_count, invalid_tokens = previous.execute(
                'SELECT count(*),count(CASE WHEN doc_count<=0 OR doc_count>? THEN 1 END) FROM message_token_stats',
                (expected,)).fetchone()
            if (expected < 0 or fts_count != expected or meta_count != expected
                    or expected_tokens < 0 or token_count != expected_tokens or invalid_tokens):
                raise sqlite3.DatabaseError(
                    f"Cannot reuse incomplete chat search index {path}: expected={expected}, "
                    f"fts={fts_count}, metadata={meta_count}, expected_tokens={expected_tokens}, "
                    f"tokens={token_count}, invalid_tokens={invalid_tokens}")
            with closing(sqlite3.connect(tmp_path)) as target:
                previous.backup(target)
            return unchanged
    return set()


def _sqlite_table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({_quote_ident(table_name)})").fetchall()
    except Exception:
        return set()

    columns: set[str] = set()
    for row in rows:
        try:
            name = str(row["name"] if isinstance(row, sqlite3.Row) else row[1] or "").strip().lower()
        except Exception:
            name = ""
        if name:
            columns.add(name)
    return columns


def _load_session_table_targets(account_dir: Path) -> dict[str, dict[str, Any]]:
    session_db_path = resolve_account_database_dir(account_dir) / "session.db"
    if not session_db_path.exists():
        return {}

    conn = sqlite3.connect(str(session_db_path))
    conn.row_factory = sqlite3.Row
    try:
        columns = _sqlite_table_columns(conn, "SessionTable")
        if "username" not in columns:
            return {}
        hidden_expr = "is_hidden" if "is_hidden" in columns else "0"
        rows = conn.execute(f"SELECT username, {hidden_expr} AS is_hidden FROM SessionTable").fetchall()
    finally:
        conn.close()

    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        u = str(r["username"] or "").strip()
        if not u:
            continue
        if not _should_keep_session(u, include_official=True):
            continue
        out[u] = {
            "is_hidden": 1 if int(r["is_hidden"] or 0) == 1 else 0,
            "is_official": 1 if u.startswith("gh_") else 0,
        }
    return out


def _load_contact_usernames_for_index(account_dir: Path) -> set[str]:
    contact_db_path = resolve_account_database_dir(account_dir) / "contact.db"
    if not contact_db_path.exists():
        return set()

    out: set[str] = set()
    conn = sqlite3.connect(str(contact_db_path))
    conn.row_factory = sqlite3.Row
    try:
        for table in ("contact", "stranger"):
            columns = _sqlite_table_columns(conn, table)
            if "username" not in columns:
                continue
            try:
                rows = conn.execute(f"SELECT username FROM {_quote_ident(table)}").fetchall()
            except Exception:
                continue
            for row in rows:
                username = _decode_sqlite_text(row["username"]).strip()
                if username:
                    out.add(username)
    finally:
        conn.close()
    return out


def _load_name2id_usernames_for_index(conn: sqlite3.Connection) -> set[str]:
    columns = _sqlite_table_columns(conn, "Name2Id")
    username_col = "user_name" if "user_name" in columns else ("username" if "username" in columns else "")
    if not username_col:
        return set()

    out: set[str] = set()
    try:
        rows = conn.execute(f"SELECT {_quote_ident(username_col)} AS username FROM Name2Id").fetchall()
    except Exception:
        return out

    for row in rows:
        try:
            raw = row["username"] if isinstance(row, sqlite3.Row) else row[0]
        except Exception:
            raw = ""
        username = _decode_sqlite_text(raw).strip()
        if username:
            out.add(username)
    return out


def _load_message_backed_index_targets(*, account_dir: Path, seed_usernames: set[str]) -> set[str]:
    out: set[str] = set()
    self_aliases = {account_dir.name, resolve_account_username(account_dir)}
    for db_path in _iter_message_db_paths(account_dir):
        conn: Optional[sqlite3.Connection] = None
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            table_names = [_decode_sqlite_text(r["name"] if isinstance(r, sqlite3.Row) else r[0]).strip() for r in rows]
            lower_to_actual = {name.lower(): name for name in table_names if name}
            if not lower_to_actual:
                continue

            candidates = set(seed_usernames)
            candidates.update(_load_name2id_usernames_for_index(conn))
            for username in candidates:
                u = str(username or "").strip()
                if not u or u in self_aliases:
                    continue
                if not _should_keep_session(u, include_official=True):
                    continue
                if _resolve_msg_table_name_by_map(lower_to_actual, u):
                    out.add(u)
        except Exception:
            continue
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    return out


def _load_sessions_for_index(account_dir: Path) -> dict[str, dict[str, Any]]:
    sessions = _load_session_table_targets(account_dir)
    contact_usernames = _load_contact_usernames_for_index(account_dir)
    message_backed_usernames = _load_message_backed_index_targets(
        account_dir=account_dir,
        seed_usernames=contact_usernames,
    )

    for u in sorted(message_backed_usernames):
        if u in sessions:
            continue
        sessions[u] = {
            "is_hidden": 0,
            "is_official": 1 if u.startswith("gh_") else 0,
        }

    return sessions


def _init_index_db(conn: sqlite3.Connection) -> None:
    # NOTE: This index DB is built as a temporary file and then atomically swapped in.
    # Using WAL here would create `-wal/-shm` side files that are *not* swapped together.
    # Because the file is disposable until `os.replace`, journal=OFF is acceptable and
    # avoids a large amount of write amplification during first full index builds.
    try:
        conn.execute("PRAGMA journal_mode=OFF")
    except Exception:
        conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA locking_mode=EXCLUSIVE")
    conn.execute("PRAGMA cache_size=-131072")
    try:
        conn.execute("PRAGMA mmap_size=268435456")
    except Exception:
        pass

    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS message_fts USING fts5(
            text,
            username UNINDEXED,
            render_type UNINDEXED,
            create_time UNINDEXED,
            sort_seq UNINDEXED,
            local_id UNINDEXED,
            server_id UNINDEXED,
            local_type UNINDEXED,
            db_stem UNINDEXED,
            table_name UNINDEXED,
            sender_username UNINDEXED,
            is_hidden UNINDEXED,
            is_official UNINDEXED,
            payload_json UNINDEXED,
            tokenize='unicode61'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS message_meta (
            rowid INTEGER PRIMARY KEY,
            text TEXT NOT NULL,
            username TEXT NOT NULL,
            render_type TEXT NOT NULL,
            create_time INTEGER NOT NULL DEFAULT 0,
            sort_seq INTEGER NOT NULL DEFAULT 0,
            local_id INTEGER NOT NULL DEFAULT 0,
            server_id INTEGER NOT NULL DEFAULT 0,
            local_type INTEGER NOT NULL DEFAULT 0,
            db_stem TEXT NOT NULL,
            table_name TEXT NOT NULL,
            sender_username TEXT NOT NULL,
            is_hidden INTEGER NOT NULL DEFAULT 0,
            is_official INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS message_token_stats (
            token TEXT PRIMARY KEY,
            doc_count INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        ("schema_version", str(_SCHEMA_VERSION)),
    )


def _create_message_meta_indexes(conn: sqlite3.Connection) -> None:
    # Create these after the bulk insert. Maintaining secondary B-tree indexes
    # during the first full SQLite scan noticeably slows index builds on large accounts.
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_message_meta_time
        ON message_meta(create_time DESC, sort_seq DESC, local_id DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_message_meta_visible_time
        ON message_meta(is_hidden, is_official, create_time DESC, sort_seq DESC, local_id DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_message_meta_username_time
        ON message_meta(username, create_time DESC, sort_seq DESC, local_id DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_message_meta_sender_time
        ON message_meta(sender_username, create_time DESC, sort_seq DESC, local_id DESC)
        """
    )


def _safe_begin(conn: sqlite3.Connection) -> None:
    try:
        if not conn.in_transaction:
            conn.execute("BEGIN")
    except sqlite3.OperationalError as e:
        # Some environments may report `in_transaction` inconsistently; avoid hard failing on nested BEGIN.
        if "within a transaction" in str(e).lower():
            return
        raise


_INDEX_PAYLOAD_KEYS = (
    "id",
    "db",
    "table",
    "username",
    "conversationUsername",
    "localId",
    "serverId",
    "serverIdStr",
    "type",
    "renderType",
    "createTime",
    "sortSeq",
    "senderUsername",
    "senderDisplayName",
    "senderAvatar",
    "isSent",
    "content",
    "title",
    "url",
    "quoteTitle",
    "quoteContent",
    "fileMd5",
    "fileSize",
    "imageMd5",
    "imageFileId",
    "videoMd5",
    "videoFileId",
    "amount",
    "locationPoiname",
    "locationLabel",
    "source",
    "isRevoked",
    "revokeTime",
)


def _json_dumps_compact(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return "{}"


def _update_single_char_token_stats(counter: dict[str, int], token_text: str) -> None:
    """Track per-document single-character token frequency for high-hit search planning."""

    try:
        tokens = set(str(token_text or "").split())
    except Exception:
        return
    for token in tokens:
        t = str(token or "").strip()
        if len(t) == 1:
            counter[t] = int(counter.get(t, 0)) + 1


def _flush_index_batch(
    conn: sqlite3.Connection,
    *,
    insert_fts_sql: str,
    insert_meta_sql: str,
    batch: list[tuple[Any, ...]],
    next_rowid: int,
) -> tuple[int, int]:
    if not batch:
        return 0, int(next_rowid)

    fts_rows: list[tuple[Any, ...]] = []
    meta_rows: list[tuple[Any, ...]] = []
    rowid = int(next_rowid)
    for rec in batch:
        fts_rows.append((rowid, *rec))
        meta_rows.append(
            (
                rowid,
                rec[0],   # text
                rec[1],   # username
                rec[2],   # render_type
                rec[3],   # create_time
                rec[4],   # sort_seq
                rec[5],   # local_id
                rec[6],   # server_id
                rec[7],   # local_type
                rec[8],   # db_stem
                rec[9],   # table_name
                rec[10],  # sender_username
                rec[11],  # is_hidden
                rec[12],  # is_official
            )
        )
        rowid += 1

    conn.executemany(insert_fts_sql, fts_rows)
    conn.executemany(insert_meta_sql, meta_rows)
    count = len(batch)
    batch.clear()
    return count, rowid


def _hit_to_index_payload(hit: dict[str, Any], *, source: str) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in _INDEX_PAYLOAD_KEYS:
        if key in hit and hit.get(key) is not None:
            payload[key] = hit.get(key)
    payload["source"] = source
    return payload


def _anti_revoke_signature(rows: list[dict[str, Any]]) -> str:
    fields = (
        "account", "username", "server_id", "local_id", "is_revoked",
        "revoke_time", "create_time", "sort_seq", "sender_username", "is_sent",
        "local_type", "content", "extra_json",
    )
    records = sorted(tuple(str(row.get(field, "")) for field in fields) for row in rows)
    return hashlib.sha256(json.dumps(records, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _build_worker(account_dir: Path, rebuild: bool, source: str = "decrypted") -> None:
    from .chat_export_service import _Row, _parse_message_for_export
    key = _account_key(account_dir)
    started = time.time()
    tmp_path = _index_db_tmp_path(account_dir)
    final_path = _index_db_path(account_dir)

    self_username = resolve_account_username(account_dir)
    try:
        tmp_path.unlink(missing_ok=True)

        source_norm = _normalize_index_source(source, default="decrypted")
        sessions = _load_sessions_for_index(account_dir)
        if not sessions:
            raise RuntimeError("No sessions found (session.db empty or missing).")

        db_paths = _iter_message_db_paths(account_dir)
        if not db_paths:
            raise RuntimeError("No message databases found for this account.")

        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        all_revoked = get_all_revoked_messages(account_dir)
        anti_revoke_signature = _anti_revoke_signature(all_revoked)
        snapshot_sources = _snapshot_index_sources(account_dir, db_paths, sessions, anti_revoke_signature)
        unchanged_shards = _reuse_snapshot_index(account_dir, tmp_path, snapshot_sources, rebuild)
        conn_fts = sqlite3.connect(str(tmp_path))
        conn_fts.isolation_level = None  # manual transaction control (prevents implicit BEGIN)
        resource_conn = None
        try:
            resource_path = resolve_account_database_dir(account_dir) / 'message_resource.db'
            if resource_path.exists():
                resource_conn = sqlite3.connect(resource_path.resolve().as_uri() + '?mode=ro', uri=True)
                resource_conn.row_factory = sqlite3.Row
            _init_index_db(conn_fts)
            conn_fts.commit()
            insert_sql = (
                "INSERT INTO message_fts("
                "rowid, text, username, render_type, create_time, sort_seq, local_id, server_id, local_type, "
                "db_stem, table_name, sender_username, is_hidden, is_official, payload_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            )
            insert_meta_sql = (
                "INSERT INTO message_meta("
                "rowid, text, username, render_type, create_time, sort_seq, local_id, server_id, local_type, "
                "db_stem, table_name, sender_username, is_hidden, is_official"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            )

            batch: list[tuple[Any, ...]] = []
            _safe_begin(conn_fts)
            token_doc_counts: dict[str, int] = dict(conn_fts.execute('SELECT token,doc_count FROM message_token_stats'))
            if unchanged_shards:
                placeholders = ','.join('?' for _ in unchanged_shards)
                removed = f'SELECT rowid FROM message_meta WHERE db_stem NOT IN ({placeholders})'
                removed_tokens: dict[str, int] = {}
                for (token_text,) in conn_fts.execute(f'SELECT text FROM message_meta WHERE db_stem NOT IN ({placeholders})', sorted(unchanged_shards)):
                    _update_single_char_token_stats(removed_tokens, token_text)
                for token, count in removed_tokens.items():
                    token_doc_counts[token] -= count
                token_doc_counts = {token: count for token, count in token_doc_counts.items() if count > 0}
                conn_fts.execute(f'DELETE FROM message_fts WHERE rowid IN ({removed})', sorted(unchanged_shards))
                conn_fts.execute(f'DELETE FROM message_meta WHERE db_stem NOT IN ({placeholders})', sorted(unchanged_shards))
            indexed = conn_fts.execute('SELECT count(*) FROM message_meta').fetchone()[0]
            fetched = 0
            fetch_calls = 0
            insert_batch_size = _insert_batch_size()
            last_commit_index = 0
            next_rowid = conn_fts.execute('SELECT COALESCE(MAX(rowid),0)+1 FROM message_meta').fetchone()[0]

            _update_build_state(
                key,
                totalConversations=len(sessions),
                completedConversations=0,
                insertBatchSize=insert_batch_size,
                commitEveryMessages=_COMMIT_EVERY_MESSAGES,
                buildMode='incremental' if unchanged_shards else 'full',
                reusedMessages=indexed,
                reusedDatabases=len(unchanged_shards),
                updatedDatabases=len(db_paths) - len(unchanged_shards),
            )

            completed_conversations: set[str] = {row[0] for row in conn_fts.execute('SELECT DISTINCT username FROM message_meta')}
            revoked_map = get_revoked_messages_map(account_dir)
            indexed_servers: set[str] = set()
            indexed_locals: set[tuple[str, int]] = set()
            if all_revoked and unchanged_shards:
                for username, local_id, server_id in conn_fts.execute(
                        "SELECT username,local_id,server_id FROM message_meta WHERE local_type != 10000 AND render_type != 'system'"):
                    if server_id:
                        indexed_servers.add(str(server_id))
                    elif local_id > 0:
                        indexed_locals.add((username, local_id))

            for db_path in db_paths:
                if db_path.stem in unchanged_shards:
                    continue
                _update_build_state(key, currentDb=str(db_path.name))
                msg_conn = sqlite3.connect(str(db_path))
                msg_conn.row_factory = sqlite3.Row
                msg_conn.text_factory = bytes
                try:
                    trows = msg_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                    lower_to_actual: dict[str, str] = {}
                    for x in trows:
                        if not x or x[0] is None:
                            continue
                        nm = _decode_sqlite_text(x[0]).strip()
                        if not nm:
                            continue
                        lower_to_actual[nm.lower()] = nm

                    my_rowid, _matched_self_username = resolve_account_self_rowid(
                        msg_conn,
                        account_dir,
                    )
                    if my_rowid is None:
                        source_rowid, source_match = resolve_account_self_rowid(
                            msg_conn,
                            account_dir,
                            candidates=(self_username,),
                        )
                        if source_rowid is not None:
                            my_rowid, _matched_self_username = source_rowid, source_match
                    db_self_username = _matched_self_username or self_username

                    for conv_username, sess_info in sessions.items():
                        _update_build_state(key, currentConversation=str(conv_username))
                        table_name = _resolve_msg_table_name_by_map(lower_to_actual, conv_username)
                        if not table_name:
                            continue

                        is_group = bool(conv_username.endswith("@chatroom"))
                        quoted_table = _quote_ident(table_name)
                        columns = {_decode_sqlite_text(column[1]).lower() for column in msg_conn.execute(f'PRAGMA table_info({quoted_table})')}
                        packed_select = 'm.packed_info_data AS packed_info_data, ' if 'packed_info_data' in columns else 'NULL AS packed_info_data, '
                        resource_chat_id = _resource_lookup_chat_id(resource_conn, conv_username) if resource_conn is not None else None

                        sql_with_join = (
                            "SELECT "
                            "m.local_id, m.server_id, m.local_type, m.sort_seq, m.real_sender_id, m.create_time, "
                            "m.message_content, m.compress_content, " + packed_select + "n.user_name AS sender_username "
                            f"FROM {quoted_table} m "
                            "LEFT JOIN Name2Id n ON m.real_sender_id = n.rowid"
                        )
                        sql_no_join = (
                            "SELECT "
                            "m.local_id, m.server_id, m.local_type, m.sort_seq, m.real_sender_id, m.create_time, "
                            "m.message_content, m.compress_content, " + packed_select + "'' AS sender_username "
                            f"FROM {quoted_table} m "
                        )

                        try:
                            cursor = msg_conn.execute(sql_with_join)
                        except Exception:
                            cursor = msg_conn.execute(sql_no_join)

                        for r in cursor:
                            hit = _row_to_search_hit(
                                r,
                                db_path=db_path,
                                table_name=table_name,
                                username=conv_username,
                                account_dir=account_dir,
                                is_group=is_group,
                                my_rowid=my_rowid,
                                self_username=db_self_username,
                            )
                            if hit['renderType'] in {'file', 'image', 'video'}:
                                # 与原消息和语义读取共用附件解析，避免轻量搜索字段丢掉来源定位。
                                attachment = _parse_message_for_export(row=_Row(
                                    db_stem=db_path.stem, table_name=table_name, local_id=hit['localId'],
                                    server_id=hit['serverId'], local_type=hit['type'], sort_seq=hit['sortSeq'],
                                    create_time=hit['createTime'], raw_text=_decode_message_content(r['compress_content'], r['message_content']).strip(),
                                    sender_username=hit['senderUsername'], is_sent=hit['isSent'], packed_info_data=r['packed_info_data']),
                                    conv_username=conv_username, is_group=is_group, resource_conn=resource_conn,
                                    resource_chat_id=resource_chat_id)
                                hit.update({field: attachment[field] for field in ('content', 'title', 'quoteTitle', 'quoteContent',
                                    'fileMd5', 'fileSize', 'imageMd5', 'imageFileId', 'videoMd5', 'videoFileId')})

                            sid = str(hit.get("serverIdStr") or hit.get("serverId") or "").strip()
                            lid = int(hit.get("localId") or 0)
                            conv_user = str(hit.get("conversationUsername") or conv_username or "").strip()
                            hit_type = int(hit.get("type") or 0)
                            is_sys = (hit_type == 10000 or hit.get("renderType") == "system")
                            if not is_sys:
                                if sid and sid != "0":
                                    indexed_servers.add(sid)
                                elif lid > 0 and conv_user:
                                    indexed_locals.add((conv_user, lid))

                            match = None
                            if not is_sys:
                                if sid and sid != "0":
                                    match = revoked_map.get(f"server:{sid}")
                                elif lid > 0 and conv_user:
                                    match = revoked_map.get(f"local:{conv_user}:{lid}")
                                    if match is not None and str(match.get("server_id") or "0") != "0":
                                        match = None

                            if match is not None:
                                hit["isRevoked"] = True
                                hit["revokeTime"] = int(match.get("revoke_time") or hit.get("createTime") or 0)

                            hay_items = [
                                str(hit.get("content") or ""),
                                str(hit.get("title") or ""),
                                str(hit.get("url") or ""),
                                str(hit.get("quoteTitle") or ""),
                                str(hit.get("quoteContent") or ""),
                                str(hit.get("amount") or ""),
                            ]
                            if hit.get("isRevoked"):
                                hay_items.insert(0, "【已撤回】")
                            haystack = "\n".join([x for x in hay_items if x.strip()])
                            if not haystack.strip():
                                continue

                            token_text = _to_char_token_text(haystack)
                            if not token_text:
                                continue
                            _update_single_char_token_stats(token_doc_counts, token_text)

                            batch.append(
                                (
                                    token_text,
                                    conv_username,
                                    str(hit.get("renderType") or ""),
                                    int(hit.get("createTime") or 0),
                                    int(hit.get("sortSeq") or 0),
                                    int(hit.get("localId") or 0),
                                    int(hit.get("serverId") or 0),
                                    int(hit.get("type") or 0),
                                    str(db_path.stem),
                                    str(table_name),
                                    str(hit.get("senderUsername") or ""),
                                    int(sess_info.get("is_hidden") or 0),
                                    int(sess_info.get("is_official") or 0),
                                    _json_dumps_compact(_hit_to_index_payload(hit, source="decrypted")),
                                )
                            )

                            if len(batch) >= insert_batch_size:
                                flushed, next_rowid = _flush_index_batch(
                                    conn_fts,
                                    insert_fts_sql=insert_sql,
                                    insert_meta_sql=insert_meta_sql,
                                    batch=batch,
                                    next_rowid=next_rowid,
                                )
                                indexed += flushed
                                elapsed = max(0.001, time.time() - started)
                                _update_build_state(
                                    key,
                                    indexedMessages=int(indexed),
                                    messagesPerSec=round(indexed / elapsed, 1),
                                    uniqueSearchTokens=len(token_doc_counts),
                                )

                                if indexed - last_commit_index >= _COMMIT_EVERY_MESSAGES:
                                    conn_fts.commit()
                                    last_commit_index = indexed
                                _safe_begin(conn_fts)
                        completed_conversations.add(str(conv_username))
                        _update_build_state(key, completedConversations=len(completed_conversations))
                finally:
                    msg_conn.close()

            for rev_row in all_revoked:
                rsid = str(rev_row.get("server_id") or "").strip()
                rlid = int(rev_row.get("local_id") or 0)
                rev_user = str(rev_row.get("username") or "").strip()
                if (
                    rsid in indexed_servers if rsid and rsid != "0"
                    else rlid > 0 and rev_user and (rev_user, rlid) in indexed_locals
                ):
                    continue

                chat_item = format_revoked_message_as_chat_item(rev_row, account_dir)
                conv_username = str(chat_item.get("conversationUsername") or rev_user or "").strip()
                if not conv_username:
                    continue
                sess_info = sessions.get(conv_username, {"is_hidden": 0, "is_official": 0})
                hay_items = [
                    "【已撤回】",
                    str(chat_item.get("content") or ""),
                    str(chat_item.get("title") or ""),
                    str(chat_item.get("url") or ""),
                    str(chat_item.get("quoteTitle") or ""),
                    str(chat_item.get("quoteContent") or ""),
                    str(chat_item.get("amount") or ""),
                ]
                haystack = "\n".join([x for x in hay_items if x.strip()])
                token_text = _to_char_token_text(haystack)
                if not token_text:
                    continue
                _update_single_char_token_stats(token_doc_counts, token_text)

                batch.append(
                    (
                        token_text,
                        conv_username,
                        str(chat_item.get("renderType") or ""),
                        int(chat_item.get("createTime") or 0),
                        int(chat_item.get("sortSeq") or 0),
                        int(chat_item.get("localId") or 0),
                        int(chat_item.get("serverId") or 0),
                        int(chat_item.get("type") or 1),
                        "anti_revoke",
                        "revoked_messages",
                        str(chat_item.get("senderUsername") or ""),
                        int(sess_info.get("is_hidden") or 0),
                        int(sess_info.get("is_official") or 0),
                        _json_dumps_compact(_hit_to_index_payload(chat_item, source="anti_revoke")),
                    )
                )
                if rsid and rsid != "0":
                    indexed_servers.add(rsid)
                elif rlid > 0 and conv_username:
                    indexed_locals.add((conv_username, rlid))

                if len(batch) >= insert_batch_size:
                    flushed, next_rowid = _flush_index_batch(
                        conn_fts,
                        insert_fts_sql=insert_sql,
                        insert_meta_sql=insert_meta_sql,
                        batch=batch,
                        next_rowid=next_rowid,
                    )
                    indexed += flushed
                    elapsed = max(0.001, time.time() - started)
                    _update_build_state(
                        key,
                        indexedMessages=int(indexed),
                        messagesPerSec=round(indexed / elapsed, 1),
                        uniqueSearchTokens=len(token_doc_counts),
                    )

            if batch:
                flushed, next_rowid = _flush_index_batch(
                    conn_fts,
                    insert_fts_sql=insert_sql,
                    insert_meta_sql=insert_meta_sql,
                    batch=batch,
                    next_rowid=next_rowid,
                )
                indexed += flushed
                elapsed = max(0.001, time.time() - started)
                _update_build_state(
                    key,
                    indexedMessages=int(indexed),
                    fetchedMessages=int(fetched),
                    fetchCalls=int(fetch_calls),
                    messagesPerSec=round(indexed / elapsed, 1),
                    uniqueSearchTokens=len(token_doc_counts),
                )

            conn_fts.execute('DELETE FROM message_token_stats')
            if token_doc_counts:
                conn_fts.executemany(
                    "INSERT INTO message_token_stats(token, doc_count) VALUES(?, ?)",
                    sorted((str(token), int(count)) for token, count in token_doc_counts.items()),
                )

            _create_message_meta_indexes(conn_fts)

            conn_fts.commit()

            finished_at = int(time.time())
            conn_fts.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("built_at", str(finished_at)),
            )
            conn_fts.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("message_count", str(indexed)),
            )
            conn_fts.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("source", source_norm),
            )
            conn_fts.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("anti_revoke_signature", anti_revoke_signature),
            )
            conn_fts.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)',
                             ('single_char_token_count', str(len(token_doc_counts))))
            if snapshot_sources is not None:
                conn_fts.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)',
                                 ('snapshot_sources', json.dumps(snapshot_sources, ensure_ascii=False, sort_keys=True)))
            conn_fts.commit()
        finally:
            if resource_conn is not None:
                resource_conn.close()
            conn_fts.close()

        if rebuild or final_path.exists():
            try:
                os.replace(str(tmp_path), str(final_path))
            except Exception:
                if tmp_path.exists():
                    tmp_path.unlink()
                raise
        else:
            os.replace(str(tmp_path), str(final_path))

        duration = max(0.0, time.time() - started)
        _update_build_state(
            key,
            status="ready",
            finishedAt=int(time.time()),
            currentDb="",
            currentConversation="",
            error="",
            durationSec=round(duration, 3),
            indexedMessages=indexed,
            completedConversations=len(completed_conversations),
        )
    except Exception as e:
        logger.exception("Failed to build chat search index")
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            logger.exception("Failed to remove incomplete chat search index: %s", tmp_path)
        _update_build_state(
            key,
            status="error",
            finishedAt=int(time.time()),
            error=str(e),
        )
