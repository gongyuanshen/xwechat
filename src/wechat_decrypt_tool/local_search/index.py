"""账号独立索引；片段只用于召回，返回时重新过滤原消息。"""
from ..ai.diagnostics import observed, event as diagnostic_event
import logging
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time


class LiteralIndexNotReady(RuntimeError):
    code = 'literal_index_migrating'

    def __init__(self, coverage):
        super().__init__('本地字面索引正在升级，混合检索尚不可用；请等待升级完成后重试')
        self.coverage = coverage


def _literal_token_text(text):
    # 每个原字符对应一个 ASCII token；空白、标点和 emoji 都占据原有位置。
    return ' '.join(f'x{ord(char):x}' for char in text.lower())


def retrieval_text(message):
    """原消息与本地派生文字分开标注，供字面、向量及 Agent 共用。"""
    extracted = message.get('local_attachment_text', '')
    original = message.get('original_text', message['text'])
    if not extracted:
        return original
    method = message.get('local_attachment_method', 'document_text')
    labels = {'document_text': '本地文档提取文字', 'local_ocr': '本地 OCR 识别文字'}
    if method not in labels:
        raise ValueError(f'附件检索文字来源无效：{method}')
    return '\n'.join((original, f'[{labels[method]}]', extracted)).strip()


class SemanticIndex:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            statements = [
                '''CREATE TABLE IF NOT EXISTS messages(generation TEXT, source TEXT, username TEXT, sender TEXT,
                    created INTEGER, kind TEXT, body TEXT, PRIMARY KEY(generation,source))''',
                'CREATE INDEX IF NOT EXISTS msg_filter ON messages(generation,username,created)',
                'CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY,generation TEXT,username TEXT,vector BLOB)',
                'CREATE INDEX IF NOT EXISTS chunks_filter ON chunks(generation,username,id)',
                'CREATE TABLE IF NOT EXISTS members(chunk TEXT,source TEXT,PRIMARY KEY(chunk,source))',
                'CREATE INDEX IF NOT EXISTS members_source ON members(source,chunk)',
                'CREATE TABLE IF NOT EXISTS progress(job TEXT PRIMARY KEY,body TEXT)',
                'CREATE TABLE IF NOT EXISTS fts_migration(generation TEXT PRIMARY KEY,source TEXT,complete INTEGER NOT NULL)',
            ]
            for statement in statements:
                db.execute(statement)
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='messages_fts'").fetchone():
                db.execute("CREATE VIRTUAL TABLE messages_fts USING fts5(tokens, tokenize='ascii')")
                db.execute('INSERT INTO fts_migration SELECT DISTINCT generation,NULL,0 FROM messages')
            if db.execute('PRAGMA user_version').fetchone()[0] < 1:
                # 旧 FTS 只包含原消息正文；复用逐批迁移，不重算已有向量。
                db.execute('INSERT OR REPLACE INTO fts_migration SELECT DISTINCT generation,NULL,0 FROM messages')
                db.execute('PRAGMA user_version=1')

    @contextmanager
    def connection(self):
        import sqlite_vec
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            if not hasattr(db, 'enable_load_extension'):
                raise RuntimeError('当前 Python 的 SQLite 不支持本地检索扩展，请使用应用内置后端或 uv 管理的 Python 3.11')
            try:
                db.enable_load_extension(True)
                sqlite_vec.load(db)
            finally:
                db.enable_load_extension(False)
            db.execute('PRAGMA journal_mode=WAL')
            with db: yield db
        except Exception as error:
            diagnostic_event('index.transaction.rolled_back', level=logging.ERROR, error=error, committed=False)
            raise
        finally:
            db.close()

    def progress(self, job):
        with self.connection() as db:
            row = db.execute('SELECT body FROM progress WHERE job=?', (job,)).fetchone()
            return json.loads(row[0]) if row else None

    def stats(self, generation):
        """统计当前可用索引，区别于单次任务新增或重算的片段数量。"""
        with self.connection() as db:
            messages = db.execute('SELECT count(*) FROM messages WHERE generation=?', (generation,)).fetchone()[0]
            chunks = db.execute('SELECT count(*) FROM chunks WHERE generation=?', (generation,)).fetchone()[0]
        return {'messages': messages, 'chunks': chunks}

    @staticmethod
    def _fts_status(db, generation=None):
        sql, params = 'SELECT count(*) FROM fts_migration WHERE complete=0', []
        if generation is not None:
            sql += ' AND generation=?'
            params.append(generation)
        pending = db.execute(sql, params).fetchone()[0]
        return {'complete': pending == 0, 'pending': pending, 'generation': generation}

    def fts_status(self, generation=None):
        with self.connection() as db:
            return self._fts_status(db, generation)

    @observed('index.fts.backfill')
    def backfill_fts(self, active_generation=None, batch_size=500, checkpoint=None):
        """一次只提交一批及其游标；新增和编辑由 commit 同步维护，不重算旧向量。"""
        if batch_size <= 0:
            raise ValueError('字面索引升级批量必须为正数')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if checkpoint: checkpoint()
            state = db.execute('SELECT generation,source FROM fts_migration WHERE complete=0'
                               ' ORDER BY (generation=?) DESC,generation LIMIT 1', (active_generation,)).fetchone()
            if state is None:
                return {**self._fts_status(db), 'processed': 0, 'migrated_generation': None}
            generation, source = state['generation'], state['source']
            sql, params = 'SELECT rowid,source,body FROM messages WHERE generation=?', [generation]
            if source is not None:
                sql += ' AND source>?'
                params.append(source)
            rows = db.execute(sql + ' ORDER BY source LIMIT ?', [*params, batch_size + 1]).fetchall()
            batch = rows[:batch_size]
            for position, row in enumerate(batch):
                if checkpoint and position % 100 == 0: checkpoint()
                message = json.loads(row['body'])
                db.execute('INSERT OR REPLACE INTO messages_fts(rowid,tokens) VALUES(?,?)',
                           (row['rowid'], _literal_token_text(retrieval_text(message))))
            if checkpoint: checkpoint()
            db.execute('UPDATE fts_migration SET source=?,complete=? WHERE generation=?',
                       (batch[-1]['source'] if batch else source, len(rows) <= batch_size, generation))
            return {**self._fts_status(db), 'processed': len(batch), 'migrated_generation': generation}

    @observed('index.commit')
    def commit(self, generation, messages, chunks, vectors, job, checkpoint=None):
        import sqlite_vec
        if len(chunks) != len(vectors):
            raise ValueError('向量数量与片段数量不一致，未提交当前批次')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if checkpoint: checkpoint()
            db.execute('INSERT OR IGNORE INTO fts_migration VALUES(?,NULL,1)', (generation,))
            for position, message in enumerate(messages):
                if checkpoint and position % 100 == 0: checkpoint()
                source = message['source']
                old = db.execute('SELECT body FROM messages WHERE generation=? AND source=?', (generation, source)).fetchone()
                body = json.dumps(message, ensure_ascii=False)
                if old and old[0] != body:
                    ids = [r[0] for r in db.execute('SELECT c.id FROM chunks c JOIN members m ON m.chunk=c.id WHERE c.generation=? AND m.source=?', (generation, source))]
                    for id in ids:
                        db.execute('DELETE FROM members WHERE chunk=?', (id,))
                        db.execute('DELETE FROM chunks WHERE id=?', (id,))
                db.execute('''INSERT INTO messages VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(generation,source) DO UPDATE SET username=excluded.username,
                    sender=excluded.sender,created=excluded.created,kind=excluded.kind,body=excluded.body''',
                    (generation, source, message['username'], message.get('sender_id', message['sender']), message['time'], message['kind'], body))
                rowid = db.execute('SELECT rowid FROM messages WHERE generation=? AND source=?', (generation, source)).fetchone()[0]
                db.execute('INSERT OR REPLACE INTO messages_fts(rowid,tokens) VALUES(?,?)',
                           (rowid, _literal_token_text(retrieval_text(message))))
            for position, (chunk, vector) in enumerate(zip(chunks, vectors)):
                if checkpoint and position % 100 == 0: checkpoint()
                id = hashlib.sha256((generation + chunk['text'] + ','.join(chunk['sources'])).encode()).hexdigest()
                db.execute('INSERT OR REPLACE INTO chunks VALUES(?,?,?,?)', (id, generation, chunk['username'], sqlite_vec.serialize_float32(vector)))
                db.executemany('INSERT OR IGNORE INTO members VALUES(?,?)', [(id, s) for s in chunk['sources']])
            if checkpoint: checkpoint()
            db.execute('INSERT OR REPLACE INTO progress VALUES(?,?)', (job['id'], json.dumps(job, ensure_ascii=False)))
        diagnostic_event('index.checkpoint.committed', task_id=job['id'], generation=generation, processed=job.get('processed'),
                         offset=job.get('offset'), chat_index=job.get('chat_index'), chunks=len(chunks), count=len(messages), committed=True)

    def existing(self, generation, messages):
        with self.connection() as db:
            return {m['source'] for m in messages if (row := db.execute('SELECT body FROM messages WHERE generation=? AND source=?', (generation, m['source'])).fetchone()) and json.loads(row[0]) == m}

    def affected_messages(self, generation, changed):
        """原文变化时重建旧片段的相邻消息，避免其他证据丢失。"""
        values={m['source']:m for m in changed}
        with self.connection() as db:
            for source in list(values):
                rows=db.execute('''SELECT DISTINCT m.source,m.body FROM members x
                    JOIN chunks c ON c.id=x.chunk AND c.generation=?
                    JOIN members y ON y.chunk=x.chunk
                    JOIN messages m ON m.source=y.source AND m.generation=c.generation
                    WHERE x.source=?''',(generation,source)).fetchall()
                for row in rows: values.setdefault(row['source'],json.loads(row['body']))
        return sorted(values.values(),key=lambda m:(m['username'],m['time'],m['source']))

    @observed('index.search')
    def keyword(self, generation, query, usernames, start=0, end=2**53, sender=None, kinds=None, limit=200):
        """从已提交原文召回关键词，不受向量候选截断影响，也不等待全账号完成。"""
        from ..chat_helpers import _make_search_tokens, _match_tokens
        tokens = _make_search_tokens(query)
        if not usernames or not tokens or limit <= 0:
            return []
        clauses = ['m.generation=?', 'm.username IN (' + ','.join('?' for _ in usernames) + ')',
                   'm.created>=?', 'm.created<=?', 'message_matches(m.body)']
        params = [generation, *usernames, start if start is not None else 0, end if end is not None else 2**53]
        if sender:
            clauses.append('m.sender=?'); params.append(sender)
        if kinds:
            clauses.append('m.kind IN (' + ','.join('?' for _ in kinds) + ')'); params.extend(kinds)
        with self.connection() as db:
            db.execute('BEGIN')
            coverage = self._fts_status(db, generation)
            if not coverage['complete']:
                raise LiteralIndexNotReady(coverage)
            # 与基础搜索共用 Unicode 大小写和多词匹配语义，查询文本从不拼入 SQL。
            db.create_function('message_matches', 1, lambda body: _match_tokens(retrieval_text(json.loads(body)), tokens), deterministic=True)
            phrase = ' AND '.join('"' + _literal_token_text(token) + '"' for token in tokens)
            # 固定从 FTS 候选查 rowid，过滤和字面复核完成后才取上限。
            rows = db.execute('SELECT m.body FROM messages_fts CROSS JOIN messages m ON m.rowid=messages_fts.rowid'
                              ' WHERE messages_fts MATCH ? AND ' + ' AND '.join(clauses)
                              + ' ORDER BY m.created DESC,m.source LIMIT ?', [phrase, *params, limit]).fetchall()
        return [json.loads(row['body']) for row in rows]

    @observed('index.search')
    def search(self, generation, vector, usernames, start=0, end=2**53, sender=None, kinds=None, limit=200):
        import sqlite_vec
        if not usernames or limit <= 0: return []
        clauses = ['m.generation=?', 'm.username IN (' + ','.join('?' for _ in usernames) + ')', 'm.created>=?', 'm.created<=?']
        params = [generation, *usernames, start or 0, end or 2**53]
        if sender:
            clauses.append('m.sender=?'); params.append(sender)
        if kinds:
            clauses.append('m.kind IN (' + ','.join('?' for _ in kinds) + ')'); params.extend(kinds)
        # 每个片段只计算一次距离。按距离逐批展开成员，再加载最终正文，
        # 避免对百万条原消息重复计算共享向量并把全部正文写入排序临时表。
        with self.connection() as db:
            db.execute('BEGIN')
            # 全历史检索不用对每个片段再查询一次时间条件；覆盖索引即可核对边界。
            bounds = db.execute('SELECT MIN(created),MAX(created) FROM messages WHERE generation=? AND username IN ('
                                + ','.join('?' for _ in usernames) + ')', [generation, *usernames]).fetchone()
            unrestricted = (not sender and not kinds and bounds[0] is not None
                            and (start or 0) <= bounds[0] and (end or 2**53) >= bounds[1])
            chunk_where = 'c.generation=? AND c.username IN (' + ','.join('?' for _ in usernames) + ')'
            chunk_params = [generation, *usernames]
            if not unrestricted:
                # 显式筛选先决定哪些片段具有有效原消息，不能让范围外片段挤掉候选。
                chunk_where += (' AND EXISTS (SELECT 1 FROM members x CROSS JOIN messages m'
                                ' ON m.source=x.source WHERE x.chunk=c.id AND ' + ' AND '.join(clauses) + ')')
                chunk_params.extend(params)
            candidates = db.execute('SELECT c.id,vec_distance_cosine(c.vector,?) AS distance FROM chunks c WHERE '
                                    + chunk_where + ' ORDER BY distance,c.id',
                                    [sqlite_vec.serialize_float32(vector), *chunk_params])
            db.execute('CREATE TEMP TABLE search_page(id TEXT PRIMARY KEY,distance REAL)')
            best = {}
            while batch := candidates.fetchmany(max(64, limit)):
                db.execute('DELETE FROM search_page')
                db.executemany('INSERT INTO search_page VALUES(?,?)', [(r['id'], r['distance']) for r in batch])
                # CROSS JOIN 固定从少量候选开始；否则 SQLite 会重新扫描整代原消息。
                hits = db.execute('SELECT m.source,m.created,c.distance FROM search_page c'
                                  ' CROSS JOIN members x ON x.chunk=c.id'
                                  ' CROSS JOIN messages m ON m.source=x.source WHERE ' + ' AND '.join(clauses), params)
                for hit in hits:
                    value = (hit['distance'], -hit['created'], hit['source'])
                    if hit['source'] not in best or value < best[hit['source']]:
                        best[hit['source']] = value
                ranked = sorted(best.values())[:limit]
                best = {row[2]: row for row in ranked}
                # 等距离片段继续读取，保证时间与来源的稳定次序，也处理重叠片段。
                if len(ranked) == limit and batch[-1]['distance'] > ranked[-1][0]:
                    break
            rows = [{'body': db.execute('SELECT body FROM messages WHERE generation=? AND source=?',
                                       (generation, source)).fetchone()[0], 'distance': distance}
                    for distance, _, source in sorted(best.values())]
        return [{'message': json.loads(row['body']), 'distance': row['distance']} for row in rows]

    @observed('index.clear')
    def clear(self, keep=None):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if keep:
                db.execute('DELETE FROM messages_fts WHERE rowid IN (SELECT rowid FROM messages WHERE generation<>?)', (keep,))
                db.execute('DELETE FROM members WHERE chunk IN (SELECT id FROM chunks WHERE generation<>?)', (keep,))
                db.execute('DELETE FROM chunks WHERE generation<>?', (keep,))
                db.execute('DELETE FROM messages WHERE generation<>?', (keep,))
                db.execute('DELETE FROM fts_migration WHERE generation<>?', (keep,))
                db.execute("DELETE FROM progress WHERE json_extract(body,'$.generation')<>?", (keep,))
            else:
                for table in ('messages_fts', 'members', 'chunks', 'messages', 'progress', 'fts_migration'):
                    db.execute('DELETE FROM ' + table)
            db.execute('PRAGMA incremental_vacuum')

    @observed('index.prune')
    def prune(self, generation, usernames, start, end):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            predicate = 'generation=? AND (username NOT IN (' + ','.join('?' for _ in usernames) + ') OR created<? OR created>?)'
            params = [generation, *usernames, start, end]
            db.execute('DELETE FROM messages_fts WHERE rowid IN (SELECT rowid FROM messages WHERE ' + predicate + ')', params)
            db.execute('DELETE FROM messages WHERE ' + predicate, params)
            db.execute('DELETE FROM members WHERE NOT EXISTS (SELECT 1 FROM messages m JOIN chunks c ON c.generation=m.generation WHERE c.id=members.chunk AND m.source=members.source)')
            db.execute('DELETE FROM chunks WHERE NOT EXISTS (SELECT 1 FROM members WHERE chunk=chunks.id)')
            db.execute("DELETE FROM progress WHERE json_extract(body,'$.generation')=?", (generation,))
            db.execute('UPDATE fts_migration SET complete=1 WHERE generation=? AND NOT EXISTS '
                       '(SELECT 1 FROM messages WHERE generation=?)', (generation, generation))


