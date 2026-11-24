"""聊天模型的全局选择；空记录保留删除状态，避免静默切换服务。"""
from pydantic import BaseModel, Field
from typing import Literal


class SelectedModel(BaseModel):
    profile_id: str = Field(min_length=1, max_length=200)
    model_id: str = Field(min_length=1, max_length=200)
    reasoning_effort: str | None = Field(None, max_length=40)
    thinking_mode: Literal['enabled', 'disabled'] | None = None
    thinking_budget: int | None = Field(None, ge=0, le=10000000, strict=True)


def selected_model(store):
    with store.lock:
        saved = store.get('selected_model', 'global')
        if saved is not None:
            if saved.get('profile_id') and not store.get('profile', saved['profile_id']):
                saved = store.put('selected_model', {'unavailable': True}, id='global')
            return {key: value for key, value in saved.items() if key != 'id'}
        profiles = [profile for profile in store.list('profile') if profile.get('model')]
        if not profiles:
            return {}
        defaults = store.get('defaults', 'global') or {}
        profile = next((p for p in profiles if p['id'] == defaults.get('text')), profiles[0])
        choice = dict(profile_id=profile['id'], model_id=profile['model'], reasoning_effort=None)
        store.put('selected_model', choice, id='global')
        return choice


def follow_profile_edit(store, old, profile):
    """编辑配置后：选中原默认模型时跟随新默认模型；选中其他模型且服务地址或协议已变时要求重新选择。"""
    with store.lock:
        saved = store.get('selected_model', 'global')
        if not saved or saved.get('profile_id') != profile['id']:
            return
        if saved['model_id'] == old['model']:
            if profile['model'] != old['model']:
                # 推理档位属于旧模型，不能沿用到新模型。
                store.put('selected_model', dict(profile_id=profile['id'], model_id=profile['model'], reasoning_effort=None), id='global')
        elif (profile['base_url'].rstrip('/'), profile['protocol']) != (old['base_url'].rstrip('/'), old['protocol']):
            store.put('selected_model', {'unavailable': True, 'reason': 'profile_changed'}, id='global')
