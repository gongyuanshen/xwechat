"""人物、消息和图片分别编号；映射随现有运行结果和检查点保存。"""
import hashlib
import re
from urllib.parse import urlencode


def reference_id(kind, account, identity):
    return hashlib.sha256(f'{kind}:{account}:{identity}'.encode()).hexdigest()[:24]


def contains_person_name(text, name):
    """数字和英文姓名必须有边界，不能把 3300 元识别为名为 330 的人。"""
    # 先做字面检索，避免对每条消息反复编译整个联系人目录的正则。
    if len(name) < 2 or name not in text:
        return False
    edge = r'[A-Za-z0-9_]'
    pattern = (rf'(?<!{edge})' if re.match(edge, name[0]) else '') + re.escape(name)
    pattern += rf'(?!{edge})' if re.match(edge, name[-1]) else ''
    return re.search(pattern, text) is not None


def source_display(value, account=''):
    result = {k: v for k, v in value.items() if k != 'media'}
    raw = value.get('media') or {}
    sender = value.get('sender_id') or raw.get('senderUsername') or ''
    result.update(text=value.get('text', '')[:1200], excerpt=len(value.get('text', '')) > 1200)
    if account and sender:
        result['sender_avatar_path'] = '/chat/avatar?' + urlencode({'account': account, 'username': sender})
    return result


def material_references(account, messages, contacts=(), existing=None):
    refs = dict(existing or {})
    by_scope, by_id_scope = {}, {}
    for contact in contacts:
        by_id_scope.setdefault(contact.get('conversation', ''), {})[contact['username']] = contact
        by_name = by_scope.setdefault(contact.get('conversation', ''), {})
        for name in set([contact['name'], *contact.get('aliases', [])]):
            by_name.setdefault(name, {})[contact['username']] = contact
    for m in messages:
        raw = m.get('media') or {}
        sender = m.get('sender_id') or raw.get('senderUsername')
        people = []
        if sender:
            people.append({'username': sender, 'name': m.get('sender') or sender,
                           'aliases': m.get('sender_aliases', [])})
        # 微信元数据中的 @ ID 和引用消息发送者比姓名匹配更明确。
        directory = {**by_id_scope.get('', {}), **by_id_scope.get(m['username'], {})}
        explicit = list(dict.fromkeys([*(raw.get('atUsernames') or []), raw.get('quoteUsername')]))
        for username in explicit:
            if not isinstance(username, str) or not username or username == 'notify@all' or username.endswith('@chatroom'):
                continue
            person = directory.get(username, {'username': username, 'name': username})
            if not person.get('isGroup'):
                people.append({**person, 'mentioned': True})
        # 仅无歧义的目录姓名才生成文字提及引用；同名不能猜身份。
        global_names, scoped_names = by_scope.get('', {}), by_scope.get(m['username'], {})
        for name in sorted(global_names.keys() | scoped_names.keys(), key=lambda n: (-len(n), n)):
            if contains_person_name(m.get('text', ''), name):
                matches = {**global_names.get(name, {}), **scoped_names.get(name, {})}
                if len(matches) != 1:
                    continue
                person = next(iter(matches.values()))
                if not person.get('isGroup') and not any(p['username'] == person['username'] and p.get('mentioned') for p in people):
                    people.append({**person, 'name': name, 'mentioned': True})
        for person in people:
            key = reference_id('person', account, person['username'])
            previous = refs.get(key) or {}
            refs[key] = {'id': key, 'kind': 'person', 'username': person['username'], 'name': person['name'],
                         'avatar_path': '/chat/avatar?' + urlencode({'account': account, 'username': person['username']}),
                         'sources': list(dict.fromkeys([*previous.get('sources', []), m['source']])),
                         'aliases': list(dict.fromkeys([*previous.get('aliases', []), *person.get('aliases', []), person['name']])),
                         'mentioned_sources': list(dict.fromkeys([*previous.get('mentioned_sources', []), *([m['source']] if person.get('mentioned') else [])]))}
        if m.get('kind') == 'image' or raw.get('renderType') == 'image':
            key = reference_id('image', account, f"{m['username']}:{m['anchor']}")
            params = {'account': account, 'username': m['username']}
            if raw.get('imageMd5'): params['md5'] = raw['imageMd5']
            elif raw.get('imageFileId'): params['file_id'] = raw['imageFileId']
            path = '/chat/media/image?' + urlencode(params) if len(params) > 2 else ''
            refs[key] = {'id': key, 'kind': 'image', 'source': m['source'], 'path': path,
                         'label': '聊天图片', 'missing': not bool(path)}
    return refs


def cited_references(text, refs):
    wanted = re.findall(r'\[\[(person|image):([a-f0-9]{24})\]\]', text or '', re.I)
    return [refs[key.lower()] for kind, key in dict.fromkeys(wanted)
            if key.lower() in refs and refs[key.lower()].get('kind') == kind.lower()]


def valid_answer_references(text, evidence, references):
    if '[[' in re.sub(r'\[\[[^\[\]]+\]\]', '', text):
        return False
    for marker in re.findall(r'\[\[([^\]]+)\]\]', text):
        if ':' not in marker:
            if marker.lower() not in evidence:
                return False
            continue
        kind, key = marker.lower().split(':', 1)
        ref = references.get(key)
        if kind not in ('person', 'image') or not ref or ref.get('kind') != kind:
            return False
        sources = ref.get('sources', []) if kind == 'person' else [ref.get('source')]
        if not sources or not any(source in evidence for source in sources):
            return False
    return True


def continuation_suffixes(prefix, evidence, references):
    """半截引用只允许接成现有合法编号，不让模型把正文写进协议标记。"""
    tail = re.search(r'\[\[(?:(?:person|image):)?[a-f0-9]{0,24}\]?$', prefix, re.I)
    if not tail:
        return None
    markers = [f'[[{key}]]' for key in evidence]
    for key, ref in references.items():
        marker = f'[[{ref.get("kind")}:{key}]]'
        if valid_answer_references(marker, evidence, references):
            markers.extend([marker, f'[[{key}]]'])
    return [marker[len(tail[0]):] for marker in markers if marker.lower().startswith(tail[0].lower())]


def valid_answer_continuation(prefix, suffix, evidence, references, *, complete=False, remaining=...):
    remaining = continuation_suffixes(prefix, evidence, references) if remaining is ... else remaining
    if remaining is not None and not any(
        suffix.lower().startswith(end.lower()) or (not complete and end.lower().startswith(suffix.lower()))
        for end in remaining
    ):
        return False
    # 检查新输出的开头，允许后文再次提到事实，但不能从已有标题或长开头重写。
    beginning, new = prefix.lstrip(), suffix.lstrip()
    if remaining:
        completed = next((end for end in remaining if suffix.lower().startswith(end.lower())), None)
        if completed is not None:
            new = suffix[len(completed):].lstrip()
    first_line = beginning.split('\n', 1)[0]
    if (len(beginning) >= 80 and new.startswith(beginning[:80])) or (
        first_line.startswith('#') and len(first_line) >= 8 and
        (new == first_line and complete or new.startswith(first_line + '\n'))
    ):
        return False
    if complete:
        # 已闭合标记由现有身份校验检查；其余协议开头不能作为完整回答保存。
        rest = re.sub(r'\[\[[^\[\]]+\]\]', '', prefix + suffix)
        if '[[' in rest:
            return False
    return True
