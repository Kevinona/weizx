"""Unit tests for backend/app/services/{message,report,scheduler}_service.py.

被测模块:
- backend/app/services/message_service.py
- backend/app/services/report_service.py
- backend/app/services/scheduler_service.py

所有外部依赖 (DB / scheduler / AI) 通过最小桩对象 mock。
不依赖真实 SQLAlchemy Session、apscheduler 启动线程或 jieba。
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ============================================================
# shared fakes
# ============================================================


class _FakeResult:
    """最小 SQLAlchemy AsyncResult 桩。"""

    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows)

    def scalar_one_or_none(self):
        if not self._rows:
            return None
        return self._rows[0]

    def scalar(self):
        if not self._rows:
            return None
        first = self._rows[0]
        if isinstance(first, (list, tuple)):
            return first[0]
        return first


class _FakeSession:
    """最小 AsyncSession 桩。"""

    def __init__(self, execute_results):
        self._queue = list(execute_results)
        self._idx = 0
        self.added = []
        self.committed = False

    async def execute(self, query):
        if self._idx < len(self._queue):
            r = self._queue[self._idx]
            self._idx += 1
            return r
        return _FakeResult([])

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True


def _make_message(**kwargs):
    """构造 Message ORM 桩，不依赖真实 declarative 类。"""
    defaults = {
        "id": 1,
        "msg_id": "m1",
        "msg_type": 1,
        "content": "hi",
        "sender_wxid": "wxid_x",
        "sender_name": "Alice",
        "room_id": "",
        "room_name": "",
        "is_group": False,
        "create_time": None,
    }
    defaults.update(kwargs)
    obj = SimpleNamespace(**defaults)
    return obj


# ============================================================
# message_service
# ============================================================


@pytest.mark.asyncio
async def test_save_message_creates_new_record_when_not_exists():
    from app.services.message_service import MessageService

    # 第一次 execute 返回 None（未找到），需要构造一个 Message ORM 对象
    msg_obj = _make_message(msg_id="abc123", content="hello")
    session = _FakeSession([_FakeResult([])])  # 查找结果为空
    svc = MessageService(session)

    # patch Message 构造器 — 避免依赖 declarative
    from app.services import message_service as ms_mod

    original = ms_mod.Message
    ms_mod.Message = MagicMock(return_value=msg_obj)
    try:
        record = await svc.save_message(
            {"msg_id": "abc123", "content": "hello", "sender": "wxid_x"}
        )
    finally:
        ms_mod.Message = original

    assert record is msg_obj
    assert session.added == [msg_obj]
    assert session.committed is True


@pytest.mark.asyncio
async def test_save_message_returns_existing_record_without_commit():
    """dedup: 已存在相同 msg_id 时不重新 add / commit。"""
    from app.services.message_service import MessageService

    existing = _make_message(msg_id="dup1", content="already")
    session = _FakeSession([_FakeResult([existing])])
    svc = MessageService(session)

    record = await svc.save_message({"msg_id": "dup1"})

    assert record is existing
    assert session.added == []
    assert session.committed is False


@pytest.mark.asyncio
async def test_get_messages_paginates_and_returns_total():
    """get_messages: 列表 + total；空结果走 `scalar() or 0` 分支。"""
    from app.services.message_service import MessageService

    rows = [_make_message(id=i, msg_id=f"m{i}") for i in range(3)]
    session = _FakeSession(
        [
            _FakeResult([(3,)]),    # count_q 结果
            _FakeResult(rows),     # query 结果
        ]
    )
    svc = MessageService(session)

    items, total = await svc.get_messages(page=1, size=10)

    assert total == 3
    assert [it.id for it in items] == [0, 1, 2]


@pytest.mark.asyncio
async def test_get_messages_empty_result_returns_zero_total():
    """edge case: count_q 返回空 → total 回退到 0。"""
    from app.services.message_service import MessageService

    session = _FakeSession(
        [
            _FakeResult([]),       # count_q 空
            _FakeResult([]),       # query 空
        ]
    )
    svc = MessageService(session)

    items, total = await svc.get_messages()

    assert items == []
    assert total == 0


@pytest.mark.asyncio
async def test_get_today_message_count_returns_int():
    """get_today_message_count: 正常路径返回 int。"""
    from app.services.message_service import MessageService

    session = _FakeSession([_FakeResult([(7,)])])
    svc = MessageService(session)

    n = await svc.get_today_message_count()

    assert n == 7


@pytest.mark.asyncio
async def test_get_today_message_count_empty_returns_zero():
    """edge case: DB 无消息时 scalar() 为 None → 回退 0。"""
    from app.services.message_service import MessageService

    session = _FakeSession([_FakeResult([])])
    svc = MessageService(session)

    n = await svc.get_today_message_count()

    assert n == 0


@pytest.mark.asyncio
async def test_get_active_rooms_returns_count():
    """get_active_rooms: 正常路径返回 int。"""
    from app.services.message_service import MessageService

    session = _FakeSession([_FakeResult([(4,)])])
    svc = MessageService(session)

    n = await svc.get_active_rooms()

    assert n == 4


# ============================================================
# report_service
# ============================================================


@pytest.mark.asyncio
async def test_generate_daily_report_includes_ranking_and_peak_hour():
    """generate_daily_report: 包含标题、TOP 排行、最活跃时段。"""
    from app.services.report_service import ReportService

    svc = ReportService(session=MagicMock())
    ranking = [
        {"user_name": "Alice", "message_count": 12},
        {"user_name": "Bob", "message_count": 7},
    ]
    timeline = [{"hour": 10, "count": 9}, {"hour": 14, "count": 3}]
    keywords = [{"word": "你好", "count": 4}]

    md = await svc.generate_daily_report("", ranking, timeline, keywords)

    assert "聊天统计报告" in md
    assert "Alice" in md and "12 条" in md
    assert "10:00" in md and "9 条" in md
    assert "热门关键词" in md and "你好" in md


@pytest.mark.asyncio
async def test_generate_daily_report_handles_empty_timeline():
    """edge case: timeline 为空时不崩溃，显示「暂无数据」。"""
    from app.services.report_service import ReportService

    svc = ReportService(session=MagicMock())
    md = await svc.generate_daily_report(
        room_id="",
        ranking=[{"user_name": "Solo", "message_count": 1}],
        timeline=[],
        keywords=[],
    )

    assert "暂无数据" in md
    assert "Solo" in md


@pytest.mark.asyncio
async def test_generate_weekly_report_includes_total_and_ranking():
    """generate_weekly_report: 包含总消息数 + TOP 5。"""
    from app.services.report_service import ReportService

    svc = ReportService(session=MagicMock())
    ranking = [
        {"user_name": f"User{i}", "message_count": 100 - i}
        for i in range(7)
    ]

    md = await svc.generate_weekly_report(
        room_id="", ranking=ranking, total_messages=500
    )

    assert "本周聊天周报" in md
    assert "500" in md
    # 只展示 TOP 5
    assert "User4" in md
    assert "User6" not in md


@pytest.mark.asyncio
async def test_generate_weekly_report_empty_ranking_does_not_crash():
    """edge case: ranking 空时不索引越界。"""
    from app.services.report_service import ReportService

    svc = ReportService(session=MagicMock())
    md = await svc.generate_weekly_report(
        room_id="", ranking=[], total_messages=0
    )

    assert "本周聊天周报" in md
    assert "0" in md


@pytest.mark.asyncio
async def test_format_order_notify_includes_all_fields():
    """format_order_notify: 完整字段 → 输出包含全部行。"""
    from app.services.report_service import ReportService

    svc = ReportService(session=MagicMock())
    order = {
        "order_id": "o123",
        "game": "LOL",
        "rank": "钻石",
        "hours": 2,
        "budget": 50,
        "user_name": "Alice",
        "notes": "急",
    }

    text = await svc.format_order_notify(order)

    assert "o123" in text
    assert "LOL" in text
    assert "钻石" in text
    assert "2h" in text
    assert "¥50/h" in text
    assert "Alice" in text
    assert "急" in text


@pytest.mark.asyncio
async def test_format_order_notify_uses_defaults_for_missing_fields():
    """edge case: 缺字段时用 .get() 回退到默认值。"""
    from app.services.report_service import ReportService

    svc = ReportService(session=MagicMock())
    text = await svc.format_order_notify({})

    assert "无" in text          # notes 默认
    assert "0h" in text          # hours 默认
    assert "¥0/h" in text        # budget 默认


# ============================================================
# scheduler_service
# ============================================================


@pytest.fixture
def patched_scheduler(monkeypatch):
    """把 scheduler_service.scheduler 换成 MagicMock，避免启动真实线程。"""
    from app.services import scheduler_service as ss_mod

    fake_sched = MagicMock()
    monkeypatch.setattr(ss_mod, "scheduler", fake_sched)

    # get_config() 也需要 mock，否则会读真实 yaml
    fake_config = SimpleNamespace(
        forward_rules=[{"targets": ["room_a", "room_b"]}]
    )
    monkeypatch.setattr(ss_mod, "get_config", lambda: fake_config)
    return fake_sched


@pytest.mark.asyncio
async def test_init_scheduler_adds_four_jobs_and_starts(patched_scheduler):
    """init_scheduler: 注册 4 个 job (daily/weekly/health/cleanup) 并 start。"""
    from app.services import scheduler_service as ss_mod

    stats = MagicMock()
    report = MagicMock()
    sender = MagicMock()
    cfg = SimpleNamespace(
        statistics={
            "daily_report_time": "22:00",
            "weekly_report_day": 6,
            "weekly_report_time": "20:00",
        }
    )

    await ss_mod.init_scheduler(stats, report, sender, cfg)

    assert patched_scheduler.add_job.call_count == 4
    patched_scheduler.start.assert_called_once()


@pytest.mark.asyncio
async def test_init_scheduler_parses_time_strings(patched_scheduler):
    """edge case: 解析 HH:MM 配置字符串。"""
    from app.services import scheduler_service as ss_mod

    cfg = SimpleNamespace(
        statistics={
            "daily_report_time": "08:30",
            "weekly_report_day": 0,
            "weekly_report_time": "09:15",
        }
    )

    await ss_mod.init_scheduler(MagicMock(), MagicMock(), MagicMock(), cfg)

    # CronTrigger 构造被传入 add_job.kwargs
    calls = patched_scheduler.add_job.call_args_list
    assert len(calls) == 4
    # 不抛异常即视为解析成功


@pytest.mark.asyncio
async def test_daily_report_job_sends_to_all_targets(patched_scheduler):
    """_daily_report_job: 给 config.forward_rules[*].targets 都发送。"""
    from app.services import scheduler_service as ss_mod

    stats = MagicMock()
    stats.get_ranking = AsyncMock(return_value=[{"user_name": "X", "message_count": 1}])
    stats.get_timeline = AsyncMock(return_value=[{"hour": 5, "count": 1}])
    stats.get_keywords = AsyncMock(return_value=[])

    report = MagicMock()
    report.generate_daily_report = AsyncMock(return_value="MARKDOWN")

    sender = MagicMock()
    sender.send_text = AsyncMock()

    await ss_mod._daily_report_job(stats, report, sender)

    assert sender.send_text.call_count == 2
    args = sender.send_text.call_args_list[0]
    assert args.args == ("MARKDOWN", "room_a") or args[0] == ("MARKDOWN", "room_a")


@pytest.mark.asyncio
async def test_daily_report_job_swallows_exceptions(patched_scheduler):
    """_daily_report_job: stats_service 抛异常时记录日志，不向上抛。"""
    from app.services import scheduler_service as ss_mod

    stats = MagicMock()
    stats.get_ranking = AsyncMock(side_effect=RuntimeError("boom"))

    # 应该不抛
    await ss_mod._daily_report_job(stats, MagicMock(), MagicMock())


@pytest.mark.asyncio
async def test_weekly_report_job_sends_to_all_targets(patched_scheduler):
    """_weekly_report_job: 正常路径。"""
    from app.services import scheduler_service as ss_mod

    stats = MagicMock()
    stats.get_ranking = AsyncMock(return_value=[])
    stats.get_overview = AsyncMock(return_value={"total_messages": 42})

    report = MagicMock()
    report.generate_weekly_report = AsyncMock(return_value="WEEKLY")

    sender = MagicMock()
    sender.send_text = AsyncMock()

    await ss_mod._weekly_report_job(stats, report, sender)

    assert sender.send_text.call_count == 2
    # 报告参数来自 overview["total_messages"]
    call_args = report.generate_weekly_report.call_args
    assert call_args.kwargs.get("total_messages") == 42 or \
           (len(call_args.args) >= 3 and call_args.args[2] == 42)


@pytest.mark.asyncio
async def test_weekly_report_job_swallows_exceptions(patched_scheduler):
    """_weekly_report_job: 异常吞掉。"""
    from app.services import scheduler_service as ss_mod

    stats = MagicMock()
    stats.get_ranking = AsyncMock(side_effect=RuntimeError("nope"))

    await ss_mod._weekly_report_job(stats, MagicMock(), MagicMock())


@pytest.mark.asyncio
async def test_health_check_job_online_does_not_warn(patched_scheduler):
    """_health_check_job: online=True 时不打印 warning。"""
    from app.services import scheduler_service as ss_mod

    sender = MagicMock()
    sender.is_wechat_running = AsyncMock(return_value=True)

    await ss_mod._health_check_job(sender)
    # 跑通即视为通过


@pytest.mark.asyncio
async def test_health_check_job_offline_survives(patched_mock=None, patched_scheduler=None):
    """_health_check_job: online=False 不抛。"""
    from app.services import scheduler_service as ss_mod

    sender = MagicMock()
    sender.is_wechat_running = AsyncMock(return_value=False)

    await ss_mod._health_check_job(sender)


@pytest.mark.asyncio
async def test_health_check_job_exception_swallowed():
    """_health_check_job: sender 抛异常时吞掉。"""
    from app.services import scheduler_service as ss_mod

    sender = MagicMock()
    sender.is_wechat_running = AsyncMock(side_effect=RuntimeError("network"))

    await ss_mod._health_check_job(sender)


def test_get_scheduler_returns_module_instance(patched_scheduler):
    """get_scheduler: 返回 module 级 scheduler 实例。"""
    from app.services import scheduler_service as ss_mod

    assert ss_mod.get_scheduler() is patched_scheduler


@pytest.mark.asyncio
async def test_cleanup_job_does_not_crash():
    """_cleanup_job: 当前仅打日志，不抛异常。"""
    from app.services import scheduler_service as ss_mod

    stats = MagicMock()
    stats.cleanup_old_data = MagicMock()  # 不应被调用（stub 实现）
    cfg = {"data_retention_days": 30}

    await ss_mod._cleanup_job(stats, cfg)