def make_chunks(messages, tokenizer, max_tokens=384, overlap=64):
    chunks, tokens, owners, username, last_time = [], [], [], '', 0
    def flush():
        nonlocal tokens, owners
        for start in range(0,len(tokens),max_tokens-overlap):
            part=tokens[start:start+max_tokens]
            text=tokenizer.decode(part)
            if text.strip(): chunks.append({'text':text,'sources':list(dict.fromkeys(owners[start:start+max_tokens])),'username':username})
            if start+max_tokens>=len(tokens): break
        tokens,owners=[],[]
    for message in messages:
        text = retrieval_text(message).strip()
        if not text: continue
        if username and (username != message['username'] or message['time'] - last_time > 600): flush()
        username, last_time = message['username'], message['time']
        part = tokenizer.encode(text+'\n', add_special_tokens=False).ids
        tokens.extend(part);owners.extend([message['source']]*len(part))
    flush()
    return chunks


def fuse(keyword_hits, semantic_hits):
    """按原消息去重融合，不把分数显示为正确率。"""
    candidates = {}
    for method, hits in [('keyword', keyword_hits), ('semantic', semantic_hits)]:
        for rank, hit in enumerate(hits):
            key = (hit.get('username'), str(hit.get('id') or hit.get('anchorId')))
            item = candidates.setdefault(key, {'hit': dict(hit), 'score': 0, 'methods': []})
            item['score'] += 1 / (60 + rank + 1)
            if method not in item['methods']: item['methods'].append(method)
    return [{**x['hit'], 'matchMethods': x['methods']} for x in sorted(candidates.values(), key=lambda x: -x['score'])]
