"""weizx — models/schemas.py 测试。

覆盖点：
- 每个 schema 都能正常 instantiate
- max_length 字段在超长时拒绝（DB/String 同源）
- Bug S7 回归：ForwardRuleOut 必须有 updated_at
- LoginRequest / TokenResponse / 各类 Out/Update 形状
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.models.schemas import (  # noqa: E402
    LoginRequest,
    TokenResponse,
    MessageOut,
    MessageListResponse,
    SendMessageRequest,
    RuleCreate,
    RuleOut,
    RuleUpdate,
    TemplateCreate,
    TemplateOut,
    TemplateUpdate,
    WorkflowState,
    WorkflowCreate,
    WorkflowUpdate,
    WorkflowOut,
    ForwardRuleCreate,
    ForwardRuleUpdate,
    ForwardRuleOut,
    RankingItem,
    TimelineItem,
    KeywordItem,
    StatisticsOverview,
    OrderOut,
    SystemConfigItem,
    SystemConfigUpdate,
    DashboardOverview,
)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_login_request_accepts_valid():
    r = LoginRequest(username="alice", password="secret")
    assert r.username == "alice"
    assert r.password == "secret"


def test_login_request_rejects_missing_field():
    with pytest.raises(ValidationError):
        LoginRequest(username="alice")  # type: ignore[call-arg]


def test_token_response_default_type_bearer():
    r = TokenResponse(access_token="abc")
    assert r.token_type == "bearer"


# ---------------------------------------------------------------------------
# Bug S10 回归: max_length 字段
# ---------------------------------------------------------------------------


def test_message_out_msg_id_max_length_64():
    """msg_id 64 char 是 DB String(64) 同源约束。"""
    MessageOut(msg_id="a" * 64, msg_type=1, content="x", sender_wxid="u1")  # 64 OK
    with pytest.raises(ValidationError):
        MessageOut(msg_id="a" * 65, msg_type=1, content="x", sender_wxid="u1")


def test_message_out_sender_wxid_max_length_64():
    MessageOut(
        msg_id="m1", msg_type=1, content="x",
        sender_wxid="u" * 64,
    )  # 64 OK
    with pytest.raises(ValidationError):
        MessageOut(
            msg_id="m1", msg_type=1, content="x",
            sender_wxid="u" * 65,
        )


def test_message_out_optional_fields_have_max_length():
    MessageOut(
        msg_id="m1", msg_type=1, content="x", sender_wxid="u1",
        sender_name="n" * 128,
        room_id="r" * 64,
        room_name="R" * 128,
    )  # all at max OK
    with pytest.raises(ValidationError):
        MessageOut(
            msg_id="m1", msg_type=1, content="x", sender_wxid="u1",
            sender_name="n" * 129,
        )


def test_message_list_response_shape():
    now = datetime.now()
    items = [MessageOut(msg_id="m1", msg_type=1, content="hi", sender_wxid="u1", create_time=now)]
    r = MessageListResponse(items=items, total=1, page=1, size=20)
    assert r.total == 1
    assert len(r.items) == 1
    assert r.items[0].msg_id == "m1"


def test_send_message_request_defaults():
    r = SendMessageRequest(msg="hi", receiver="alice")
    assert r.aters == ""  # default


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def test_rule_create_defaults():
    r = RuleCreate(name="r", patterns=["hi"])
    assert r.type == "keyword"  # default
    assert r.reply == ""
    assert r.workflow == ""
    assert r.priority == 0
    assert r.enabled is True


def test_rule_out_inherits_and_adds():
    now = datetime.now()
    r = RuleOut(
        id=1, name="r", patterns=["hi"], reply="yo",
        created_at=now, updated_at=now,
    )
    assert r.id == 1
    assert r.created_at == now


def test_rule_update_all_optional():
    r = RuleUpdate()
    assert r.name is None
    assert r.type is None
    assert r.patterns is None


def test_rule_update_partial_only_name():
    r = RuleUpdate(name="renamed")
    assert r.name == "renamed"
    # other fields stay None
    assert r.type is None
    assert r.patterns is None


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def test_template_create_defaults():
    t = TemplateCreate(name="t1", content="c")
    assert t.type == "text"
    assert t.title == ""
    assert t.footer == ""


def test_template_update_partial():
    t = TemplateUpdate(title="New")
    assert t.title == "New"
    assert t.name is None


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------


def test_workflow_state_defaults():
    s = WorkflowState(name="S1", on_enter="hi")
    assert s.transitions == []


def test_workflow_create_defaults():
    w = WorkflowCreate(name="wf1")
    assert w.description == ""
    assert w.trigger_intents == []
    assert w.states == []
    assert w.forward_to == ""
    assert w.enabled is True


def test_workflow_create_with_states():
    w = WorkflowCreate(
        name="wf1",
        states=[
            WorkflowState(name="S1", on_enter="hi"),
            WorkflowState(name="S2", on_enter="next"),
        ],
    )
    assert len(w.states) == 2
    assert w.states[0].name == "S1"


def test_workflow_update_partial():
    w = WorkflowUpdate(enabled=False)
    assert w.enabled is False
    assert w.name is None
    assert w.states is None


# ---------------------------------------------------------------------------
# Bug S7 回归: ForwardRuleOut 必须有 updated_at
# ---------------------------------------------------------------------------


def test_forward_rule_out_has_updated_at():
    """Bug S7 修复回归：与 model/ForwardRule 和其他 Out 一致。"""
    now = datetime.now()
    r = ForwardRuleOut(
        id=1, name="r", trigger="keyword:hi",
        targets=["g1"], template="t",
        created_at=now, updated_at=now,
    )
    assert r.updated_at == now
    # 字段在 model_fields 里也存在
    field_names = set(ForwardRuleOut.model_fields.keys())
    assert "updated_at" in field_names


def test_forward_rule_out_created_at_and_updated_at_required():
    """两个时间字段都是必填（非 default）。"""
    with pytest.raises(ValidationError):
        ForwardRuleOut(
            id=1, name="r", trigger="keyword:hi",
            targets=["g1"], template="t",
            created_at=datetime.now(),
            # missing updated_at
        )


def test_forward_rule_update_partial():
    r = ForwardRuleUpdate(enabled=False)
    assert r.enabled is False
    assert r.name is None


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def test_ranking_item_max_length():
    """Bug S10：user_wxid 64 char 上限。"""
    RankingItem(user_wxid="u" * 64, user_name="n", message_count=1)
    with pytest.raises(ValidationError):
        RankingItem(user_wxid="u" * 65, user_name="n", message_count=1)


def test_ranking_item_user_name_max_length():
    RankingItem(user_wxid="u", user_name="n" * 128, message_count=1)
    with pytest.raises(ValidationError):
        RankingItem(user_wxid="u", user_name="n" * 129, message_count=1)


def test_timeline_item_shape():
    t = TimelineItem(hour=14, count=42)
    assert t.hour == 14
    assert t.count == 42


def test_keyword_item_shape():
    k = KeywordItem(word="hello", count=3, score=0.5)
    assert k.word == "hello"
    assert k.score == 0.5


def test_statistics_overview_defaults():
    s = StatisticsOverview(
        total_messages=100, active_users=5, active_rooms=2,
    )
    assert s.ranking == []
    assert s.timeline == []
    assert s.keywords == []


# ---------------------------------------------------------------------------
# Orders (Bug S10)
# ---------------------------------------------------------------------------


def test_order_out_max_lengths():
    OrderOut(
        order_id="o" * 32, user_wxid="u" * 64, user_name="n" * 128,
        game="g" * 64, rank="r" * 64,
        hours=1.0, budget=50.0, status="pending", assignee_name="a" * 128,
        created_at=datetime.now(),
    )
    # 超长 order_id 拒绝
    with pytest.raises(ValidationError):
        OrderOut(
            order_id="o" * 33, user_wxid="u", user_name="n",
            game="g", rank="r",
            hours=1.0, budget=50.0, status="pending", assignee_name="a",
            created_at=datetime.now(),
        )


# ---------------------------------------------------------------------------
# System config / Dashboard
# ---------------------------------------------------------------------------


def test_system_config_item_shape():
    i = SystemConfigItem(key="log_level", value="INFO")
    assert i.key == "log_level"
    assert i.value == "INFO"


def test_system_config_update_with_items():
    u = SystemConfigUpdate(items=[
        SystemConfigItem(key="log_level", value="INFO"),
        SystemConfigItem(key="page_size", value="20"),
    ])
    assert len(u.items) == 2


def test_dashboard_overview_shape():
    d = DashboardOverview(
        platform="darwin", wechat_online=True, today_messages=10,
        active_rooms=2, ai_calls=42, pending_orders=0,
    )
    assert d.platform == "darwin"
    assert d.wechat_online is True
    assert d.ai_calls == 42


# ---------------------------------------------------------------------------
# 通用：datetime 默认值（工厂调用时机）
# ---------------------------------------------------------------------------


def test_message_out_create_time_default_is_nowish():
    m = MessageOut(msg_id="m1", msg_type=1, content="x", sender_wxid="u1")
    delta = datetime.now() - m.create_time
    assert timedelta(seconds=-10) < delta < timedelta(seconds=10)


def test_message_out_create_time_accepts_past_datetime():
    past = datetime(2020, 1, 1, 12, 0, 0)
    m = MessageOut(msg_id="m1", msg_type=1, content="x", sender_wxid="u1", create_time=past)
    assert m.create_time == past
