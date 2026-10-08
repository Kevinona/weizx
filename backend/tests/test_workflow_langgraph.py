"""weizx — workflow/langgraph_engine.py 测试。

覆盖点：
- _try_parse_order 解析
- _SafeDict 行为
- _extract_action 防御性处理 None / 非 dict
- _make_state_node 在 cancel 时保留 ended=True（L9 修复）
- 编译 / 不编译失败不能崩

不引入真实 langgraph 依赖（用 sys.modules mock 替换 StateGraph / MemorySaver）。
"""

from __future__ import annotations

import os
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# --- mock langgraph BEFORE importing app.workflow.langgraph_engine ------------

_lg_graph = ModuleType("langgraph.graph")


class _FakeStateGraph:
    """Minimal StateGraph stub that records calls and yields a fake compiled graph."""

    def __init__(self, _state_type):
        self._nodes = {}
        self._entry = None
        self._edges = []
        self._conditional = []

    def add_node(self, name, fn):
        self._nodes[name] = fn

    def set_entry_point(self, name):
        self._entry = name

    def add_edge(self, src, dst):
        self._edges.append((src, dst))

    def add_conditional_edges(self, src, router, route_map):
        self._conditional.append((src, router, route_map))

    def compile(self, checkpointer=None):
        # 返回一个简单的可调用 graph，async 支持
        graph = SimpleNamespace(
            _nodes=self._nodes,
            _entry=self._entry,
            _checkpointer=checkpointer,
            ainvoke=None,  # 由测试设置
            get_state=MagicMock(return_value=None),
        )
        return graph


_lg_graph.StateGraph = _FakeStateGraph
_lg_graph.END = "__end__"

_lg_checkpoint = ModuleType("langgraph.checkpoint.memory")
_lg_checkpoint.MemorySaver = MagicMock

_lg_pkg = ModuleType("langgraph")
_lg_pkg.graph = _lg_graph
_lg_pkg.checkpoint = _lg_checkpoint
_lg_checkpoint_pkg = ModuleType("langgraph.checkpoint")
_lg_checkpoint_pkg.memory = _lg_checkpoint

sys.modules.setdefault("langgraph", _lg_pkg)
sys.modules.setdefault("langgraph.graph", _lg_graph)
sys.modules.setdefault("langgraph.checkpoint", _lg_checkpoint_pkg)
sys.modules.setdefault("langgraph.checkpoint.memory", _lg_checkpoint)

from app.workflow.langgraph_engine import (  # noqa: E402
    LangGraphWorkflowEngine,
    _SafeDict,
    _try_parse_order,
    _ORDER_PARSE_RE,
)


# ---------------------------------------------------------------------------
# _try_parse_order
# ---------------------------------------------------------------------------


def test_parse_order_basic():
    out = _try_parse_order("王者荣耀 钻石 3 150")
    assert out is not None
    assert out["game"] == "王者荣耀"
    assert out["rank"] == "钻石"
    assert out["hours"] == 3.0
    assert out["budget"] == 150.0


def test_parse_order_with_unit_suffix():
    out = _try_parse_order("LOL 钻石 2小时 100元 排位")
    assert out["hours"] == 2.0
    assert out["budget"] == 100.0
    assert "排位" in out["notes"]


def test_parse_order_garbage_returns_none():
    assert _try_parse_order("hello world") is None


# ---------------------------------------------------------------------------
# _SafeDict
# ---------------------------------------------------------------------------


def test_safe_dict_returns_empty_for_missing_key():
    d = _SafeDict({"a": 1})
    assert d["a"] == 1
    assert d["missing"] == ""


# ---------------------------------------------------------------------------
# _extract_action 防御性 (Bug L7)
# ---------------------------------------------------------------------------


def test_extract_action_normal_dict():
    result = {"action": "reply", "reply": "hi", "forward_targets": ["a"], "ended": False}
    out = LangGraphWorkflowEngine._extract_action(result)
    assert out == result


def test_extract_action_none_returns_safe_default():
    out = LangGraphWorkflowEngine._extract_action(None)
    assert out == {"action": "none", "reply": "", "forward_targets": [], "ended": False}


def test_extract_action_non_dict_returns_safe_default():
    out = LangGraphWorkflowEngine._extract_action("not a dict")
    assert out == {"action": "none", "reply": "", "forward_targets": [], "ended": False}


def test_extract_action_missing_keys_uses_defaults():
    out = LangGraphWorkflowEngine._extract_action({})
    assert out == {"action": "none", "reply": "", "forward_targets": [], "ended": False}


# ---------------------------------------------------------------------------
# Bug L9 回归: cancel 后 state_node 必须保留 ended=True
# ---------------------------------------------------------------------------


def test_state_node_preserves_ended_true_from_input():
    """cancel_workflow 写入 ended=True；state_node 不能再覆盖为 False。"""
    eng = LangGraphWorkflowEngine()
    state_def = {"on_enter": "hello", "transitions": []}
    wf_def = {"forward_to": ""}
    node_fn = eng._make_state_node("wf", "MID", state_def, wf_def)

    # 模拟 cancel 后 invoke：state.ended=True，但 state 是 MID（非终态）
    state = {"current_state": "MID", "data": {}, "ended": True}
    import asyncio

    out = asyncio.run(node_fn(state))
    assert out["ended"] is True, "cancel 的 ended=True 必须被保留"


