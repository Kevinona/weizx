"""weizx — api/config.py 测试。

覆盖点：
- get_ai_config API key 掩码
- update_ai_config *** 前缀保护
- Bug C7: update_forward_rule 用 exclude_unset（partial update）
- Bug C10: trigger_job 同时支持 sync / async job.func
- get_system_config 默认值兜底
- 异常路径（404 / 500）
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# 必须在 import app.api.config 之前 mock 掉 get_session / verify_token
# 这两个是 FastAPI Depends，跑测试时不要触发实际签名
_fake_session_ctx = MagicMock()
_fake_session_ctx.return_value.__aenter__ = AsyncMock(return_value=SimpleNamespace())
_fake_session_ctx.return_value.__aexit__ = AsyncMock(return_value=None)


# 1. 直接 import 然后 patch router 里的依赖
from app.api import config as api_config  # noqa: E402

from app.models.schemas import (  # noqa: E402
    ForwardRuleCreate, ForwardRuleUpdate, ForwardRuleOut,
)


# ---------------------------------------------------------------------------
# API key 掩码
# ---------------------------------------------------------------------------


def test_get_ai_config_masks_api_key():
    cfg = SimpleNamespace(ai={
        "api_key": "sk-1234567890abcdef",
        "model": "gpt-4o",
        "base_url": "https://api.openai.com",
    })
    with patch.object(api_config, "get_config", return_value=cfg):
        out = api_config.get_ai_config()
    assert out["api_key"] == "***cdef"
    assert out["model"] == "gpt-4o"
    assert out["base_url"] == "https://api.openai.com"


def test_get_ai_config_does_not_mask_short_api_key():
    """key <= 4 chars 不被 mask（避免全部被遮）。"""
    cfg = SimpleNamespace(ai={"api_key": "abc", "model": "gpt-4o"})
    with patch.object(api_config, "get_config", return_value=cfg):
        out = api_config.get_ai_config()
    assert out["api_key"] == "abc"


# ---------------------------------------------------------------------------
# Bug C7: update_forward_rule 用 ForwardRuleUpdate + exclude_unset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_forward_rule_partial_keeps_other_fields():
    """Bug C7: 用 ForwardRuleUpdate 让前端只发改过的字段，避免覆盖其他字段。"""
    rule = SimpleNamespace(
        id=1, name="原名", trigger="原trigger",
        targets=["原group"], template="原template", enabled=True,
    )

    class _FakeResult:
        def scalar_one_or_none(self):
            return rule

    class _FakeSession:
        async def execute(self, *args, **kwargs):
            return _FakeResult()

        async def commit(self):
            pass

        async def refresh(self, _):
            pass

    sess = _FakeSession()

    payload = ForwardRuleUpdate(name="新名")  # 只改 name，其他不动

    await api_config.update_forward_rule(1, payload, session=sess)

    # name 被改
    assert rule.name == "新名"
    # 其他字段保持原值
    assert rule.trigger == "原trigger"
    assert rule.targets == ["原group"]
    assert rule.template == "原template"
    assert rule.enabled is True


@pytest.mark.asyncio
async def test_update_forward_rule_not_found_raises_404():
    class _FakeResult:
        def scalar_one_or_none(self):
            return None

    class _FakeSession:
        async def execute(self, *args, **kwargs):
            return _FakeResult()

    sess = _FakeSession()
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await api_config.update_forward_rule(999, ForwardRuleUpdate(name="x"), session=sess)
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Bug C10: trigger_job 同时支持 sync / async job.func
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_job_handles_sync_func():
    """sync job.func 必须被 wrap 成 coroutine，不能直接传给 create_task。"""

    sync_called = {"n": 0}

    def sync_job():
        sync_called["n"] += 1
        return "sync_result"

    class _FakeScheduler:
        def get_job(self, _id):
            return SimpleNamespace(
                id="job1", name="daily_report", func=sync_job, args=(), kwargs={},
                next_run_time=None, trigger="interval",
            )

    fake_task = SimpleNamespace()

    async def _fake_create_task(coro):
        # 关键：coro 必须是 coroutine（不能是 None / 直接结果）
        import inspect
        assert inspect.iscoroutine(coro)
        # run it to completion
        await coro
        return fake_task

    with patch.object(api_config, "get_scheduler", return_value=_FakeScheduler()), \
         patch("asyncio.create_task", side_effect=_fake_create_task):
        out = await api_config.trigger_job("job1")

    assert out["success"] is True
    assert out["triggered"] is True
    assert sync_called["n"] == 1


@pytest.mark.asyncio
async def test_trigger_job_handles_async_func():
    """async job.func 正常 schedule。"""
    async_called = {"n": 0}

    async def async_job():
        async_called["n"] += 1

    class _FakeScheduler:
        def get_job(self, _id):
            return SimpleNamespace(
                id="job1", name="cleanup", func=async_job, args=(), kwargs={},
                next_run_time=None, trigger="interval",
            )

    async def _fake_create_task(coro):
        await coro

    with patch.object(api_config, "get_scheduler", return_value=_FakeScheduler()), \
         patch("asyncio.create_task", side_effect=_fake_create_task):
        out = await api_config.trigger_job("job1")

    assert out["success"] is True
    assert async_called["n"] == 1


@pytest.mark.asyncio
async def test_trigger_job_not_found_raises_404():
    class _FakeScheduler:
        def get_job(self, _id):
            return None

    from fastapi import HTTPException

    with patch.object(api_config, "get_scheduler", return_value=_FakeScheduler()):
        with pytest.raises(HTTPException) as exc_info:
            await api_config.trigger_job("nope")
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# update_job pause/resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_job_pause_calls_job_pause():
    job = SimpleNamespace(
        id="job1", pause=MagicMock(), resume=MagicMock(),
    )

    class _FakeScheduler:
        def get_job(self, _id):
            return job

    with patch.object(api_config, "get_scheduler", return_value=_FakeScheduler()):
        out = await api_config.update_job("job1", {"paused": True})
    assert out["success"] is True
    job.pause.assert_called_once()
    job.resume.assert_not_called()


@pytest.mark.asyncio
async def test_update_job_resume_calls_job_resume():
    job = SimpleNamespace(
        id="job1", pause=MagicMock(), resume=MagicMock(),
    )

    class _FakeScheduler:
        def get_job(self, _id):
            return job

    with patch.object(api_config, "get_scheduler", return_value=_FakeScheduler()):
        out = await api_config.update_job("job1", {"paused": False})
    job.resume.assert_called_once()
    job.pause.assert_not_called()


# ---------------------------------------------------------------------------
# get_system_config 默认值兜底
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_system_config_fills_defaults_for_missing_keys():
    class _FakeRow:
        def __init__(self, k, v):
            self.key = k
            self.value = v

    class _FakeResult:
        def scalars(self):
            return SimpleNamespace(all=lambda: [_FakeRow("log_level", "DEBUG")])

    class _FakeSession:
        async def execute(self, *args, **kwargs):
            return _FakeResult()

    sess = _FakeSession()
    out = await api_config.get_system_config(session=sess)
    keys = {item["key"] for item in out}
    # defaults 中所有 key 必须出现
    for expected in [
        "system_name", "system_version", "admin_email", "log_level",
        "data_retention_days", "page_size", "alert_enabled", "alert_room_id",
    ]:
        assert expected in keys
    # DB 里的值优先
    log_level = next(i for i in out if i["key"] == "log_level")
    assert log_level["value"] == "DEBUG"


# ---------------------------------------------------------------------------
# update_ai_config *** 前缀保护
# ---------------------------------------------------------------------------


def test_update_ai_config_skips_masked_api_key():
    cfg = SimpleNamespace(ai={"api_key": "real-key-1234", "model": "gpt-4o"})

    # 不动文件系统路径；用 tmp_path 隔开
    with patch.object(api_config, "get_config", return_value=cfg), \
         patch.object(api_config, "_get_config_path", return_value="/nonexistent/x.yaml"):
        # 配置文件路径不存在 → 持久化抛 FileNotFoundError 被 except 包成 HTTPException
        # 但内存里的 cfg.ai 还是要正确处理 ***
        try:
            api_config.update_ai_config({"api_key": "***1234", "model": "gpt-4o-mini"})
        except Exception:
            pass
    # 即使持久化失败，内存里 key 不能被覆盖
    assert cfg.ai["api_key"] == "real-key-1234"
    # model 不以 *** 开头，应该被改
    assert cfg.ai["model"] == "gpt-4o-mini"
