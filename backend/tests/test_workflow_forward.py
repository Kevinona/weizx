"""weizx — workflow/forward_engine.py 测试。

覆盖点：
- ForwardEngine.load_rules：显式 session / session_factory / 无可用的退化
- ForwardEngine.match_and_forward：
    * Bug T1 (回归): workflow:<flow>.<STATE> 必须按 docstring 仅精确匹配,
      不能再 prefix-match 到别的 STATE
    * Bug T1 (相邻): workflow:<flow>（无 STATE 后缀）保持 prefix 通配语义
    * keyword 触发：任一关键字命中即匹配
    * enabled=False 的 rule 被跳过
- ForwardEngine.send_to_targets：成功 / 异常 per-target 隔离
- _SafeDict.__missing__ 返回空串
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.workflow.forward_engine import ForwardEngine, _SafeDict  # noqa: E402


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeAsyncSession:
    """最小 async session, 只支持 .execute(stmt) -> Result.scalars().all()."""

    def __init__(self, rows):
        self.rows = rows

    async def execute(self, stmt):  # noqa: ARG002
        return _FakeResult(self.rows)


def _row(id=1, name="r1", trigger="workflow:foo.FORWARD",
         targets=("chat1@chatroom",), template=""):
    """ForwardRule 行替代品（SQLAlchemy 行对象）。"""
    return SimpleNamespace(
        id=id, name=name, trigger=trigger,
        targets=list(targets), template=template, enabled=True,
    )


# ---------------------------------------------------------------------------
# load_rules
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_load_rules_uses_session_factory():
    rows = [_row(name="r1"), _row(name="r2", trigger="keyword:hi")]
    cm = AsyncMock()
    cm.__aenter__.return_value = _FakeAsyncSession(rows)
    cm.__aexit__.return_value = None
    factory = MagicMock(return_value=cm)

    engine = ForwardEngine(session_factory=factory)
    await engine.load_rules()

    factory.assert_called_once()
    assert [r["name"] for r in engine._rules] == ["r1", "r2"]
    assert engine._rules[1]["trigger"] == "keyword:hi"


@pytest.mark.asyncio
async def test_load_rules_with_explicit_session_takes_precedence():
    factory = MagicMock()
    rows = [_row(name="explicit")]
    engine = ForwardEngine(session_factory=factory)
    await engine.load_rules(session=_FakeAsyncSession(rows))
    assert len(engine._rules) == 1
    assert engine._rules[0]["name"] == "explicit"
    # 显式 session 路径不应触发 session_factory
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_load_rules_without_session_noop(caplog):
    """Neither session nor session_factory -> 内部分支只 log, 不崩溃."""
    engine = ForwardEngine()
    with caplog.at_level("ERROR"):
        await engine.load_rules()
    assert engine._rules == []
    assert any("cannot load forward rules" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# match_and_forward
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_match_keyword_trigger_contains_any():
    engine = ForwardEngine()
    engine._rules = [{
        "id": 1, "name": "kw1",
        "trigger": "keyword:陪玩,代练",
        "targets": ["chat1@chatroom", "chat2@chatroom"],
        "template": "",  # empty -> _render_template returns var dump
        "enabled": True,
    }]
    out = await engine.match_and_forward(
        {"trigger": "我要代练一单", "data": {"game": "王者荣耀"}}
    )
    # 1 rule * 2 targets
    assert len(out) == 2
    assert {o["target"] for o in out} == {"chat1@chatroom", "chat2@chatroom"}
    # Empty template -> 变量 dump
    assert out[0]["rendered_msg"] == "game: 王者荣耀"


@pytest.mark.asyncio
async def test_match_workflow_state_rule_is_exact_only():
    """Bug T1 regression: workflow:foo.FORWARD 不应 prefix-match 到别的 STATE."""
    engine = ForwardEngine()
    engine._rules = [{
        "id": 1, "name": "state-rule",
        "trigger": "workflow:foo.FORWARD",
        "targets": ["chat1@chatroom"],
        "template": "",
        "enabled": True,
    }]

    # 精确命中 —— 必须匹配
    out = await engine.match_and_forward(
        {"trigger": "workflow:foo.FORWARD", "data": {}}
    )
    assert len(out) == 1

    # 不同 STATE —— 必须不匹配 (Bug 修复前会 prefix-match 错误命中)
    out = await engine.match_and_forward(
        {"trigger": "workflow:foo.OTHER_STATE", "data": {}}
    )
    assert out == []

    # 看着像 FORWARD 但实际是别的、长得相似 —— 也不应匹配
    out = await engine.match_and_forward(
        {"trigger": "workflow:foo.FORWARD_EXT", "data": {}}
    )
    assert out == []


@pytest.mark.asyncio
async def test_match_workflow_bare_flow_prefix_matches_any_state():
    """Bug T1 相邻路径: workflow:foo（无 STATE 后缀）保留 prefix 通配行为."""
    engine = ForwardEngine()
    engine._rules = [{
        "id": 1, "name": "bare",
        "trigger": "workflow:foo",
        "targets": ["chat1@chatroom"],
        "template": "",
        "enabled": True,
    }]
    for ctx in ("workflow:foo.FORWARD", "workflow:foo.END", "workflow:foo"):
        out = await engine.match_and_forward({"trigger": ctx, "data": {}})
        assert len(out) == 1, f"bare flow should prefix-match ctx={ctx}"
    # 不同 flow name 不应命中
    out = await engine.match_and_forward(
        {"trigger": "workflow:bar.FORWARD", "data": {}}
    )
    assert out == []


@pytest.mark.asyncio
async def test_match_disabled_rule_skipped():
    engine = ForwardEngine()
    engine._rules = [{
        "id": 1, "name": "off",
        "trigger": "workflow:foo.FORWARD",
        "targets": ["chat1@chatroom"],
        "template": "",
        "enabled": False,
    }]
    out = await engine.match_and_forward(
        {"trigger": "workflow:foo.FORWARD", "data": {}}
    )
    assert out == []


# ---------------------------------------------------------------------------
# send_to_targets
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_to_targets_collects_results():
    class FakeSender:
        def __init__(self):
            self.calls = []

        async def send_text(self, msg, receiver, aters=""):  # noqa: ARG002
            self.calls.append((msg, receiver))
            return True

    sender = FakeSender()
    out = await ForwardEngine().send_to_targets(
        [
            {"target": "chat1@chatroom", "rendered_msg": "hello"},
            {"target": "chat2@chatroom", "rendered_msg": "world"},
        ],
        sender,
    )
    assert [r["target"] for r in out] == ["chat1@chatroom", "chat2@chatroom"]
    assert all(r["success"] for r in out)
    assert sender.calls == [("hello", "chat1@chatroom"), ("world", "chat2@chatroom")]


@pytest.mark.asyncio
async def test_send_to_targets_isolates_per_target_exceptions():
    """一个 target 抛异常不影响其余 target 的发送."""

    flaky_done = {"n": 0}

    class FlakySender:
        async def send_text(self, msg, receiver, aters=""):  # noqa: ARG002
            flaky_done["n"] += 1
            if receiver == "bad@chatroom":
                raise RuntimeError("network down")
            return True

    out = await ForwardEngine().send_to_targets(
        [
            {"target": "ok1@chatroom", "rendered_msg": "a"},
            {"target": "bad@chatroom", "rendered_msg": "b"},
            {"target": "ok2@chatroom", "rendered_msg": "c"},
        ],
        FlakySender(),
    )
    assert len(out) == 3
    assert out[0]["success"] is True
    assert out[1]["success"] is False
    assert "network down" in out[1]["error"]
    assert out[2]["success"] is True
    assert flaky_done["n"] == 3  # 三个 target 都尝试过


@pytest.mark.asyncio
async def test_send_to_targets_records_false_when_send_returns_false():
    class LiarSender:
        async def send_text(self, msg, receiver, aters=""):  # noqa: ARG002
            return False

    out = await ForwardEngine().send_to_targets(
        [{"target": "x@chatroom", "rendered_msg": "hi"}], LiarSender()
    )
    assert out[0]["success"] is False
    assert "False" in out[0]["error"]


# ---------------------------------------------------------------------------
# _SafeDict
# ---------------------------------------------------------------------------

def test_safe_dict_missing_key_returns_empty():
    sd = _SafeDict({"present": "ok"})
    assert sd["present"] == "ok"
    assert sd["absent"] == ""  # __missing__


@pytest.mark.asyncio
async def test_render_template_fallback_tolerates_stray_braces():
    """Bug T2 regression: stray '{' in template_name must not crash, in any path."""
    engine = ForwardEngine()
    # 不会抛 ValueError
    rendered = await engine._render_template(  # noqa: SLF001
        template_name="literal { not closed",
        variables={"k": "v"},
    )
    assert isinstance(rendered, str)
