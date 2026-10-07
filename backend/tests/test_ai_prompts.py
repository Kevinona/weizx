"""weizx — prompts.py 行为测试。

验证 ``format_prompt`` 的 SafeDict 占位符保留逻辑，以及
``get_prompt_for_context`` 在群聊 / 私聊场景下的模板选择。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# 直接 import prompts.py 跳过 app.ai.__init__ 的 langchain 链路
import importlib.util

_PROMPTS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "app", "ai", "prompts.py"
)
spec = importlib.util.spec_from_file_location("_prompts_under_test", _PROMPTS_PATH)
prompts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prompts)

format_prompt = prompts.format_prompt
get_prompt_for_context = prompts.get_prompt_for_context
PRIVATE_CHAT_PROMPT = prompts.PRIVATE_CHAT_PROMPT
GROUP_CHAT_PROMPT = prompts.GROUP_CHAT_PROMPT


# ---------------------------------------------------------------------------
# 1. format_prompt 保留未提供的占位符（SafeDict 行为）
# ---------------------------------------------------------------------------


def test_format_prompt_preserves_missing_placeholders():
    """未提供的变量必须以 ``{name}`` 原样保留，避免静默替换为空。"""
    out = format_prompt("hello {a}, today is {b}", a="Kevin")

    assert "Kevin" in out
    # ``{b}`` 必须原样保留
    assert "{b}" in out
    assert "hello Kevin, today is {b}" == out


# ---------------------------------------------------------------------------
# 2. format_prompt 支持转义花括号 ``{{var}}`` 解析为 ``{var}``
# ---------------------------------------------------------------------------


def test_format_prompt_handles_literal_double_braces():
    """``{{name}}`` 是 Python str.format 的字面量花括号，应解析为 ``{name}``。"""
    out = format_prompt("代码示例：{{not_replaced}} and {a}", a="X")

    assert out == "代码示例：{not_replaced} and X"


# ---------------------------------------------------------------------------
# 3. get_prompt_for_context 在群聊 / 私聊下分别选对应模板
# ---------------------------------------------------------------------------


def test_get_prompt_for_context_selects_correct_template():
    """is_group=True 必须使用 GROUP_CHAT_PROMPT，False 使用 PRIVATE_CHAT_PROMPT。"""
    common = dict(
        user_name="Alice",
        current_time="2026-01-01 12:00:00",
        chat_context="-",
        knowledge_context="-",
        memory_context="-",
        self_awareness="",
    )

    group_out = get_prompt_for_context(is_group=True, room_name="测试群", **common)
    private_out = get_prompt_for_context(is_group=False, **common)

    # 群聊模板独有字段
    assert "群聊名称：测试群" in group_out
    assert "## 群聊氛围" in group_out
    # 私聊模板独有字段
    assert "## 你的风格" in private_out
    # 互斥校验：群聊输出不应包含私聊标题
    assert "## 你的风格" not in group_out
    assert "## 群聊氛围" not in private_out


# ---------------------------------------------------------------------------
# 4. format_prompt 在缺失字段时不会抛 KeyError（防御式）
# ---------------------------------------------------------------------------


def test_format_prompt_does_not_raise_on_missing_keys():
    """SafeDict 必须吞掉 KeyError，让调用方不必传入全部变量。"""
    # 即便模板引用了完全不存在的键
    out = format_prompt(
        "A={a} B={b} C={c}",
        a="1",
    )

    assert "A=1" in out
    assert "{b}" in out
    assert "{c}" in out