"""weizx — style_distiller.py 测试。

覆盖点：
- _load_cache 在 new cache 过期时正确回退到 legacy（Bug 9）
- _parse_response 处理 fence 包裹 / 裸 JSON / 无法解析
- _sample_messages 边界
- _normalize_skill 字段合并

不引入真实 LLM/Chromadb 依赖（用 sys.modules mock 替换 langchain_core / app.ai.models）。
"""

from __future__ import annotations

import json
import os
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# --- 在导入 style_distiller 之前 mock 它依赖的两个模块 ----------------------
_models = ModuleType("app.ai.models")


def _fake_create_llm(_config=None):
    return MagicMock()


_models.create_llm = _fake_create_llm

_lc_core = ModuleType("langchain_core.messages")
_lc_core.HumanMessage = lambda content: SimpleNamespace(content=content)
_lc_core.SystemMessage = lambda content: SimpleNamespace(content=content)

sys.modules.setdefault("app.ai.models", _models)
sys.modules.setdefault("langchain_core.messages", _lc_core)
sys.modules.setdefault("langchain_core", ModuleType("langchain_core"))
sys.modules["langchain_core"].messages = _lc_core

from app.ai.style_distiller import StyleDistiller  # noqa: E402


# ---------------------------------------------------------------------------
# Bug 9: _load_cache falls back to legacy when new cache is stale
# ---------------------------------------------------------------------------


