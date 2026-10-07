"""AI 工具/记忆/RAG/向量库/embedding 测试。

本文件覆盖 backend/app/ai/ 下的 5 个目标文件：
- app/ai/tools.py (4 个原有 + 4 个新增)
- app/ai/memory.py
- app/ai/rag.py
- app/ai/vector_store.py
- app/ai/embeddings.py

约定：
- 所有外部依赖（AI API、embedding API、chromadb、sentence-transformers）都用 mock。
- 不引入 Windows 路径或 Windows-only 模块。
- chromadb 在导入前被替换为 MagicMock，避免环境依赖。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

# 在导入任何 AI 模块之前，把 chromadb 替换成 MagicMock
# 避免测试环境需要真实的 chromadb 安装。
sys.modules.setdefault("chromadb", MagicMock())
sys.modules.setdefault("chromadb.config", MagicMock())

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402  — pytest 用于 fixture / monkeypatch

from app.ai import tools as ai_tools  # noqa: E402
from app.ai import memory as ai_memory  # noqa: E402
from app.ai import embeddings as ai_embeddings  # noqa: E402
from app.ai import vector_store as ai_vector_store  # noqa: E402
from app.ai import rag as ai_rag  # noqa: E402


# =============================================================================
# tools.py
# =============================================================================


def test_search_web_missing_dependency_returns_tool_result(monkeypatch):
    """搜索依赖缺失时必须返回 ToolMessage 内容，不能让 Agent 历史损坏。"""

    class BrokenSearch:
        def __init__(self):
            raise ImportError("missing ddgs")

    monkeypatch.setattr(ai_tools, "DuckDuckGoSearchRun", BrokenSearch)

    result = ai_tools.search_web.invoke("今天天气")

    assert "搜索工具暂时不可用" in result
    assert "ddgs" in result


def test_get_weather_requires_city_before_calling_amap():
    result = ai_tools.get_weather.invoke("")

    assert "哪个城市" in result


def test_create_tools_includes_weather_tool_by_default():
    tool_names = [tool.name for tool in ai_tools.create_tools()]

    assert "get_weather" in tool_names


def test_get_weather_uses_amap_and_formats_live_weather(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "1",
                "info": "OK",
                "infocode": "10000",
                "lives": [
                    {
                        "province": "贵州",
                        "city": "贵阳市",
                        "weather": "多云",
                        "temperature": "22",
                        "winddirection": "南",
                        "windpower": "≤3",
                        "humidity": "68",
                        "reporttime": "2026-05-15 15:00:00",
                    }
                ],
            }

    def fake_get(url, params, timeout):
        captured["url"] = url
        captured["params"] = params
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(
        ai_tools,
        "get_config",
        lambda: SimpleNamespace(
            ai={
                "amap_key": "test-key",
                "amap_security_key": "secret",
            }
        ),
    )
    monkeypatch.setattr(ai_tools.httpx, "get", fake_get)

    result = ai_tools.get_weather.invoke("贵阳")

    expected_sig_base = (
        "city=贵阳&extensions=base&key=test-key&output=JSONsecret"
    )
    assert captured["url"].endswith("/v3/weather/weatherInfo")
    assert captured["params"]["key"] == "test-key"
    assert captured["params"]["city"] == "贵阳"
    assert captured["params"]["sig"] == hashlib.md5(
        expected_sig_base.encode("utf-8")
    ).hexdigest()
    assert "贵阳市天气：多云，22°C" in result
    assert "湿度 68%" in result


def test_safe_eval_basic_arithmetic_and_functions():
    """白名单内的算术与函数应正常求值。"""
    assert ai_tools._safe_eval("2 + 3 * 4") == 14
    assert ai_tools._safe_eval("(1 + 2) * 3") == 9
    assert ai_tools._safe_eval("-5 + 10") == 5
    assert ai_tools._safe_eval("2 ** 10") == 1024
    # 浮点容差
    assert abs(ai_tools._safe_eval("sqrt(16)") - 4.0) < 1e-9
    assert ai_tools._safe_eval("max(1, 2, 3)") == 3


def test_safe_eval_rejects_unsafe_expression():
    """任何 AST 白名单外的语法必须抛 ValueError，防止代码注入。"""
    for bad in (
        "__import__('os').system('ls')",  # 函数调用不在白名单
        "open('x.txt').read()",            # 属性访问
        "[1, 2, 3]",                       # 列表字面量
        "'hello'",                         # 字符串字面量
        "",                                # 空表达式
    ):
        with pytest.raises(ValueError):
            ai_tools._safe_eval(bad)


def test_safe_eval_division_by_zero():
    with pytest.raises(ValueError, match="除数不能为零"):
        ai_tools._safe_eval("1 / 0")


def test_get_current_time_chinese_weekday_no_leak(monkeypatch):
    """星期数字 → 中文映射必须覆盖 strftime('%w') 的 0-6 范围（含周日=0）。

    回归 bug：原代码使用 now.weekday()+1 作为 key，导致周日 7 落不到 map 里，
    返回 "星期7" 而不是 "星期日"。
    """
    # 模拟一个固定的 datetime：2024-01-07 是星期日。
    fixed = datetime(2024, 1, 7, 10, 30, 0)

    class FakeDatetime:
        @classmethod
        def now(cls):
            return fixed

    monkeypatch.setattr(ai_tools, "datetime", FakeDatetime)

    result = ai_tools.get_current_time.invoke()

    assert "星期日" in result
    assert "星期7" not in result
    assert "星期0" not in result
    # 同时覆盖周一
    fixed_mon = datetime(2024, 1, 8, 10, 30, 0)
    monkeypatch.setattr(ai_tools, "datetime", type("D", (), {"now": staticmethod(lambda: fixed_mon)}))
    result_mon = ai_tools.get_current_time.invoke()
    assert "星期一" in result_mon
    assert "星期1" not in result_mon


def test_calculate_returns_failure_string_for_unsupported_expression():
    """@tool calculate 必须返回错误字符串（不抛），让 LLM 能继续对话。"""
    result = ai_tools.calculate.invoke("__import__('os')")
    assert "计算失败" in result


# =============================================================================
# memory.py
# =============================================================================


def test_make_session_id_private_and_group():
    assert ai_memory.ConversationMemory.make_session_id(False, "wxid_abc") == "private:wxid_abc"
    assert ai_memory.ConversationMemory.make_session_id(True, "123@chatroom") == "group:123@chatroom"


def test_record_turn_increments_counter_per_session():
    mem = ai_memory.ConversationMemory()
    assert mem.get_turn_count("private:user1") == 0
    mem.record_turn("private:user1", "hi", "hello")
    mem.record_turn("private:user1", "again", "world")
    mem.record_turn("group:g1", "ping", "pong")
    assert mem.get_turn_count("private:user1") == 2
    assert mem.get_turn_count("group:g1") == 1
    # 不存在的会话返回 0 而不是抛错
    assert mem.get_turn_count("private:nobody") == 0


def test_clear_memory_removes_session_state():
    mem = ai_memory.ConversationMemory()
    mem.record_turn("private:user1", "hi", "hello")
    mem.set_summary("private:user1", "聊过天气")
    mem.clear_memory("private:user1")
    assert mem.get_turn_count("private:user1") == 0
    assert mem.get_summary("private:user1") == ""
    # 多次清理不抛错
    mem.clear_memory("private:user1")


def test_maybe_summarize_returns_none_below_threshold():
    """未达到阈值时 must_summarize 必须直接返回 None，不触发 LLM。"""
    mem = ai_memory.ConversationMemory(k=5)
    # 5 轮远低于阈值 40
    for i in range(5):
        mem.record_turn("private:u", f"msg-{i}", f"reply-{i}")
    result = asyncio.run(mem.maybe_summarize("private:u"))
    assert result is None


def test_maybe_summarize_skips_when_no_llm_even_above_threshold():
    """达到阈值但 llm is None，必须返回 None 而不抛错。"""
    mem = ai_memory.ConversationMemory(k=5)
    # 直接把计数顶到阈值以上
    mem._message_counts["private:u"] = ai_memory.AUTO_SUMMARY_THRESHOLD + 10
    result = asyncio.run(mem.maybe_summarize("private:u"))
    assert result is None


def test_maybe_summarize_above_threshold_with_llm_but_no_summaryagent(monkeypatch):
    """有 LLM 但 SummaryAgent 不可用时，必须 graceful 返回 None 而不是崩溃。"""
    mem = ai_memory.ConversationMemory(k=5)
    mem.llm = MagicMock()  # 任意对象
    mem._message_counts["private:u"] = ai_memory.AUTO_SUMMARY_THRESHOLD + 10

    # 让 import 失败：SummaryAgent 不可导入
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "app.ai.agent":
            raise ImportError("SummaryAgent missing in test env")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    result = asyncio.run(mem.maybe_summarize("private:u"))
    assert result is None


def test_get_all_sessions_and_memory_stats():
    mem = ai_memory.ConversationMemory()
    mem.record_turn("private:a", "x", "y")
    mem.record_turn("group:b", "x", "y")
    sessions = mem.get_all_sessions()
    assert set(sessions) == {"private:a", "group:b"}
    stats = mem.memory_stats()
    assert stats == {"private:a": 1, "group:b": 1}


# =============================================================================
# rag.py
# =============================================================================


class _FakeEmbeddings:
    """最小化的 EmbeddingManager 替身，只实现 rag 用到的 async 方法。"""

    def __init__(self, vec=None):
        self._vec = vec or [0.1] * 4

    async def embed_query_async(self, text):
        return list(self._vec)


class _FakeVectorStore:
    """最小化的 VectorStoreManager 替身。"""

    def __init__(self, knowledge=None, similar=None, recent=None, dup_result=(False, "")):
        self._knowledge = knowledge or []
        self._similar = similar or []
        self._recent = recent or []
        self._dup_result = dup_result

    async def search_knowledge_async(self, query_embedding, k=3):
        return list(self._knowledge)

    async def search_similar_conversations_async(
        self, query_embedding, k=3, session_id="", exclude_session=""
    ):
        return list(self._similar)

    def get_recent_responses(self, session_id, k=5):
        return list(self._recent)

    async def check_duplicate_async(self, response_embedding, threshold=0.85):
        return self._dup_result

    async def remember_response_async(self, session_id, response, embedding):
        # 在测试里记录副作用
        self.remembered = (session_id, response, embedding)


def _run(coro):
    return asyncio.run(coro)


def test_build_context_returns_dict_structure_with_empty_data():
    """空知识库 / 无历史时，build_context 必须返回带中文 fallback 文本的结构化 dict。"""
    rag = ai_rag.RAGPipeline(_FakeEmbeddings(), _FakeVectorStore())
    result = _run(rag.build_context("今天天气", "private:u1"))
    assert set(result.keys()) == {"knowledge_docs", "similar_conversations", "duplicate_warning"}
    assert "暂无相关知识库内容" in result["knowledge_docs"]
    assert "暂无相关历史对话" in result["similar_conversations"]
    assert "第一次" in result["duplicate_warning"]


def test_build_context_includes_knowledge_and_similar_when_available():
    """有数据时，必须格式化返回（带 topic 前缀、列表序号）。"""
    knowledge = [
        {"text": "退货政策：7 天无理由", "distance": 0.1, "metadata": {"topic": "退换货"}},
        {"text": "联系客服 400-xxx", "distance": 0.3, "metadata": {}},
    ]
    similar = ["上周聊过同样的退货问题"]
    recent = ["已经回复过一次：请联系客服"]
    rag = ai_rag.RAGPipeline(
        _FakeEmbeddings(),
        _FakeVectorStore(knowledge=knowledge, similar=similar, recent=recent),
    )
    result = _run(rag.build_context("怎么退货", "private:u1"))
    assert "[退换货] 退货政策：7 天无理由" in result["knowledge_docs"]
    assert "1. 退货政策：7 天无理由" in result["knowledge_docs"]
    assert "联系客服 400-xxx" in result["knowledge_docs"]
    assert "上周聊过同样的退货问题" in result["similar_conversations"]
    # 自省提醒应包含最近回复
    assert "已经回复过一次：请联系客服" in result["duplicate_warning"]
    assert "## AI 自省提醒" in result["duplicate_warning"]


def test_check_duplicate_and_remember_swallows_exception():
    """任何子步骤抛错时，必须返回 False 并记录 warning，不向调用方抛。"""

    class ExplodingStore(_FakeVectorStore):
        async def check_duplicate_async(self, response_embedding, threshold=0.85):
            raise RuntimeError("chroma down")

    rag = ai_rag.RAGPipeline(_FakeEmbeddings(), ExplodingStore())
    result = _run(rag.check_duplicate_and_remember("private:u1", "hi"))
    assert result is False


def test_check_duplicate_and_remember_skips_memory_when_duplicate():
    """如果检测为重复，不应再写 remember_response_async。"""
    store = _FakeVectorStore(dup_result=(True, "之前的回复"))
    rag = ai_rag.RAGPipeline(_FakeEmbeddings(), store)
    result = _run(rag.check_duplicate_and_remember("private:u1", "新回复"))
    assert result is True
    assert not hasattr(store, "remembered")


def test_remember_conversation_swallows_exception():
    """写入向量库失败时必须 swallow。"""

    class ExplodingStore(_FakeVectorStore):
        async def add_conversation_summary_async(self, session_id, summary, embedding):
            raise RuntimeError("disk full")

    rag = ai_rag.RAGPipeline(_FakeEmbeddings(), ExplodingStore())
    # 必须不抛
    _run(rag.remember_conversation("private:u1", "一段摘要"))


# =============================================================================
# vector_store.py
# =============================================================================


def test_hash_id_with_prefix_and_without():
    h_with = ai_vector_store._hash_id("hello world", "kb")
    h_no = ai_vector_store._hash_id("hello world")
    assert h_with.startswith("kb_")
    assert "_" in h_with
    # 没有 prefix 时返回纯哈希
    assert "_" not in h_no


def test_hash_id_is_deterministic_and_short():
    a = ai_vector_store._hash_id("同一段文本", "self")
    b = ai_vector_store._hash_id("同一段文本", "self")
    assert a == b
    # 哈希部分应当比较短（sha256 前 12 位 + 前缀）
    assert len(a.split("_", 1)[1]) == 12
    # 不同输入产出不同 ID
    assert ai_vector_store._hash_id("A", "kb") != ai_vector_store._hash_id("B", "kb")


def test_search_knowledge_handles_missing_distances_field():
    """chromadb 偶发回边 fields 为 None时不能 IndexError。"""
    rag_store = ai_vector_store.VectorStoreManager.__new__(ai_vector_store.VectorStoreManager)
    # 直接构造一个 fake knowledge_base 集合，避免 chromadb 真的启动
    rag_store.knowledge_base = MagicMock()
    # distances / metadatas / documents 三个字段都缺失
    rag_store.knowledge_base.query.return_value = {
        "ids": [["doc-1", "doc-2"]],
        # distances 缺失
        "documents": [["文本1", "文本2"]],
        "metadatas": [[{"src": "a"}, {"src": "b"}]],
    }
    # 关键：threshold=0.6 时 distance < 0.4 才被纳入；但 distances 缺失应当返回空而不是抛错
    docs = rag_store.search_knowledge([0.1] * 4, k=3, threshold=0.6)
    assert docs == []  # 没有有效距离 → 空结果，不抛错


def test_search_knowledge_includes_close_match():
    """distance < 1.0 - threshold 的文档应被纳入。"""
    rag_store = ai_vector_store.VectorStoreManager.__new__(ai_vector_store.VectorStoreManager)
    rag_store.knowledge_base = MagicMock()
    rag_store.knowledge_base.query.return_value = {
        "ids": [["d1", "d2"]],
        "documents": [["近", "远"]],
        "metadatas": [[{"src": "a"}, {"src": "b"}]],
        "distances": [[0.1, 0.9]],  # 阈值 0.6 → 仅 0.1 命中
    }
    docs = rag_store.search_knowledge([0.1] * 4, k=3, threshold=0.6)
    assert len(docs) == 1
    assert docs[0]["id"] == "d1"
    assert docs[0]["text"] == "近"
    assert docs[0]["distance"] == 0.1


def test_check_duplicate_handles_missing_distances():
    """distances 缺失时不能 IndexError，应返回 (False, '')。"""
    rag_store = ai_vector_store.VectorStoreManager.__new__(ai_vector_store.VectorStoreManager)
    rag_store.ai_self_memory = MagicMock()
    rag_store.ai_self_memory.query.return_value = {
        "ids": [["some-id"]],
        "documents": [["已有回复"]],
        # distances 缺失
    }
    is_dup, text = rag_store.check_duplicate([0.1] * 4)
    assert is_dup is False
    assert text == ""


def test_get_recent_responses_handles_missing_metadatas_and_documents():
    """metadatas/documents 缺失时不能 IndexError。"""
    rag_store = ai_vector_store.VectorStoreManager.__new__(ai_vector_store.VectorStoreManager)
    rag_store.ai_self_memory = MagicMock()
    rag_store.ai_self_memory.get.return_value = {
        "ids": ["a", "b"],
        # metadatas / documents 缺失
    }
    result = rag_store.get_recent_responses("private:u1", k=5)
    assert result == []


# =============================================================================
# embeddings.py
# =============================================================================


def test_embed_query_empty_text_returns_zeros_without_loading_client():
    """空文本不能加载 embedding client，直接返回零向量。

    回归 bug：原代码在 if not text: 分支里也调用了 _ensure_client()，
    会触发 sentence-transformers 真实下载/加载。
    """
    em = ai_embeddings.EmbeddingManager(provider="local")
    # 关键断言：调用前 _client 应为 None
    assert em._client is None

    result = em.embed_query("")

    assert em._client is None  # 关键：没有触发 _ensure_client
    assert result == [0.0] * em._dimension
    assert len(result) == em._dimension


def test_embed_query_none_text_treated_as_empty():
    em = ai_embeddings.EmbeddingManager(provider="local")
    result = em.embed_query(None)  # type: ignore[arg-type]
    assert result == [0.0] * em._dimension
    assert em._client is None


def test_embed_empty_list_returns_empty_list_without_loading():
    em = ai_embeddings.EmbeddingManager(provider="local")
    assert em.embed([]) == []
    assert em._client is None


def test_can_load_local_embedding_returns_bool():
    """helper 必须返回 bool（即便缓存不存在也不抛错）。"""
    result = ai_embeddings.can_load_local_embedding("nonexistent-model-xyz")
    assert isinstance(result, bool)
    assert result is False


def test_get_local_embedding_cache_status_returns_known_strings():
    actual = ai_embeddings.get_local_embedding_cache_status("nonexistent-model-xyz")
    assert actual in ("已缓存", "未缓存，将在后台自动下载")


def test_embed_query_async_returns_value_from_sync(monkeypatch):
    """embed_query_async 必须把同步 embed_query 丢进 executor，返回其结果。"""
    em = ai_embeddings.EmbeddingManager(provider="local")
    # stub 掉 _ensure_client，让 embed_query 能直接返回
    em._ensure_client = lambda: None
    em._client = MagicMock()
    em._client.encode.return_value.tolist.return_value = [[0.2, 0.4]]
    result = asyncio.run(em.embed_query_async("你好"))
    assert result == [0.2, 0.4]