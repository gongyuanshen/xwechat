"""Durable user-confirmed matters and due reminders in the existing AI store."""
import json
import time
import uuid
from contextlib import contextmanager

from fastapi import HTTPException


class MatterService:
    def __init__(self, ai, resolve_account):
        self.ai, self.store, self.resolve_account = ai, ai.store, resolve_account

    @contextmanager
    def transaction(self, account):
        with self.ai.account_lifecycle_lock:
            owner = self.resolve_account(account)
            self.ai.deleted_accounts.discard(owner)
            self.store.revoked_accounts.discard(owner)
            with self.store.connection() as db:
                yield db, owner

    @staticmethod
    def decode(row):
        value = json.loads(row['body'])
        fields = {'id', 'account', 'title', 'source', 'status', 'due_at', 'reminder_enabled',
                  'revision', 'reminder_revision', 'notified_revision', 'history', 'created_at', 'updated_at'}
        if (not isinstance(value, dict) or not fields.issubset(value) or value['id'] != row['id']
                or value['account'] != row['account'] or value['status'] not in {'todo', 'in_progress', 'done'}
                or not isinstance(value['source'], dict) or not isinstance(value['history'], list)):
            raise ValueError(f'事项记录损坏: {row["id"]}')
        return value

    def get(self, db, id, account):
        row = db.execute("SELECT id,account,body FROM records WHERE kind='matter' AND id=? AND account=?", (id, account)).fetchone()
        if row is None:
            raise HTTPException(404, '事项不存在')
        return self.decode(row)

    @staticmethod
    def put(db, item):
        db.execute("INSERT INTO records VALUES('matter',?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body,updated=excluded.updated",
                   (item['id'], item['account'], json.dumps(item, ensure_ascii=False), item['updated_at']))
        return item

    def list(self, account):
        with self.store.connection() as db:
            return [self.decode(row) for row in db.execute("SELECT id,account,body FROM records WHERE kind='matter' AND account=? ORDER BY updated DESC", (account,))]

    def create(self, account, fields):
        with self.transaction(account) as (db, owner):
            now = time.time()
            item = {**fields, 'id': uuid.uuid4().hex, 'account': owner, 'status': 'todo', 'revision': 1,
                    'reminder_revision': 1, 'notified_revision': None, 'created_at': now, 'updated_at': now,
                    'history': [{'kind': 'created', 'at': now, 'status': 'todo', 'due_at': fields['due_at'], 'source': fields['source']}]}
            return self.put(db, item)

    def update(self, account, id, revision, fields, source=None):
        with self.transaction(account) as (db, owner):
            item = self.get(db, id, owner)
            if item['revision'] != revision:
                raise HTTPException(409, '事项已变化，请重新打开后确认')
            if source is not None and item['status'] == 'done':
                raise HTTPException(409, '已完成事项不能改期，请先重新打开事项')
            if fields.get('reminder_enabled') and item['due_at'] is None:
                raise HTTPException(422, '请先确认期限再启用提醒')
            now = time.time()
            changes = {key: value for key, value in fields.items() if item[key] != value}
            if not changes:
                raise HTTPException(422, '事项没有变化')
            history = {'kind': 'rescheduled' if source is not None else 'updated', 'at': now,
                       'before': {key: item[key] for key in changes}, 'after': changes}
            if source is not None:
                history.update(source=source, previous_due_at=item['due_at'], due_at=fields['due_at'])
            reset_reminder = ('due_at' in changes or 'reminder_enabled' in changes or changes.get('status') == 'done')
            if reset_reminder:
                db.execute("DELETE FROM events WHERE account=? AND kind='notification' AND delivered=0 AND json_extract(body,'$.target.matter_id')=?", (owner, id))
                item['reminder_revision'] += 1
            return self.put(db, {**item, **changes, 'revision': revision + 1, 'updated_at': now,
                                 'history': [*item['history'], history]})

    def dispatch_due(self, now=None):
        now = time.time() if now is None else now
        with self.ai.account_lifecycle_lock, self.store.connection() as db:
            rows = db.execute("SELECT id,account,body FROM records WHERE kind='matter' "
                "AND json_extract(body,'$.status') != 'done' AND json_extract(body,'$.reminder_enabled')=1 "
                "AND json_extract(body,'$.due_at')<=? AND json_extract(body,'$.notified_revision') IS NOT json_extract(body,'$.reminder_revision')", (now,)).fetchall()
            for row in rows:
                item = self.decode(row)
                if item['account'] in self.store.revoked_accounts:
                    continue
                # The OS notification does not expose source text or titles, even
                # when the application's current privacy toggle is off.
                body = {'title': '事项已到期', 'body': '打开事项面板查看并更新状态',
                        'target': {'account': item['account'], 'matter_id': item['id']}}
                db.execute("INSERT OR IGNORE INTO events(account,kind,body,unique_key,created) VALUES(?,'notification',?,?,?)",
                    (item['account'], json.dumps(body, ensure_ascii=False), f"matter:{item['id']}:{item['reminder_revision']}", now))
                self.put(db, {**item, 'notified_revision': item['reminder_revision']})
        # The existing /ai/events notification stream reads committed events
        # every two seconds. No second scheduler or delivery channel is needed.
