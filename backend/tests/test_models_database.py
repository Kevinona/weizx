"""weizx — models/database.py 测试。

覆盖点：
- 每个 ORM model 都能在 SQLite in-memory 模式下创建表
- 默认值生效
- 主键 / 唯一约束 / 索引按预期
- Bug 修复回归：Message.create_time 有 index，ForwardRule 有 updated_at
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# 在 import app.models.database 之前 mock SQLAlchemy
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.database import (  # noqa: E402
    Base,
    Message,
    AutoReplyRule,
    MessageTemplate,
    Workflow,
    ForwardRule,
    ChatStatistic,
    Order,
    SystemConfig,
    create_db_engine,
    create_session,
)


@pytest.fixture
def session():
    """每个测试用独立的 in-memory SQLite session。"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()
    engine.dispose()


# ---------------------------------------------------------------------------
# Bug MD9 回归: Message.create_time 索引存在
# ---------------------------------------------------------------------------


def test_message_create_time_has_index():
    """Bug 修复回归：Message.create_time 必须有索引（被 date-range 查询高频使用）。"""
    table = Message.__table__
    indexed_cols = [col.name for col in table.columns if col.index]
    assert "create_time" in indexed_cols, (
        f"Message.create_time 应该有索引；当前索引列: {indexed_cols}"
    )


def test_message_create_time_is_datetime_column():
    assert Message.__table__.columns["create_time"].type.__class__.__name__ == "DateTime"


def test_message_no_vestigial_created_at():
    """Bug 修复：删除无用的 created_at 字段（之前和 create_time 重复）。"""
    assert "created_at" not in Message.__table__.columns


# ---------------------------------------------------------------------------
# Bug MD4 回归: ForwardRule.updated_at 存在
# ---------------------------------------------------------------------------


def test_forward_rule_has_updated_at():
    """Bug 修复回归：ForwardRule 之前缺 updated_at，破坏了与其他模型的一致性。"""
    assert "updated_at" in ForwardRule.__table__.columns


def test_forward_rule_consistency_with_siblings():
    """一致性检查：AutoReplyRule / MessageTemplate / Workflow / ForwardRule 都该有 updated_at。"""
    for model in (AutoReplyRule, MessageTemplate, Workflow, ForwardRule):
        assert "updated_at" in model.__table__.columns, (
            f"{model.__name__} 缺 updated_at 字段"
        )


# ---------------------------------------------------------------------------
# Model 创建 / 主键 / 唯一约束
# ---------------------------------------------------------------------------


def test_all_models_create_tables_without_error():
    """8 个 model 都能在 in-memory SQLite 中建表。"""
    engine = create_db_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        # 验证表都在
        for model in (
            Message, AutoReplyRule, MessageTemplate, Workflow,
            ForwardRule, ChatStatistic, Order, SystemConfig,
        ):
            assert model.__tablename__ in Base.metadata.tables
    finally:
        engine.dispose()


def test_message_primary_key_is_id(session):
    m = Message(msg_id="x", msg_type=1, content="hi")
    session.add(m)
    session.commit()
    assert m.id is not None
    assert m.id > 0


def test_message_msg_id_unique_constraint(session):
    """msg_id 必须唯一 — 插入同 msg_id 第二次应失败。"""
    from sqlalchemy.exc import IntegrityError

    session.add(Message(msg_id="dup", msg_type=1, content="first"))
    session.commit()
    session.add(Message(msg_id="dup", msg_type=1, content="second"))
    with pytest.raises(IntegrityError):
        session.commit()


# ---------------------------------------------------------------------------
# 默认值
# ---------------------------------------------------------------------------


def test_message_default_create_time_recent(session):
    m = Message(msg_id="x", msg_type=1, content="hi")
    session.add(m)
    session.commit()
    assert m.create_time is not None
    # 默认值是 now，应在最近 10 秒内
    delta = datetime.now() - m.create_time
    assert timedelta(seconds=-10) < delta < timedelta(seconds=10)


def test_message_default_is_group_false(session):
    m = Message(msg_id="x", msg_type=1, content="hi")
    session.add(m)
    session.commit()
    assert m.is_group is False
    assert m.room_id == ""  # default="" per column


def test_auto_reply_rule_default_priority_and_enabled(session):
    r = AutoReplyRule(name="r1", type="keyword", patterns=["hi"], reply="yo")
    session.add(r)
    session.commit()
    assert r.priority == 0
    assert r.enabled is True
    assert r.created_at is not None
    assert r.updated_at is not None


def test_message_template_unique_name(session):
    from sqlalchemy.exc import IntegrityError

    session.add(MessageTemplate(name="t1", type="text", content="c"))
    session.commit()
    session.add(MessageTemplate(name="t1", type="text", content="c2"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_workflow_default_enabled_true(session):
    w = Workflow(name="wf1", states={"S1": {}})
    session.add(w)
    session.commit()
    assert w.enabled is True
    assert w.forward_to == ""


def test_order_default_status_pending(session):
    o = Order(
        order_id="o1", user_wxid="u1", user_name="alice",
        game="lol", rank="gold", hours=1.0, budget=100.0,
    )
    session.add(o)
    session.commit()
    assert o.status == "pending"
    assert o.assignee_wxid == ""


def test_system_config_unique_key(session):
    from sqlalchemy.exc import IntegrityError

    session.add(SystemConfig(key="log_level", value="INFO"))
    session.commit()
    session.add(SystemConfig(key="log_level", value="DEBUG"))
    with pytest.raises(IntegrityError):
        session.commit()


# ---------------------------------------------------------------------------
# JSON 字段
# ---------------------------------------------------------------------------


def test_auto_reply_rule_patterns_as_json(session):
    r = AutoReplyRule(name="r", type="regex", patterns=["^hi$", "hello"], reply="yo")
    session.add(r)
    session.commit()
    fetched = session.query(AutoReplyRule).filter_by(name="r").one()
    assert fetched.patterns == ["^hi$", "hello"]


def test_forward_rule_targets_as_json(session):
    r = ForwardRule(
        name="r", trigger="keyword:hi",
        targets=["g1", "g2", "g3"], template="tpl",
    )
    session.add(r)
    session.commit()
    fetched = session.query(ForwardRule).filter_by(name="r").one()
    assert fetched.targets == ["g1", "g2", "g3"]


def test_workflow_states_as_json(session):
    w = Workflow(
        name="wf",
        states={
            "START": {"on_enter": "hi", "transitions": []},
            "FORM": {"on_enter": "form", "transitions": []},
        },
    )
    session.add(w)
    session.commit()
    fetched = session.query(Workflow).filter_by(name="wf").one()
    assert "START" in fetched.states
    assert fetched.states["FORM"]["on_enter"] == "form"


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def test_create_db_engine_returns_engine():
    engine = create_db_engine("sqlite:///:memory:")
    try:
        assert engine is not None
        # 可以连
        with engine.connect() as conn:
            from sqlalchemy import text
            conn.execute(text("SELECT 1"))
    finally:
        engine.dispose()


def test_create_session_returns_session_factory():
    engine = create_db_engine("sqlite:///:memory:")
    try:
        Session = create_session(engine)
        assert callable(Session)
        with Session() as s:
            from sqlalchemy import text
            s.execute(text("SELECT 1"))
    finally:
        engine.dispose()
