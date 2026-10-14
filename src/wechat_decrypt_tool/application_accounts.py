"""Application records remain usable without their original chat snapshot."""
import asyncio
from contextlib import contextmanager

from fastapi import HTTPException

from .ai.service import get_ai_service
from .app_paths import get_output_dir
from .application_backup import KINDS
from .chat_accounts import _safe_account_name, list_chat_account_names
from .routers.ai import account_name as chat_account_name
from .snapshot_registry import begin_account_work


ACCOUNT_RECORD_KINDS = (*KINDS, 'agent_thread')


def application_account_dir(account):
    if not isinstance(account, str) or not account or _safe_account_name(account) != account:
        raise HTTPException(422, '请选择有效的资料账号')
    return get_output_dir() / 'databases' / account


def list_application_accounts():
    chat_accounts = list_chat_account_names()
    with get_ai_service().store.connection() as db:
        names = [row['account'] for row in db.execute(
            f'SELECT DISTINCT account FROM records WHERE kind IN ({",".join("?" for _ in ACCOUNT_RECORD_KINDS)})', ACCOUNT_RECORD_KINDS)]
    for name in names:
        application_account_dir(name)
    return {'accounts': sorted(set(chat_accounts) | set(names)), 'chat_accounts': chat_accounts}


def application_account_name(account):
    application_account_dir(account)
    with get_ai_service().store.connection() as db:
        owned = db.execute(f'SELECT 1 FROM records WHERE account=? AND kind IN ({",".join("?" for _ in ACCOUNT_RECORD_KINDS)}) LIMIT 1',
                           (account, *ACCOUNT_RECORD_KINDS)).fetchone()
    if owned is not None:
        return account
    return chat_account_name(account)


@contextmanager
def application_account_work(account):
    # Share the deletion lease, but saved copies do not read a chat snapshot.
    directory = application_account_dir(account)
    lease = begin_account_work(directory)
    try:
        yield directory
    finally:
        lease.release()


def create_application_account_task(account, function, *args):
    """Reserve saved records before queuing, including pre-start cancellation."""
    lease = begin_account_work(application_account_dir(account))
    coroutine = function(*args)
    try:
        task = asyncio.get_running_loop().create_task(coroutine)
    except BaseException:
        coroutine.close()
        lease.release()
        raise
    task.add_done_callback(lambda _task: lease.release())
    return task
