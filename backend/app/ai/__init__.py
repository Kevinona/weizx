"""AI 引擎模块 - LLM、RAG、记忆、安全、Embedding 一体化管理。

注意：本 __init__ 故意不 eager import 任何重模块。
`langchain_openai` / `langgraph` 等外部依赖如果没装，应该只让用到的子模块失败，
而不是让 `import app.ai.counter` 这样的轻量调用也跟着崩。

调用方应该显式 import 所需模块：
    from app.ai.counter import get_count
    from app.ai.guard import sanitize_user_input
    from app.ai.knowledge_seed import seed_knowledge_base

或者在确实需要时延迟加载：
    from app import ai
    agent_cls = ai.WeixAgent  # 走 __getattr__ 触发实际 import
"""

from __future__ import annotations

# 这些是 stdlib-only 的轻量模块，保留 eager re-export 不会引发重依赖
from app.ai.counter import (
    get_count,
    increment,
    reset,
)
from app.ai.guard import (
    sanitize_user_input,
    get_hardened_system_prompt,
    build_self_awareness_prompt,
    check_output_safety,
    ASSISTANT_GUARD_PROMPT_APPENDIX,
    SELF_GUARD_PROMPT_APPENDIX,
)

__all__ = [
    # counter
    "get_count",
    "increment",
    "reset",
    # guard
    "sanitize_user_input",
    "get_hardened_system_prompt",
    "build_self_awareness_prompt",
    "check_output_safety",
    "ASSISTANT_GUARD_PROMPT_APPENDIX",
    "SELF_GUARD_PROMPT_APPENDIX",
]

# 延迟重导出：仅在被显式访问时才 import 重模块（避免 langchain 缺失时
# 任何 `import app.ai.x` 都崩）。`from app import ai; ai.WeixAgent` 会触发。
_LAZY_EXPORTS = {
    "WeixAgent": "app.ai.agent",
    "SummaryAgent": "app.ai.agent",
    "LLMConfig": "app.ai.models",
    "create_llm": "app.ai.models",
    "ConversationMemory": "app.ai.memory",
    "EmbeddingManager": "app.ai.embeddings",
    "VectorStoreManager": "app.ai.vector_store",
    "RAGPipeline": "app.ai.rag",
    "SYSTEM_PROMPT": "app.ai.prompts",
    "GROUP_CHAT_PROMPT": "app.ai.prompts",
    "PRIVATE_CHAT_PROMPT": "app.ai.prompts",
    "SUMMARY_PROMPT": "app.ai.prompts",
    "STATISTICS_PROMPT": "app.ai.prompts",
    "search_web": "app.ai.tools",
    "get_weather": "app.ai.tools",
    "get_current_time": "app.ai.tools",
    "calculate": "app.ai.tools",
    "query_statistics": "app.ai.tools",
    "create_tools": "app.ai.tools",
}


def __getattr__(name):
    """PEP 562 延迟加载：只在 `ai.WeixAgent` 这种访问时实际 import 重模块。"""
    if name in _LAZY_EXPORTS:
        import importlib

        module = importlib.import_module(_LAZY_EXPORTS[name])
        value = getattr(module, name)
        globals()[name] = value  # 缓存到 globals，下次直接拿
        return value
    raise AttributeError(f"module 'app.ai' has no attribute {name!r}")
