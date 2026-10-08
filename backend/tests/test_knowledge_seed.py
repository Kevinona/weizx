"""weizx — knowledge_seed.py 测试。

覆盖点：
- seed_knowledge_base 当 KB 非空时跳过
- seed_knowledge_base 当 KB 为空时写入全部种子
- SEED_DOCUMENTS 至少有 1 条且都有 text/topic/priority 字段
- 写入时 ids / metadatas 数量与 SEED_DOCUMENTS 一致
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ai.knowledge_seed import seed_knowledge_base, SEED_DOCUMENTS  # noqa: E402


def _build_mocks(existing_count: int = 0):
    """构造 chromadb collection 风格的 mock。"""
    kb = MagicMock()
    kb.count.return_value = existing_count
    kb.add = MagicMock()

    vector_store = SimpleNamespace(knowledge_base=kb)

    embedding_manager = MagicMock()
    embedding_manager.embed.return_value = [
        [0.0] * 4, [0.1] * 4, [0.2] * 4, [0.3] * 4,
        [0.4] * 4, [0.5] * 4, [0.6] * 4, [0.7] * 4,
    ]
    return vector_store, embedding_manager


# ---------------------------------------------------------------------------
# SEED_DOCUMENTS: shape 校验
# ---------------------------------------------------------------------------


def test_seed_documents_is_non_empty():
    assert len(SEED_DOCUMENTS) >= 1


def test_seed_documents_have_required_fields():
    for d in SEED_DOCUMENTS:
        assert "text" in d and isinstance(d["text"], str) and d["text"]
        assert "topic" in d and isinstance(d["topic"], str)
        assert "priority" in d and d["priority"] in {"high", "medium", "low"}


def test_seed_documents_have_unique_topics():
    """至少覆盖 4 个不同 topic：价格 / 流程 / 服务 / 售后 / 群规。"""
    topics = {d["topic"] for d in SEED_DOCUMENTS}
    assert len(topics) >= 4


# ---------------------------------------------------------------------------
# seed_knowledge_base 行为
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seed_skips_when_kb_not_empty():
    vector_store, embedding_manager = _build_mocks(existing_count=10)
    n = await seed_knowledge_base(vector_store, embedding_manager)
    assert n == 0
    vector_store.knowledge_base.add.assert_not_called()


@pytest.mark.asyncio
async def test_seed_writes_all_when_kb_empty():
    vector_store, embedding_manager = _build_mocks(existing_count=0)
    n = await seed_knowledge_base(vector_store, embedding_manager)
    assert n == len(SEED_DOCUMENTS)
    vector_store.knowledge_base.add.assert_called_once()


@pytest.mark.asyncio
async def test_seed_calls_add_with_correct_shape():
    vector_store, embedding_manager = _build_mocks(existing_count=0)
    await seed_knowledge_base(vector_store, embedding_manager)

    call = vector_store.knowledge_base.add.call_args
    assert "ids" in call.kwargs
    assert "documents" in call.kwargs
    assert "embeddings" in call.kwargs
    assert "metadatas" in call.kwargs

    # ids 形如 seed_0..seed_N
    ids = call.kwargs["ids"]
    assert ids == [f"seed_{i}" for i in range(len(SEED_DOCUMENTS))]

    # metadatas 每个都有 source/topic/priority/added_at
    for meta in call.kwargs["metadatas"]:
        assert meta["source"] == "seed"
        assert "topic" in meta
        assert "priority" in meta
        assert "added_at" in meta


@pytest.mark.asyncio
async def test_seed_handles_count_exception():
    """如果 count() 抛异常，必须继续写入（不阻塞 seed）。"""
    kb = MagicMock()
    kb.count.side_effect = RuntimeError("count failed")
    kb.add = MagicMock()
    vector_store = SimpleNamespace(knowledge_base=kb)

    embedding_manager = MagicMock()
    embedding_manager.embed.return_value = [[0.0] * 4] * len(SEED_DOCUMENTS)

    n = await seed_knowledge_base(vector_store, embedding_manager)
    assert n == len(SEED_DOCUMENTS)
    kb.add.assert_called_once()


@pytest.mark.asyncio
async def test_seed_propagates_embed_exception():
    """如果 embed 失败，异常透传（不静默吞掉）。"""
    vector_store, _ = _build_mocks(existing_count=0)
    embedding_manager = MagicMock()
    embedding_manager.embed.side_effect = RuntimeError("embed failed")

    with pytest.raises(RuntimeError, match="embed failed"):
        await seed_knowledge_base(vector_store, embedding_manager)
