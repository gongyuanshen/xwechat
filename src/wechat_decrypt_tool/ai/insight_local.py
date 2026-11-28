"""Local classification and explicit statistical portraits; no generated prose.

Question wording/style and relationship weights adapted from WechatVibe's
electron/laya/{catalog,style,personality,options}.ts (Apache-2.0).
See resources/licenses/wechatvibe-NOTICE.txt. Aggregation is independent.
"""
from collections import Counter
import re


RULE_VERSION = 'laya-statistics-v1'
EMOTIONS = dict(happy='开心', affectionate='温暖', neutral='中性', amused='愉悦', sad='难过', anxious='焦虑', angry='生气')
INTENTS = {'small talk': '闲聊', 'share news': '分享', 'ask question': '提问', 'seek comfort': '寻求支持',
           'give comfort': '关心支持', 'make plan': '安排计划', 'flirt': '亲近表达', 'complain': '抱怨',
           'apologize': '道歉', 'joke': '玩笑', 'reject': '拒绝', 'distance': '保持距离'}
STYLES = {
    'energy': ('表达活力', 'no lively social expression', 'lively social expression', 'Does the TARGET sender use an energetic, socially expressive tone?'),
    'humor': ('幽默表达', 'no humor expressed', 'humor expressed', 'Does the TARGET sender deliberately joke or use playful humor?'),
    'calm': ('情绪平和', 'no composed tone shown', 'composed tone shown', 'Does the TARGET sender show a calm, steady tone, including matter-of-fact speech?'),
    'initiative': ('话题主动', 'no conversation initiative', 'conversation initiative', 'Does the TARGET sender initiate or actively advance a topic, question, or plan?'),
    'care': ('关怀支持', 'no care shown', 'care shown', 'Does the TARGET sender show concern, comfort, or practical support for another person?'),
    'closeness': ('亲近表达', 'no affection shown', 'affection shown', 'Does the TARGET sender express personal warmth, closeness, or affection toward someone?'),
}
MBTI_OPTIONS = {
    'EI': ['no stated energy preference', 'outward interaction restores energy', 'inward reflection restores energy'],
    'SN': ['no stated information preference', 'concrete facts and experience', 'patterns and possibilities'],
    'TF': ['no stated decision preference', 'logical principles in decisions', "personal values and people's impact"],
    'JP': ['no stated external world preference', 'structured closure and schedules', 'flexible exploration of options'],
}
MBTI_INSTRUCTIONS = {
    'EI': "Classify only the sender's stated recurring preference for restoring energy. Routine chat or talking a lot is not evidence. If no self-described stable preference, choose no stated preference.",
    'SN': "Classify only the sender's stated recurring preference for taking in information. Mentioning a fact or idea once is not a stable preference. If not self-described, choose no stated preference.",
    'TF': "Classify only the sender's stated recurring basis for making decisions. Emotion alone is not evidence. If there is no self-described stable decision preference, choose no stated preference.",
    'JP': "Classify only the sender's stated recurring preference for organizing the external world. One appointment is not evidence. If no self-described stable preference, choose no stated preference.",
}
RELATIONSHIP_WEIGHTS = {'romantic': 1., 'warm': .55, 'neutral': 0., 'distant': -.45, 'rejecting': -1.}
SCOPE_OPTIONS = ['no enduring preference stated', 'sender states a recurring personal preference']
STOPWORDS = {'这个', '那个', '我们', '你们', '他们', '自己', '就是', '可以', '一下', '然后', '不是', '没有', '什么', '怎么', '真的', '还是', '还有'}


def question(instructions, criteria):
    return dict(type='choice', instructions=instructions, criteria=list(criteria))


def questions_for(item, *, allow_affinity, allow_mbti):
    result = {
        'emotion': question("Which ONE broad emotion does the sender of the TARGET message feel? Judge the sender's emotion, not the recipient's illness or their conversational purpose. Use neutral for matter-of-fact messages.", EMOTIONS),
        'intent': question("What is the sender of the TARGET message mainly trying to do? flirt = romantic interest; reject = refuse an advance; distance = cool down or pull back; make plan = arrange to meet; give comfort = care for the other; seek comfort = ask for support.", INTENTS),
    }
    if item['target']:
        result.update({f'style_{key}': question(instruction + ' Judge only displayed behaviour in the TARGET message.', [negative, positive])
                       for key, (_, negative, positive, instruction) in STYLES.items()})
        if allow_affinity:
            result['relationship'] = question('From the sender of the TARGET message toward the recipient, what relationship signal does the TARGET message carry? Choose romantic for clear romantic interest, warm for friendly affection, neutral for ordinary or task talk, distant for cooling off or emotional distance, and rejecting for an explicit refusal or push-away.', RELATIONSHIP_WEIGHTS)
        if allow_mbti:
            result['mbti_scope'] = question('Does the TARGET sender explicitly describe their own enduring or recurring personal preference across situations? A greeting, refusal, kind act, emotion, ordinary plan or isolated fact is NOT preference evidence.', SCOPE_OPTIONS)
            result.update({f'mbti_{axis}': question(MBTI_INSTRUCTIONS[axis], options) for axis, options in MBTI_OPTIONS.items()})
    return result


def message_state(item, context, *, private_chat=False):
    def speaker(message):
        role = ('them' if message['target'] else 'me') if private_chat else 'group member'
        return f"{role}; sender_id={message['sender_id']}"
    lines = ['TARGET message to judge:', f"[{speaker(item)}]: {item['text']}"]
    if item['quote_context']:
        lines += ['', 'Quoted words (context only, NOT current TARGET text):', item['quote_context']]
    if context:
        lines += ['', 'Recent context before the target (oldest first):']
        lines += [f"[{speaker(other)}]: {other['text']}" for other in context]
    return '\n'.join(lines)


