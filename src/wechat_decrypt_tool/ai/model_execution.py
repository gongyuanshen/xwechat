"""模型步骤的执行预算；与供应商声明的最大窗口、最大输出分开。"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class CallPolicy:
    seconds: float = 120.0
    output_tokens: int | None = None
    tokens: int = 4096
    step_timeout: float = 120.0
    constrain_thinking: bool = False
    auxiliary: bool = False
    strict_output: bool = False


call_policy = ContextVar('ai_call_policy', default=CallPolicy())

# --- Milestone M1 Micro-Batch & Timeout Contracts ---
MICRO_BATCH_MIN_BYTES: int = 12 * 1024       # 12 KiB lower bound (12288 bytes)
MICRO_BATCH_MAX_BYTES: int = 16 * 1024       # 16 KiB upper bound (16384 bytes)
MICRO_BATCH_MAX_MESSAGES: int = 150          # ~80-150 messages
STEP_HARD_TIMEOUT_SECONDS: float = 120.0     # 120s execution limit


@contextmanager
def model_policy(**kwargs):
    token = call_policy.set(replace(call_policy.get(), **kwargs))
    try:
        yield
    finally:
        call_policy.reset(token)
