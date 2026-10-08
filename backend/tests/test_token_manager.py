"""weizx — token_manager.py 测试。

覆盖点：
- count_tokens 处理 str / list[BaseMessage] / multimodal content
- count_tokens 处理 None content / bytes / dict
- _guess_context_size substring 行为

不引入真实 tiktoken / langchain_core（用 sys.modules mock 替换）。
"""

from __future__ import annotations

import os
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# --- mock tiktoken + langchain_core BEFORE importing token_manager ------------

_tiktoken = ModuleType("tiktoken")


class _FakeEncoding:
    """Mimics the tiktoken.Encoding callable interface."""

    def encode(self, text: str) -> list[int]:
        # 1 token per character (good enough for assertions on relative counts)
        return [ord(c) for c in text]

    def __call__(self, text: str) -> list[int]:
        return self.encode(text)


_tiktoken.encoding_for_model = lambda name: _FakeEncoding()
_tiktoken.get_encoding = lambda name: _FakeEncoding()
sys.modules.setdefault("tiktoken", _tiktoken)

_lc_messages = ModuleType("langchain_core.messages")


class _FakeBaseMessage:
    """Stand-in for langchain_core.messages.BaseMessage — only `content` matters here."""

    def __init__(self, content):
        self.content = content


_lc_messages.BaseMessage = _FakeBaseMessage
_lc_messages.trim_messages = lambda messages, **kwargs: messages  # no-op for tests

_lc_core = ModuleType("langchain_core")
_lc_core.messages = _lc_messages
sys.modules.setdefault("langchain_core", _lc_core)
sys.modules.setdefault("langchain_core.messages", _lc_messages)
sys.modules["langchain_core.messages"] = _lc_messages
sys.modules.setdefault("langchain_core.messages", _lc_messages)

from app.ai.token_manager import TokenManager, _extract_text  # noqa: E402

_extract_text_static = staticmethod(_extract_text)


# ---------------------------------------------------------------------------
# count_tokens: string input
# ---------------------------------------------------------------------------


def test_count_tokens_for_string():
    tm = TokenManager("gpt-4o")
    n = tm.count_tokens("hello world")
    # 11 chars, fake encoding = 1 token / char
    assert n == 11


def test_count_tokens_for_empty_string():
    tm = TokenManager("gpt-4o")
    assert tm.count_tokens("") == 0


# ---------------------------------------------------------------------------
# count_tokens: list of BaseMessage
# ---------------------------------------------------------------------------


def test_count_tokens_for_list_of_messages_with_string_content():
    tm = TokenManager("gpt-4o")
    msgs = [
        _FakeBaseMessage("hello"),
        _FakeBaseMessage("world"),
    ]
    assert tm.count_tokens(msgs) == 10  # 5 + 5


def test_count_tokens_handles_none_content():
    tm = TokenManager("gpt-4o")
    msgs = [_FakeBaseMessage(None), _FakeBaseMessage("")]
    assert tm.count_tokens(msgs) == 0


def test_count_tokens_handles_multimodal_list_content():
    """Bug B: msg.content 是 list（多模态）时，老代码会 str(list) 进去。新代码只取 text 段。"""
    tm = TokenManager("gpt-4o")
    multimodal_msg = _FakeBaseMessage(
        [
            {"type": "text", "text": "看看这张图"},
            {"type": "image_url", "image_url": {"url": "http://..."}},
            {"type": "text", "text": "谢谢"},
        ]
    )
    n = tm.count_tokens([multimodal_msg])
    # Implementation joins text segments with '\n':
    # "看看这张图" (5) + "\n" (1) + "谢谢" (2) = 8
    assert n == 8


def test_count_tokens_handles_dict_list_with_no_text():
    """全是 image，不贡献 token。"""
    tm = TokenManager("gpt-4o")
    msg = _FakeBaseMessage(
        [
            {"type": "image_url", "image_url": {"url": "http://a"}},
            {"type": "image_url", "image_url": {"url": "http://b"}},
        ]
    )
    assert tm.count_tokens([msg]) == 0


def test_count_tokens_handles_string_part_in_multimodal():
    """list 中混有裸 string 元素（部分 LangChain 实现可能这样）。"""
    tm = TokenManager("gpt-4o")
    msg = _FakeBaseMessage([{"type": "text", "text": "abc"}, "def"])
    # "abc" + "\n" + "def" = 7
    assert tm.count_tokens([msg]) == 7


def test_count_tokens_handles_bytes_message():
    tm = TokenManager("gpt-4o")
    msg = _FakeBaseMessage(b"hello bytes")
    # 11 bytes → utf-8 11 chars → 11 tokens
    assert tm.count_tokens([msg]) == 11


