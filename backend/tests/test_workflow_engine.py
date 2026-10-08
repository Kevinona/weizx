"""weizx — workflow/engine.py 测试。

覆盖点：
- start_workflow / process_message / cancel_workflow 主流程
- Bug E1: re.search 只调用一次（不是两次）
- _try_parse_order 解析
- _SafeDict.format_map 缺 key 行为
- 状态机：自动 START → 下一态、FORWARD 分支、END 终止
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.workflow.engine import WorkflowEngine, _SafeDict, _ORDER_PARSE_RE  # noqa: E402


def _make_wf_def():
    """构造一个内存 workflow 定义（peiwang_order_flow 简化版）。"""
    return {
        "id": 1,
        "name": "test_flow",
        "description": "test",
        "trigger_intents": [],
        "enabled": True,
        "forward_to": "ops_group,backup_group",
        "states": {
            "START": {
                "name": "START",
                "on_enter": "请填写：游戏 段位 时长 预算",
                "transitions": [],
            },
            "FORM": {
                "name": "FORM",
                "on_enter": "收到订单：{game} {rank}",
                "transitions": [
                    {"pattern": r"^确认$", "next": "CONFIRM"},
                ],
            },
            "CONFIRM": {
                "name": "CONFIRM",
                "on_enter": "已确认。",
                "transitions": [
                    {"pattern": r"^继续$", "next": "FORWARD"},
                ],
            },
            "FORWARD": {
                "name": "FORWARD",
                "on_enter": "正在转发...",
                "transitions": [],
            },
            "DONE": {
                "name": "DONE",
                "on_enter": "完成。",
                "transitions": [],
            },
        },
    }


def _build_engine_with_workflow():
    eng = WorkflowEngine()
    eng._workflows["test_flow"] = _make_wf_def()
    return eng


# ---------------------------------------------------------------------------
# start_workflow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_workflow_creates_instance_and_runs_start_state():
    eng = _build_engine_with_workflow()
    action = await eng.start_workflow("test_flow", "user_1")
    assert action is not None
    assert action["action"] == "reply"
    assert "请填写" in action["reply"]
    inst = eng.get_instance("user_1")
    # Auto-transition START → FORM (next state)
    assert inst["state"] == "FORM"


@pytest.mark.asyncio
async def test_start_workflow_unknown_returns_none():
    eng = _build_engine_with_workflow()
    action = await eng.start_workflow("does_not_exist", "user_1")
    assert action is None
    assert eng.get_instance("user_1") is None


@pytest.mark.asyncio
async def test_start_workflow_disabled_returns_none():
    eng = _build_engine_with_workflow()
    eng._workflows["test_flow"]["enabled"] = False
    action = await eng.start_workflow("test_flow", "user_1")
    assert action is None


@pytest.mark.asyncio
async def test_start_workflow_overrides_existing():
    eng = _build_engine_with_workflow()
    await eng.start_workflow("test_flow", "user_1")
    await eng.start_workflow("test_flow", "user_1")
    inst = eng.get_instance("user_1")
    assert inst["state"] == "FORM"  # 重新初始化


# ---------------------------------------------------------------------------
# process_message 主路径
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_message_no_active_workflow_returns_none():
    eng = _build_engine_with_workflow()
    out = await eng.process_message("user_unknown", "hi")
    assert out == {"action": "none", "reply": "", "forward_targets": [], "ended": False}


@pytest.mark.asyncio
async def test_process_message_form_to_confirm():
    eng = _build_engine_with_workflow()
    await eng.start_workflow("test_flow", "user_1")
    out = await eng.process_message("user_1", "确认")
    assert out["action"] == "reply"
    assert "已确认" in out["reply"]
    assert eng.get_instance("user_1")["state"] == "CONFIRM"


@pytest.mark.asyncio
async def test_process_message_confirm_to_forward_includes_targets():
    eng = _build_engine_with_workflow()
    await eng.start_workflow("test_flow", "user_1")
    # 跳过 FORM 直接手动设到 CONFIRM
    eng.get_instance("user_1")["state"] = "CONFIRM"
    out = await eng.process_message("user_1", "继续")
    assert out["action"] == "forward"
    assert out["forward_targets"] == ["ops_group", "backup_group"]


@pytest.mark.asyncio
async def test_process_message_ended_state_cleans_up():
    eng = _build_engine_with_workflow()
    await eng.start_workflow("test_flow", "user_1")
    eng.get_instance("user_1")["state"] = "DONE"
    out = await eng.process_message("user_1", "x")
    assert out["ended"] is True
    assert eng.get_instance("user_1") is None  # cleaned up


@pytest.mark.asyncio
async def test_process_message_no_match_keeps_state():
    eng = _build_engine_with_workflow()
    await eng.start_workflow("test_flow", "user_1")
    out = await eng.process_message("user_1", "随便发点啥")
    assert out["action"] == "none"
    assert eng.get_instance("user_1")["state"] == "FORM"


# ---------------------------------------------------------------------------
# Bug E1: re.search must be called once per pattern, not twice
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_message_runs_re_search_once_per_pattern(monkeypatch):
    """确认 re.search 对每个 pattern 只调用一次。"""
    eng = _build_engine_with_workflow()
    await eng.start_workflow("test_flow", "user_1")

    import re as real_re

    call_count = {"n": 0}
    original_search = real_re.search

    def _counting_search(pattern, *args, **kwargs):
        call_count["n"] += 1
        return original_search(pattern, *args, **kwargs)

    monkeypatch.setattr("app.workflow.engine.re.search", _counting_search)
    out = await eng.process_message("user_1", "确认")
    # 1 个 transition 1 个 pattern，1 次调用。
    assert call_count["n"] == 1
    assert out["action"] == "reply"


@pytest.mark.asyncio
async def test_process_message_invalid_pattern_does_not_crash(monkeypatch):
    """坏 regex 不会让 process_message 崩。"""
    eng = _build_engine_with_workflow()
    eng._workflows["test_flow"]["states"]["FORM"]["transitions"] = [
        {"pattern": r"([unclosed", "next": "CONFIRM"},  # bad regex
        {"pattern": r"^确认$", "next": "CONFIRM"},  # good
    ]
    await eng.start_workflow("test_flow", "user_1")
    out = await eng.process_message("user_1", "确认")
    assert out["action"] == "reply"
    # bad regex 被跳过，good 仍能命中


# ---------------------------------------------------------------------------
# _try_parse_order
# ---------------------------------------------------------------------------


def test_parse_order_basic():
    result = WorkflowEngine._try_parse_order("王者荣耀 钻石 3 150 带我一个")
    assert result is not None
    assert result["game"] == "王者荣耀"
    assert result["rank"] == "钻石"
    assert result["hours"] == 3.0
    assert result["budget"] == 150.0
    assert "带我一个" in result["notes"]


def test_parse_order_with_unit_suffix():
    result = WorkflowEngine._try_parse_order("原神 大师级 2小时 200元")
    assert result["hours"] == 2.0
    assert result["budget"] == 200.0
    assert result["notes"] == ""


def test_parse_order_garbage_returns_none():
    assert WorkflowEngine._try_parse_order("hello world") is None


def test_parse_order_handles_decimal_hours():
    result = WorkflowEngine._try_parse_order("CS2 黄金 1.5 80 排位")
    assert result["hours"] == 1.5
    assert result["budget"] == 80.0


# ---------------------------------------------------------------------------
# _SafeDict
# ---------------------------------------------------------------------------


def test_safe_dict_returns_empty_for_missing_key():
    d = _SafeDict({"a": 1})
    assert d["a"] == 1
    assert d["missing"] == ""
    assert d["another_missing"] == ""


def test_safe_dict_works_with_format_map():
    d = _SafeDict({"game": "王者荣耀"})
    assert "王者荣耀" in "{game} {rank}".format_map(d)
    assert "{rank}" not in "{game} {rank}".format_map(d)  # missing → "" not "{rank}"


# ---------------------------------------------------------------------------
# active_count / cancel
# ---------------------------------------------------------------------------


def test_active_count_tracks_instances():
    eng = WorkflowEngine()
    assert eng.active_count() == 0
    eng._instances["u1"] = {"workflow": "x", "state": "A", "data": {}}
    eng._instances["u2"] = {"workflow": "y", "state": "B", "data": {}}
    assert eng.active_count() == 2


@pytest.mark.asyncio
async def test_cancel_workflow_returns_true_if_existed():
    eng = _build_engine_with_workflow()
    await eng.start_workflow("test_flow", "user_1")
    assert eng.cancel_workflow("user_1") is True
    assert eng.get_instance("user_1") is None


def test_cancel_workflow_returns_false_if_not_existed():
    eng = _build_engine_with_workflow()
    assert eng.cancel_workflow("never_started") is False


# ---------------------------------------------------------------------------
# 内部：state_def 缺省
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_message_missing_state_def_returns_none_and_cleans_up():
    eng = _build_engine_with_workflow()
    await eng.start_workflow("test_flow", "user_1")
    # 删掉 state definition 模拟「definition 消失」
    del eng._workflows["test_flow"]["states"]["FORM"]
    out = await eng.process_message("user_1", "hi")
    assert out["action"] == "none"
    assert eng.get_instance("user_1") is None
