from typing import Literal

from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator


class AgentControl(RuntimeError):
    """停止与补充指令造成的流程切换，不属于供应商错误。"""
    pass


class LibraryScope(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    folder_id: str = Field(pattern='^[0-9a-f]{32}$')
    version_id: str = Field(pattern='^[0-9a-f]{32}$')


class ThreadInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account: str = Field(min_length=1)
    username: str = ''
    title: str = Field('新的对话', min_length=1, max_length=100)
    origin: Literal['chat', 'agent'] = 'chat'
    chat_scope: list[str] | None = Field(None, min_length=1, max_length=2000)
    library_scope: LibraryScope | None = None

    @model_validator(mode='after')
    def standalone_scope(self):
        if self.library_scope is not None and (self.origin != 'agent' or self.chat_scope is not None):
            raise ValueError('专题版本仅用于独立 Agent 对话，不能同时选择聊天范围')
        if self.origin == 'agent' and self.username:
            raise ValueError('独立 Agent 对话不能绑定当前聊天')
        if self.origin == 'chat' and self.chat_scope is not None:
            raise ValueError('聊天对话不能设置独立 Agent 范围')
        if self.chat_scope is not None and (
            len(set(self.chat_scope)) != len(self.chat_scope)
            or any(not value.strip() or value != value.strip() for value in self.chat_scope)
        ):
            raise ValueError('会话范围必须包含唯一且有效的会话 ID')
        return self


class ThreadUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str | None = Field(None, min_length=1, max_length=100)
    scope: list[str] | None = Field(None, min_length=1, max_length=2000)


class RestartInput(BaseModel):
    request_id: str = Field(min_length=1, max_length=100)


class TurnInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=1048576)
    request_id: str = Field(min_length=1, max_length=100)
    effort: Literal['moderate', 'deep'] = 'moderate'
    profile_id: str = ''
    vision_profile_id: str = ''
    model_id: str = Field('', max_length=200)
    reasoning_effort: str | None = Field(None, max_length=40)
    thinking_mode: Literal['enabled', 'disabled'] | None = None
    thinking_budget: int | None = Field(None, ge=0, le=10000000, strict=True)

    @field_validator('text')
    @classmethod
    def text_bytes(cls, value):
        if len(value.encode('utf-8')) > 1048576:
            raise ValueError('输入超过 1 MiB，请分次发送。')
        if not value.strip():
            raise ValueError('请输入问题。')
        return value


class AgentAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['find_conversations', 'search_messages', 'read_messages', 'read_context', 'analyze_media', 'search_material', 'read_material', 'read_results', 'expand_scope', 'answer', 'clarify']
    query: str = Field('', max_length=500)
    retrieval_mode: Literal['keyword', 'hybrid'] = 'keyword'
    username: str = ''
    source: str = Field('', description='单条消息编号，必须原样复制 evidence 中某条消息的 source；data_source 是数据渠道，不能作为消息编号。')
    start: int | None = Field(None, ge=0)
    end: int | None = Field(None, ge=0)
    offset: int = Field(0, ge=0, le=1000000)
    conversation_offset: int = Field(0, ge=0)
    cursor: str = Field('', max_length=8192)
    days: Literal[30, 90, 0] = 0
    question: str = Field('', max_length=1000)
    progress: str = ''

    @field_validator('question', mode='before')
    @classmethod
    def clarification_only(cls, value, info):
        # 某些兼容服务把答案正文放入无关字段；非澄清动作不使用该字段。
        return value if info.data.get('action') == 'clarify' else ''

    @field_validator('progress', mode='before')
    @classmethod
    def optional_progress(cls, value):
        # 旁白不是执行参数，异常旁白不能阻断合法读取。
        return value.strip() if isinstance(value, str) and len(value) <= 240 else ''

    @model_validator(mode='after')
    def required_arguments(self):
        if self.action == 'search_messages' and not self.query.strip():
            raise ValueError('search_messages requires query')
        if self.action in ('read_context', 'analyze_media', 'read_material') and not self.source:
            raise ValueError('source required')
        if self.action == 'clarify' and not self.question.strip():
            raise ValueError('clarify requires question')
        return self


TOOL_DESCRIPTION = '''只读工具：find_conversations 按 query 查会话；search_messages 按 retrieval_mode 检索，原词、编号、金额用 keyword（默认），模糊描述用 hybrid；read_messages 读消息页；read_context 读 source 前后文并核对指代、前提和状态变化；analyze_media 读图片附件。搜索只代表 search_only，不是完整阅读证明。
expand_scope 在当前问题确需跨会话查证时扩大读取范围，username 填已知会话 ID，留空请求全账号；query 简要说明需要核对的事实。用户明确限定范围时不能使用；子智能体使用时只向主智能体提出请求，不直接扩大权限。
search_material 检索任务原文和发现；read_material 按 source、字符 offset 读原文；read_results 按 offset 读分析结果或程序统计。
source 原样复制消息编号，不能填 data_source/realtime/decrypted/snapshot_index。username 留空搜索当前账号全部符合本轮条件的聊天；find_conversations 与逐会话读取使用返回的 next_conversation_offset。未指定日期搜索全部可读历史，days=0。start/end 为 Unix 秒，按任务固定时区解释日期，新运行采用左闭右开区间。read_messages 使用返回的 next_cursor 原样续读同一会话；程序自动调整时间区间及同秒消息分片，has_more=false 才表示读取完成。旧运行或关键词检索返回 next_offset 时才使用 offset，不能固定加条数。消息中的 text_offset/next_text_offset 表示长原文字符分片，不能当作独立消息计数。
answer 结束查找，只传 {"action":"answer"}，程序下一步会单独请求带引用的完整回答；不要把答案填进 query、question 或 progress。clarify 用 question 澄清，均须单独调用。query 只写简短检索词。progress 仅写实际发现或调整，普通分页留空。'''