def test_count_tokens_handles_unknown_type():
    """非 BaseMessage 元素：fallback 到 str() 编码。"""
    tm = TokenManager("gpt-4o")
    msgs = [42, 3.14, SimpleNamespace(content="abc")]
    # 42 → "42" (2), 3.14 → "3.14" (4), SimpleNamespace → "abc" (3) = 9
    assert tm.count_tokens(msgs) == 9


# ---------------------------------------------------------------------------
# _guess_context_size
# ---------------------------------------------------------------------------


def test_guess_context_size_picks_substring_match():
    """`deepseek` substring in 'deepseek-chat' returns deepseek-chat size."""
    size = TokenManager._guess_context_size("deepseek-chat")
    assert size == 64000


def test_guess_context_size_unknown_falls_back_to_8192():
    size = TokenManager._guess_context_size("some-unknown-model-9000")
    assert size == 8192


def test_guess_context_size_iterates_dict_in_definition_order():
    """Python 3.7+ dict insertion order is preserved — gpt-4o is listed before gpt-4,
    so 'gpt-4o' wins. If dict order ever changes (e.g. external override), this
    test would catch it."""
    # If the order were wrong, `gpt-4o-mini` would return 8192 (gpt-4's size).
    size = TokenManager._guess_context_size("gpt-4o-mini")
    assert size == 128000


# ---------------------------------------------------------------------------
# TokenManager init: max_input_tokens override
# ---------------------------------------------------------------------------


def test_max_input_tokens_override():
    tm = TokenManager("gpt-4o", max_input_tokens=2000)
    assert tm.context_size == 2000
    # OUTPUT_RESERVE_RATIO = 0.3 → max_input = 2000 * 0.7 = 1400
    assert tm.max_input_tokens == 1400


def test_default_uses_guessed_context_and_reserves_output():
    tm = TokenManager("gpt-4o")
    assert tm.context_size == 128000
    assert tm.max_input_tokens == 89600


def test_unknown_model_tiktoken_falls_back_to_cl100k_base():
    """`encoding_for_model` raises KeyError → we use get_encoding('cl100k_base')."""
    # Since our fake `encoding_for_model` always succeeds, this just confirms
    # the encoding attribute is set (not None) on every construction.
    tm = TokenManager("any-model")
    assert tm._encoding is not None


# ---------------------------------------------------------------------------
# trim: fallback to last 20 when langchain fails
# ---------------------------------------------------------------------------


def test_trim_fallback_returns_last_20_messages(monkeypatch):
    """如果 trim_messages 抛异常，必须 fallback 到最后 20 条而非返回全部。"""
    from app.ai import token_manager as tm_module

    def _raise(*args, **kwargs):
        raise RuntimeError("langchain broke")

    monkeypatch.setattr(tm_module, "trim_messages", _raise)

    tm = TokenManager("gpt-4o")
    msgs = [_FakeBaseMessage(f"msg-{i}") for i in range(50)]
    out = tm.trim(msgs)
    assert len(out) == 20
    assert out[0].content == "msg-30"  # last 20 of 50 → indices 30..49
    assert out[-1].content == "msg-49"


def test_trim_fallback_returns_all_when_fewer_than_20(monkeypatch):
    """输入 < 20 条时 fallback 仍要返回所有（不是空）。"""
    from app.ai import token_manager as tm_module

    def _raise(*args, **kwargs):
        raise RuntimeError("nope")

    monkeypatch.setattr(tm_module, "trim_messages", _raise)

    tm = TokenManager("gpt-4o")
    msgs = [_FakeBaseMessage(f"m{i}") for i in range(5)]
    out = tm.trim(msgs)
    assert len(out) == 5


# ---------------------------------------------------------------------------
# trim: system_prompt 占配额
# ---------------------------------------------------------------------------


def test_trim_reserves_space_for_system_prompt(monkeypatch):
    """trim 必须把 system_prompt token 从可用配额里扣掉。"""
    captured = {}

    def _spy(messages, **kwargs):
        captured.update(kwargs)
        return messages

    from app.ai import token_manager as tm_module

    monkeypatch.setattr(tm_module, "trim_messages", _spy)

    tm = TokenManager("gpt-4o", max_input_tokens=1000)
    msgs = [_FakeBaseMessage("hi")]
    tm.trim(msgs, system_prompt="x" * 100)  # 100 chars

    # max_input = 700, system = 100, available = max(700-100-500, 500) = 500
    assert captured["max_tokens"] == 500
