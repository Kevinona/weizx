"""weizx — agent.py 修复回归测试。

主要验证 Checkpoint 持久化（_save_checkpoints / _load_checkpoints /
_discard_checkpoint）兼容 langgraph MemorySaver（使用 ``storage`` 属性）
的行为，避免对真实 checkpointer 静默失败。
"""

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ai.agent import WeixAgent  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _tmp_agent(checkpointer, tmp_path: Path) -> WeixAgent:
    """构造一个绕过 __init__ 的 WeixAgent，避免触发 LLM/RAG/工具链。"""
    agent = WeixAgent.__new__(WeixAgent)
    agent._checkpointer = checkpointer
    # 把 checkpoint 文件指向临时目录，避免污染 data/
    agent._tmp_data_dir = tmp_path  # noqa: SLF001 仅为测试引用
    return agent


def _patch_data_dir(monkeypatch, tmp_path: Path) -> None:
    """将 get_data_dir 替换为临时目录，验证文件落盘。"""
    monkeypatch.setattr(
        "app.ai.agent.get_data_dir", lambda: tmp_path, raising=False
    )
    # 同步设置已经被 import 进去的旧引用
    import app.utils.paths as paths_module  # noqa: WPS433
    monkeypatch.setattr(paths_module, "get_data_dir", lambda: tmp_path)


# ---------------------------------------------------------------------------
# 1. _discard_checkpoint 兼容旧版 _checkpoints（既有契约）
# ---------------------------------------------------------------------------


def test_discard_checkpoint_removes_failed_thread_with_legacy_dict(monkeypatch, tmp_path):
    """使用 ``_checkpoints`` 字典时，必须按既有行为删除条目并触发持久化。"""
    _patch_data_dir(monkeypatch, tmp_path)
    agent = _tmp_agent(
        SimpleNamespace(
            _checkpoints={
                "private:bad": {"checkpoint": {}, "metadata": {}, "channel_values": {}},
                "private:ok": {"checkpoint": {}, "metadata": {}, "channel_values": {}},
            }
        ),
        tmp_path,
    )

    agent._discard_checkpoint("private:bad")

    assert "private:bad" not in agent._checkpointer._checkpoints
    assert "private:ok" in agent._checkpointer._checkpoints


# ---------------------------------------------------------------------------
# 2. _save_checkpoints 兼容 langgraph MemorySaver.storage（新 bug 修复）
# ---------------------------------------------------------------------------


def test_save_checkpoints_writes_file_when_checkpointer_uses_storage(monkeypatch, tmp_path):
    """使用 ``storage`` 而非 ``_checkpoints`` 的真实 MemorySaver 必须能落盘。"""
    _patch_data_dir(monkeypatch, tmp_path)
    agent = _tmp_agent(
        SimpleNamespace(storage={"private:alice": {"": {"ckpt1": "payload"}}}),
        tmp_path,
    )

    agent._save_checkpoints()

    ckpt_file = tmp_path / "checkpoints.json"
    assert ckpt_file.exists(), "save 应当产生文件，修复前会因 AttributeError 静默吞掉"
    data = json.loads(ckpt_file.read_text(encoding="utf-8"))
    assert "private:alice" in data


# ---------------------------------------------------------------------------
# 3. _load_checkpoints 兼容 langgraph MemorySaver.storage（新 bug 修复）
# ---------------------------------------------------------------------------


def test_load_checkpoints_restores_into_storage_attribute(monkeypatch, tmp_path):
    """加载时若无 ``_checkpoints`` 但有 ``storage``，必须把数据写入 ``storage``。"""
    _patch_data_dir(monkeypatch, tmp_path)
    ckpt_file = tmp_path / "checkpoints.json"
    ckpt_file.write_text(
        json.dumps(
            {
                "private:bob": {
                    "checkpoint": {"id": "ckpt-1"},
                    "metadata": {"step": 1},
                    "channel_values": {},
                }
            }
        ),
        encoding="utf-8",
    )

    storage: dict = {}
    agent = _tmp_agent(SimpleNamespace(storage=storage), tmp_path)

    agent._load_checkpoints()

    assert "private:bob" in storage
    assert storage["private:bob"]["checkpoint"] == {"id": "ckpt-1"}


# ---------------------------------------------------------------------------
# 4. _discard_checkpoint 在 storage 模式下也能清掉失败 session
# ---------------------------------------------------------------------------


def test_discard_checkpoint_works_with_storage_attribute(monkeypatch, tmp_path):
    """修复前：_discard_checkpoint 对真实 MemorySaver 静默 no-op。
    修复后：必须真的把 thread 从 storage 中删除。
    """
    _patch_data_dir(monkeypatch, tmp_path)
    storage = {"private:failing": {"ns": {"id": "y"}}, "private:ok": {"ns": {"id": "z"}}}
    writes: dict = {("private:failing", "ns", "id"): "pending"}
    agent = _tmp_agent(SimpleNamespace(storage=storage, writes=writes), tmp_path)

    agent._discard_checkpoint("private:failing")

    assert "private:failing" not in storage
    assert "private:ok" in storage
    # 同步清理 writes
    assert not any(
        isinstance(k, tuple) and k and k[0] == "private:failing"
        for k in writes
    )