def test_load_cache_falls_back_to_legacy_when_new_cache_version_stale(tmp_path, monkeypatch):
    """当 new cache 存在但 version 不匹配时，必须回退到 legacy cache 而不是返回空。"""
    new_cache = tmp_path / "persona_skill.json"
    new_cache.write_text(
        json.dumps({"version": 1, "persona_name": "old"}, ensure_ascii=False),
        encoding="utf-8",
    )
    legacy_cache = tmp_path / "persona.json"
    legacy_cache.write_text(
        json.dumps(
            {
                "persona_name": "legacy_user",
                "tone": "casual",
                "catchphrases": ["好的", "ok"],
                "signature_traits": ["直接"],
                "emoji_style": "少用",
                "sentence_style": "短句",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("app.utils.paths.get_data_dir", lambda: tmp_path)

    d = StyleDistiller()
    # 旧 cache 存在但 version 错 → 应该回退到 legacy
    assert d.has_persona is True
    assert d.meta["name"] == "legacy_user"
    assert d.meta["source"] == "legacy_persona_json"


def test_load_cache_uses_new_cache_when_version_matches(tmp_path, monkeypatch):
    """new cache version 匹配时优先用 new。"""
    new_cache = tmp_path / "persona_skill.json"
    new_cache.write_text(
        json.dumps(
            {
                "version": 2,
                "mode": "contextual",
                "meta": {"name": "fresh", "source": "wechat"},
                "self_memory_md": "## Self Memory",
                "persona_md": "## Persona",
                "runtime_prompt_private": "private",
                "runtime_prompt_group": "group",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    # 故意放一个 legacy 文件，若 new 优先就不应被读到
    legacy_cache = tmp_path / "persona.json"
    legacy_cache.write_text(
        json.dumps({"persona_name": "should_not_load"}, ensure_ascii=False),
        encoding="utf-8",
    )

    monkeypatch.setattr("app.utils.paths.get_data_dir", lambda: tmp_path)

    d = StyleDistiller()
    assert d.meta["name"] == "fresh"


def test_load_cache_returns_empty_when_no_files(tmp_path, monkeypatch):
    monkeypatch.setattr("app.utils.paths.get_data_dir", lambda: tmp_path)
    d = StyleDistiller()
    assert d.has_persona is False
    assert d.persona is None
    assert d.mode == "contextual"  # DEFAULT_MODE
    assert d.build_prompt() == ""


# ---------------------------------------------------------------------------
# _parse_response: fence / bare JSON / unparseable
# ---------------------------------------------------------------------------


def test_parse_response_unwraps_json_code_fence():
    content = '```json\n{"self_memory_md": "x", "persona_md": "y"}\n```'
    out = StyleDistiller._parse_response(content)
    assert out["self_memory_md"] == "x"
    assert out["persona_md"] == "y"


def test_parse_response_handles_bare_json():
    content = '{"persona_md": "bare"}'
    out = StyleDistiller._parse_response(content)
    assert out["persona_md"] == "bare"


def test_parse_response_recovers_object_in_prose():
    content = '前面的话 {"meta": {"name": "X"}, "persona_md": "P"} 后面的话'
    out = StyleDistiller._parse_response(content)
    assert out["meta"]["name"] == "X"


def test_parse_returns_empty_on_total_garbage():
    out = StyleDistiller._parse_response("not json at all")
    assert out == {}


# ---------------------------------------------------------------------------
# _sample_messages: edge cases
# ---------------------------------------------------------------------------


def test_sample_messages_sampling_reduces_count():
    """10 条消息 ratio=0.7 → target=7 → 实际采样 7 条。"""
    msgs = [f"m{i}" for i in range(10)]
    out = StyleDistiller._sample_messages(msgs, ratio=0.7)
    assert len(out) == 7


def test_sample_messages_caps_at_3000():
    msgs = [f"m{i}" for i in range(10000)]
    out = StyleDistiller._sample_messages(msgs, ratio=0.7)
    # 10000 * 0.7 = 7000 → cap at 3000
    assert len(out) == 3000


def test_sample_messages_evenly_distributed():
    msgs = [f"m{i}" for i in range(100)]
    out = StyleDistiller._sample_messages(msgs, ratio=0.5)
    # target=50, step=2.0, indices = [0,2,4,...,98] → 50 items
    assert len(out) == 50
    assert out[0] == "m0"
    assert out[-1] == "m98"


# ---------------------------------------------------------------------------
# build_prompt routes to private vs group key
# ---------------------------------------------------------------------------


def test_build_prompt_returns_empty_when_no_cached_skill():
    d = StyleDistiller.__new__(StyleDistiller)
    d._cached_skill = None
    assert d.build_prompt() == ""
    assert d.build_prompt(is_group=True) == ""


def test_build_prompt_picks_correct_key():
    d = StyleDistiller.__new__(StyleDistiller)
    d._cached_skill = {
        "runtime_prompt_private": "priv-text",
        "runtime_prompt_group": "group-text",
    }
    assert d.build_prompt() == "priv-text"
    assert d.build_prompt(is_group=True) == "group-text"


# ---------------------------------------------------------------------------
# _normalize_skill 字段合并（legacy 入口 + 异常输入）
# ---------------------------------------------------------------------------


def test_normalize_skill_rejects_non_dict():
    s = StyleDistiller._normalize_skill("not a dict", mode="x")
    assert s["version"] == 2
    assert s["meta"]["name"] == "我"


def test_normalize_skill_falls_back_to_legacy_when_no_persona_md():
    """payload 没有 persona_md 但有 tone → 走 legacy 路径。"""
    payload = {"tone": "casual", "persona_name": "P"}
    s = StyleDistiller._normalize_skill(payload, mode="contextual")
    assert s["meta"]["name"] == "P"
    assert "Layer 0" in s["persona_md"]


def test_normalize_skill_fills_missing_fields():
    """全空 payload → 不会崩，所有字段都是合理默认。"""
    s = StyleDistiller._normalize_skill({})
    assert s["version"] == 2
    assert s["self_memory_md"]  # 非空
    assert s["persona_md"]  # 非空
    assert "build_private" not in s["runtime_prompt_private"]  # 不是模板代码


# ---------------------------------------------------------------------------
# save_edits merges meta correctly
# ---------------------------------------------------------------------------


def test_save_edits_merges_meta_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr("app.utils.paths.get_data_dir", lambda: tmp_path)
    d = StyleDistiller()
    # 设置一个空 skill 起点
    d._cached_skill = d._empty_skill()

    out = d.save_edits(meta={"name": "Edited"}, runtime_prompt_private="hello")
    assert out["meta"]["name"] == "Edited"
    assert out["runtime_prompt_private"] == "hello"

    # 持久化
    cache_file = tmp_path / "persona_skill.json"
    assert cache_file.exists()
    loaded = json.loads(cache_file.read_text(encoding="utf-8"))
    assert loaded["meta"]["name"] == "Edited"