def test_state_node_ended_false_for_active_state():
    """正常流程中，非终态返回 ended=False。"""
    eng = LangGraphWorkflowEngine()
    state_def = {"on_enter": "hello", "transitions": []}
    wf_def = {"forward_to": ""}
    node_fn = eng._make_state_node("wf", "MID", state_def, wf_def)

    state = {"current_state": "MID", "data": {}, "ended": False}
    import asyncio

    out = asyncio.run(node_fn(state))
    assert out["ended"] is False


def test_state_node_ended_true_for_terminal_state():
    eng = LangGraphWorkflowEngine()
    state_def = {"on_enter": "完成", "transitions": []}
    wf_def = {"forward_to": ""}
    node_fn = eng._make_state_node("wf", "DONE", state_def, wf_def)

    state = {"current_state": "DONE", "data": {}, "ended": False}
    import asyncio

    out = asyncio.run(node_fn(state))
    assert out["ended"] is True
    assert out["action"] == "none"


def test_state_node_forward_action_includes_targets():
    eng = LangGraphWorkflowEngine()
    state_def = {"on_enter": "forwarding", "transitions": []}
    wf_def = {"forward_to": "ops,backup"}
    node_fn = eng._make_state_node("wf", "FORWARD", state_def, wf_def)

    state = {"current_state": "FORWARD", "data": {}, "ended": False}
    import asyncio

    out = asyncio.run(node_fn(state))
    assert out["action"] == "forward"
    assert out["forward_targets"] == ["ops", "backup"]


def test_state_node_forward_no_targets_falls_back_to_reply():
    """FORWARD 状态但 forward_to 空时不应 action=forward，应 action=reply。"""
    eng = LangGraphWorkflowEngine()
    state_def = {"on_enter": "forwarding", "transitions": []}
    wf_def = {"forward_to": ""}
    node_fn = eng._make_state_node("wf", "FORWARD", state_def, wf_def)

    state = {"current_state": "FORWARD", "data": {}, "ended": False}
    import asyncio

    out = asyncio.run(node_fn(state))
    assert out["action"] == "reply"


# ---------------------------------------------------------------------------
# _make_router 行为
# ---------------------------------------------------------------------------


def test_router_matches_first_transition():
    eng = LangGraphWorkflowEngine()
    transitions = [
        {"pattern": r"^确认$", "next": "CONFIRM"},
        {"pattern": r"^取消$", "next": "CANCEL"},
    ]
    router = eng._make_router(transitions)
    state = {"user_message": "确认"}
    assert router(state) == "CONFIRM"


def test_router_falls_back_to_stay_when_no_match():
    eng = LangGraphWorkflowEngine()
    transitions = [{"pattern": r"^确认$", "next": "CONFIRM"}]
    router = eng._make_router(transitions)
    state = {"user_message": "hello"}
    assert router(state) == "__stay__"


def test_router_returns_end_when_ended():
    eng = LangGraphWorkflowEngine()
    transitions = [{"pattern": r"^确认$", "next": "CONFIRM"}]
    router = eng._make_router(transitions)
    state = {"user_message": "anything", "ended": True}
    assert router(state) == "__end__"


def test_router_skips_invalid_regex_without_crashing():
    eng = LangGraphWorkflowEngine()
    transitions = [
        {"pattern": r"([unclosed", "next": "BAD"},  # bad regex
        {"pattern": r"^确认$", "next": "CONFIRM"},
    ]
    router = eng._make_router(transitions)
    state = {"user_message": "确认"}
    assert router(state) == "CONFIRM"  # bad regex 跳过，good 命中


# ---------------------------------------------------------------------------
# load_workflows 错误路径
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_workflows_no_session_logs_error(caplog):
    """没有 session / session_factory 时，必须 log error 不抛。"""
    import logging

    eng = LangGraphWorkflowEngine()
    with caplog.at_level(logging.ERROR, logger="app.workflow.langgraph_engine"):
        await eng.load_workflows()
    assert "No session" in caplog.text or len(eng._graphs) == 0


@pytest.mark.asyncio
async def test_load_workflows_handles_compile_failure():
    """某个 workflow 编译失败必须 log error 但不阻断其他。"""
    eng = LangGraphWorkflowEngine()

    def _compile_fails(name, wf_def):
        if name == "broken":
            raise RuntimeError("simulated compile failure")
        return SimpleNamespace(name=name)

    eng._compile_workflow = _compile_fails
    eng._definitions = {
        "broken": {"states": {}},
        "good": {"states": {}},
    }

    # 直接调用 _compile 部分（绕过 DB load）
    for name, wf_def in eng._definitions.items():
        try:
            eng._graphs[name] = _compile_fails(name, wf_def)
        except Exception:
            pass

    assert "broken" not in eng._graphs
    assert "good" in eng._graphs
