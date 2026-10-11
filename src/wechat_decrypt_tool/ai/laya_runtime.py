"""Offline CPU inference for the pinned multilingual Laya decision model.

Prompt construction/calibration adapted from laya-mlx at dc3aa6b150cb861d0788fbd421cfd1303de4ed57.
See resources/licenses/laya-NOTICE.txt and laya-LICENSE.txt. Unlike upstream, inputs
that would be truncated are rejected explicitly. No generation or provider fallback.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import threading
import time
import uuid

import numpy as np

from ..local_search.catalog import verify_model
from .insight_local_models import SPEC

QTYPES = {'choice': 0, 'score': 1, 'noul': 2}
INPUT_TYPES = {'input_ids': 'tensor(int64)', 'attention_mask': 'tensor(int64)',
               'marker_pos': 'tensor(int64)', 'marker_mask': 'tensor(bool)', 'qtype': 'tensor(int64)'}
_runtime_status = {'actual_device': None, 'active': False, 'reason': '', 'using_fallback': False}
_runtime_status_lock = threading.Lock()


def get_laya_runtime_status():
    """Only successful inference records activity; never expose input or account data."""
    with _runtime_status_lock:
        return dict(_runtime_status)


class LayaInputError(ValueError):
    """Invalid model configuration, question or input text."""


class LayaContextOverflow(LayaInputError):
    def __init__(self, question_id, section, tokens, limit):
        self.question_id, self.section, self.tokens, self.limit = question_id, section, tokens, limit
        super().__init__(f'Laya 输入超出容量：问题 {question_id}，{section} {tokens} token > {limit}；未截断内容')


class LayaOutputError(RuntimeError):
    """The ONNX graph returned invalid shapes or non-finite values."""


def _json(value, *, ensure_ascii=False):
    try:
        return json.dumps(value, ensure_ascii=ensure_ascii, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise LayaInputError('Laya 问题必须使用有效 JSON 值') from error


def to_internal(question):
    if not isinstance(question, dict) or set(question) - {'type', 'instructions', 'criteria'}:
        raise LayaInputError('Each question must contain type, instructions and optional criteria')
    kind = question.get('type')
    if not isinstance(kind, str) or kind not in QTYPES or 'instructions' not in question:
        raise LayaInputError('Question requires a supported type and instructions')
    criteria = question.get('criteria')
    if kind == 'choice':
        if isinstance(criteria, list):
            if not all(isinstance(label, str) for label in criteria) or len(set(criteria)) != len(criteria):
                raise LayaInputError('Choice labels must be unique strings')
            criteria = dict.fromkeys(criteria)
        if not isinstance(criteria, dict) or not criteria or not all(isinstance(k, str) for k in criteria):
            raise LayaInputError('Choice criteria must be a nonempty dictionary or list')
    elif kind == 'score':
        if not isinstance(criteria, list) or not criteria:
            raise LayaInputError('Score criteria must be a nonempty list')
    elif criteria is not None and (not isinstance(criteria, dict) or set(criteria) - {'false', 'true'}):
        raise LayaInputError('Noul criteria must contain only false/true descriptions')
    _json(criteria)
    instructions = question['instructions']
    if not isinstance(instructions, str):
        instructions = _json(instructions, ensure_ascii=True)
    return {'t': kind, 'ins': instructions, 'crit': criteria}


def render_options(question):
    kind, criteria = question['t'], question['crit']
    def render(value):
        return value if isinstance(value, str) else _json(value)
    if kind == 'choice':
        return [key if value is None or value == '' else f'{key}: {render(value)}'
                for key, value in criteria.items()]
    if kind == 'score':
        return [f'level {index}: {render(value)}' for index, value in enumerate(criteria)]
    criteria = {} if criteria is None else criteria
    result = []
    for label, description in [('false', 'no, the statement does not hold'), ('true', 'yes, the statement holds')]:
        value = criteria.get(label)
        result.append(f'{label}: ' + (description if value is None or value == '' else render(value)))
    return result


def validate_config(config):
    if config.get('encoder') != 'jhu-clsp/mmBERT-base' or config.get('head_layers') != 2:
        raise LayaInputError('Unexpected pinned Laya encoder or decision head')
    if config.get('max_len') != 1024 or config.get('head_max_len') != 256:
        raise LayaInputError('Pinned Laya capacity must be max_len=1024, head_max_len=256')
    temperatures, buckets = config.get('temperature'), config.get('temperature_by_options')
    if not isinstance(temperatures, list) or len(temperatures) != 3 or not isinstance(buckets, dict):
        raise LayaInputError('Missing Laya calibration temperatures')
    if any(isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) or t <= 0
           for t in [*temperatures, *buckets.values()]):
        raise LayaInputError('Calibration temperatures must be finite and positive')


def prepare(tokenizer, special, config, state, questions):
    """Build untruncated upstream token sequences, validating the inference boundary."""
    if not isinstance(state, str) or not isinstance(questions, dict) or not questions:
        raise LayaInputError('Laya requires a text state and a nonempty question dictionary')
    if not all(isinstance(key, str) and key for key in questions):
        raise LayaInputError('Question IDs must be nonempty strings')
    mask = special['mask_token']
    def encode(text):
        return tokenizer.encode(text.replace(mask, ' '), add_special_tokens=False).ids
    state_ids = encode(state)
    items, internal = [], []
    for qid, definition in questions.items():
        q = to_internal(definition)
        head = encode(f"{q['t']} question: {q['ins']}")
        options = []
        for option in render_options(q):
            encoded = encode(' ' + option)
            if len(encoded) > 48:
                raise LayaContextOverflow(qid, 'option', len(encoded), 48)
            options.append([special['mask'], *encoded])
        prefix_length = len(head) + sum(map(len, options))
        if prefix_length > config['head_max_len']:
            raise LayaContextOverflow(qid, 'prefix', prefix_length, config['head_max_len'])
        ids, markers = [special['cls'], *head, special['sep']], []
        for option in options:
            markers.append(len(ids))
            ids.extend(option)
        ids.append(special['sep'])
        room = config['max_len'] - len(ids) - 1
        if len(state_ids) > room:
            raise LayaContextOverflow(qid, 'state', len(state_ids), room)
        ids.extend([*state_ids, special['sep']])
        items.append({'ids': ids, 'markers': markers, 'qtype': QTYPES[q['t']]})
        internal.append(q)
    return items, internal


def collate(items, pad_id):
    rows, length = len(items), max(len(item['ids']) for item in items)
    count = max(2, max(len(item['markers']) for item in items))
    batch = {
        'input_ids': np.full((rows, length), pad_id, np.int64),
        'attention_mask': np.zeros((rows, length), np.int64),
        'marker_pos': np.zeros((rows, count), np.int64),
        'marker_mask': np.zeros((rows, count), np.bool_),
        'qtype': np.array([item['qtype'] for item in items], np.int64),
    }
    for row, item in enumerate(items):
        size, markers = len(item['ids']), len(item['markers'])
        batch['input_ids'][row, :size] = item['ids']
        batch['attention_mask'][row, :size] = 1
        batch['marker_pos'][row, :markers] = item['markers']
        batch['marker_mask'][row, :markers] = True
    return batch


def format_answers(config, question_ids, internal, items, logits, act_logits):
    rows = len(items)
    width = max(2, max(len(item['markers']) for item in items))
    if (len(question_ids) != rows or len(internal) != rows or logits.shape != (rows, width)
            or act_logits.ndim != 2 or act_logits.shape[0] != rows or act_logits.shape[1] < 1):
        raise LayaOutputError('Laya ONNX output shapes do not match the question batch')
    if not np.isfinite(logits).all() or not np.isfinite(act_logits).all():
        raise LayaOutputError('Laya ONNX returned non-finite outputs')
    def softmax(values):
        exp = np.exp(values - values.max())
        return exp / exp.sum()
    answers = {}
    for row, (qid, q, item) in enumerate(zip(question_ids, internal, items)):
        k, qt = len(item['markers']), item['qtype']
        bucket = '2' if k <= 2 else '3-5' if k <= 5 else '6-10' if k <= 10 else '11+'
        scale = config['temperature_by_options'].get(f"{q['t']}:{bucket}", config['temperature'][qt])
        p = softmax(logits[row, :k] / max(1e-3, float(scale)))
        confidence = 1. if k < 2 else float(np.clip(1. + (p * np.log(np.clip(p, 1e-12, 1.))).sum() / math.log(k), 0., 1.))
        answer = {'type': q['t'], 'confidence': round(confidence, 4),
                  'action': {'act_probability': round(float(softmax(act_logits[row])[0]), 4)}}
        if q['t'] == 'choice':
            labels = list(q['crit'])
            answer.update(choice=labels[int(p.argmax())],
                          probabilities={label: round(float(v), 4) for label, v in zip(labels, p)})
        elif q['t'] == 'score':
            answer.update(score=round(float((np.arange(k) * p).sum()), 4),
                          legend={str(i): value for i, value in enumerate(q['crit'])},
                          probabilities={str(i): round(float(v), 4) for i, v in enumerate(p)})
        else:
            answer.update(noul=round(float(p[1]), 4), confidence=round(max(float(p[1]), 1. - float(p[1])), 4))
        answers[qid] = answer
    return answers


class LayaRuntime:
    def __init__(self, model_dir):
        self.model_dir = Path(model_dir)
        self._lock = threading.RLock()
        self._session = None
        self._runtime_id = uuid.uuid4().hex

    def _load(self):
        if self._session is not None:
            return
        from tokenizers import Tokenizer
        import onnxruntime as ort

        ort.disable_telemetry_events()
        verify_model(self.model_dir, SPEC)
        config = json.loads((self.model_dir / 'rl_agent_config.json').read_text(encoding='utf8'))
        validate_config(config)
        tokenizer = Tokenizer.from_file(str(self.model_dir / 'tokenizer/tokenizer.json'))
        tokenizer.no_truncation()
        tokenizer.no_padding()
        tokenizer_config = json.loads((self.model_dir / 'tokenizer/tokenizer_config.json').read_text(encoding='utf8'))
        special = {}
        for key in ('cls', 'sep', 'pad', 'mask'):
            token = tokenizer_config[f'{key}_token']
            if isinstance(token, dict):
                token = token['content']
            token_id = tokenizer.token_to_id(token)
            if token_id is None:
                raise LayaInputError(f'Tokenizer is missing {key}_token')
            special[key] = token_id
            if key == 'mask':
                special['mask_token'] = token
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        session = ort.InferenceSession(str(self.model_dir / 'model.onnx'), sess_options=options,
                                       providers=['CPUExecutionProvider'])
        session.disable_fallback()
        if session.get_providers() != ['CPUExecutionProvider']:
            raise LayaInputError('Laya requires the CPUExecutionProvider')
        if {item.name: item.type for item in session.get_inputs()} != INPUT_TYPES:
            raise LayaInputError('Laya ONNX input contract does not match the pinned model')
        if {item.name: item.type for item in session.get_outputs()} != {'logits': 'tensor(float)', 'act_logits': 'tensor(float)'}:
            raise LayaInputError('Laya ONNX output contract does not match the pinned model')
        self._config, self._tokenizer, self._special, self._session = config, tokenizer, special, session

    def predict(self, state: str, questions: dict) -> dict:
        global _runtime_status
        with self._lock:
            self._load()
            items, internal = prepare(self._tokenizer, self._special, self._config, state, questions)
            answers, qids = {}, list(questions)
            for start in range(0, len(items), 8):
                chunk = items[start:start + 8]
                logits, act_logits = self._session.run(['logits', 'act_logits'], collate(chunk, self._special['pad']))
                answers.update(format_answers(self._config, qids[start:start + 8], internal[start:start + 8], chunk,
                                              logits, act_logits))
            with _runtime_status_lock:
                _runtime_status = {'actual_device': 'cpu', 'device_name': 'CPU', 'active': True,
                                   'model': SPEC['id'], 'model_revision': SPEC['revision'],
                                   'runtime_id': self._runtime_id, 'updated': time.time(),
                                   'using_fallback': False, 'reason': ''}
            return answers

    def close(self):
        with self._lock:
            self._session = None
            with _runtime_status_lock:
                if _runtime_status.get('runtime_id') == self._runtime_id:
                    _runtime_status['active'] = False