def evidence(text, sources):
    return dict(text=text, sources=list(dict.fromkeys(sources))[:3])


def keep_source(items, source, strength):
    items.append((strength, source))
    items.sort(key=lambda pair: pair[0], reverse=True)
    del items[3:]


class LocalPortrait:
    """Bounded source representatives plus cumulative sufficient statistics."""
    def __init__(self, *, group, member=False):
        self.group, self.member = group, member
        self.count = 0
        self.styles = {key: 0. for key in STYLES}
        self.emotions, self.intents, self.words = Counter(), Counter(), Counter()
        self.sources = {}
        self.axes = {axis: [0., 0., 0] for axis in MBTI_OPTIONS}
        self.relationship_sum = self.relationship_ranked = 0.

    def _source(self, key, source, strength):
        keep_source(self.sources.setdefault(key, []), source, strength)

    def _sources(self, key):
        return [source for _, source in self.sources.get(key, [])]

    def add(self, item, answers, label):
        if not item['target']:
            return
        source = item['source']
        self.count += 1
        self._source('summary', source, 1)
        for key in STYLES:
            probability = answers[f'style_{key}']['probabilities'][STYLES[key][2]]
            self.styles[key] += probability
            self._source(key, source, abs(probability - .5))
        for key, totals in [('emotion', self.emotions), ('intent', self.intents)]:
            for name, probability in answers[key]['probabilities'].items():
                totals[name] += probability
                self._source(f'{key}:{name}', source, probability)
        if not self.group:
            value = sum(answers['relationship']['probabilities'][key] * weight for key, weight in RELATIONSHIP_WEIGHTS.items())
            self.relationship_sum += value
            self.relationship_ranked += (self.count - 1) * value
            self._source('relationship', source, abs(value))
        if 'mbti_scope' in answers:
            scope = answers['mbti_scope']['probabilities']
            if scope[SCOPE_OPTIONS[1]] > scope[SCOPE_OPTIONS[0]]:
                for axis, (unknown, left, right) in MBTI_OPTIONS.items():
                    probabilities = answers[f'mbti_{axis}']['probabilities']
                    if probabilities[unknown] >= max(probabilities[left], probabilities[right]):
                        continue
                    self.axes[axis][0] += probabilities[left]
                    self.axes[axis][1] += probabilities[right]
                    self.axes[axis][2] += 1
                    self._source(axis, source, max(probabilities[left], probabilities[right]))
        import jieba
        words = Counter(word.lower() for word in jieba.lcut(item['text'])
                        if word not in STOPWORDS and re.fullmatch(r'[\u4e00-\u9fff]{2,8}|[A-Za-z]{3,24}', word))
        self.words.update(words)
        for word, count in words.items():
            self._source(f'word:{word}', source, count)

    def portrait(self):
        if not self.count:
            return None
        traits = {key: dict(score=int(100 * self.styles[key] / self.count + .5),
            reason=f'本地统计：{self.count} 条目标文本的「{STYLES[key][0]}」正向模型信号均值；不是人格量表。',
            sources=self._sources(key)) for key in STYLES}
        emotion = max(self.emotions, key=self.emotions.get)
        intent = max(self.intents, key=self.intents.get)
        subject = '群内发言' if self.group and not self.member else '目标本人发言'
        intent_text = f'主要意图候选为「{INTENTS[intent]}」' if self.intents[intent] / self.count >= .5 else '意图信号分散，暂无明确主类'
        emotion_text = f'主要情绪候选为「{EMOTIONS[emotion]}」' if self.emotions[emotion] / self.count >= .5 else '情绪信号分散，暂无明确主类'
        summary = evidence(f'本地统计：已分析 {self.count} 条{subject}；{intent_text}；{emotion_text}。',
                           self._sources('intent:' + intent) + self._sources('emotion:' + emotion))
        topics = [evidence(f'常见用词「{word}」：出现 {count} 次。', self._sources('word:' + word))
                  for word, count in self.words.most_common(6) if count >= 2]
        communication = [evidence(f'交流意图「{INTENTS[name]}」：平均模型信号 {total / self.count:.1%}。', self._sources('intent:' + name))
                         for name, total in self.intents.most_common(3)]
        affinity = None
        if not self.group:
            mean = self.relationship_sum if self.count == 1 else (
                .5 * self.relationship_sum + .5 * self.relationship_ranked / (self.count - 1)) / (.75 * self.count)
            affinity = dict(score=int(50 + 50 * mean + .5),
                reason='本地统计：仅使用对方发言的关系信号，按时间由近及远加权；50 表示模型关系信号居中，不代表真实心意或恋爱概率。',
                sources=self._sources('relationship'))
        mbti = None
        if self.count >= 100 and (not self.group or self.member):
            mbti = {axis: dict(score=int(100 * left / (left + right) + .5) if count else None,
                reason=f'本地统计：{count} 条目标自述稳定偏好信号；分数高表示 {axis[0]}。' if count else '未发现目标自述的稳定偏好证据。',
                sources=self._sources(axis)) for axis, (left, right, count) in self.axes.items()}
        return dict(summary=summary, topics=topics, communication=communication,
            mood=evidence(f'本地统计：主要情绪候选「{EMOTIONS[emotion]}」，平均模型信号 {self.emotions[emotion] / self.count:.1%}。', self._sources('emotion:' + emotion)) if self.emotions[emotion] / self.count >= .5 else None,
            traits=traits, affinity=affinity, mbti=mbti,
            uncertain=['由 Laya 分类结果按固定统计规则整理，并非生成式文字分析。',
                       '分类分值与六维分数不代表心理测量准确率；常见用词不等于完整话题。',
                       '本地模型每条消息独立判断，使用至多三条相邻消息作为语境；仅描述所选范围。'])
