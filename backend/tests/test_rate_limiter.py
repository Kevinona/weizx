"""RateLimiter / CircuitBreaker / random_delay 单元测试。"""

import time

import pytest

from app.utils.rate_limiter import (
    CircuitBreaker,
    RateLimiter,
    random_delay,
)


# -------------------- RateLimiter --------------------


def test_rate_limiter_global_cap_blocks_21st_message_within_window():
    """全局 token bucket 容量为 20，第 21 次 must_send 在同一窗口内失败。"""
    limiter = RateLimiter(max_per_minute=20)

    successes = 0
    # 不同 session 绕过 per-session cooldown，只验证全局容量
    for i in range(25):
        if limiter.can_send(f"wxid_{i}", cooldown=0):
            successes += 1

    assert successes == 20, (
        f"expected 20 messages within 1 minute window, got {successes}"
    )


def test_rate_limiter_per_session_cooldown_blocks_within_30s():
    """同一 session 第二次发送在 30s 冷却窗口内必须被拒绝。"""
    limiter = RateLimiter(max_per_minute=100)

    assert limiter.can_send("wxid_a", cooldown=30) is True
    # 立即再发 — 仍在 30s 冷却内
    assert limiter.can_send("wxid_a", cooldown=30) is False

    # 把时间模拟推进到 31s 之后，冷却应当失效
    limiter.session_cooldowns["wxid_a"] = time.monotonic() - 31
    assert limiter.can_send("wxid_a", cooldown=30) is True


def test_rate_limiter_session_cooldown_isolates_sessions():
    """session A 的冷却不应影响 session B。"""
    limiter = RateLimiter(max_per_minute=100)

    assert limiter.can_send("wxid_a", cooldown=30) is True
    # 同一时刻 wxid_b 没有冷却记录，应当可以发
    assert limiter.can_send("wxid_b", cooldown=30) is True
    # wxid_a 仍被锁
    assert limiter.can_send("wxid_a", cooldown=30) is False


# -------------------- CircuitBreaker --------------------


def test_circuit_breaker_opens_after_threshold_failures():
    """3 次连续失败后熔断器开启。"""
    cb = CircuitBreaker(threshold=3, cooldown=300)

    cb.record_failure()
    assert cb.is_open() is False
    cb.record_failure()
    assert cb.is_open() is False
    cb.record_failure()
    assert cb.is_open() is True
    assert cb.failures == 3


def test_circuit_breaker_success_resets_failure_count():
    """一次成功必须清空累计失败计数。"""
    cb = CircuitBreaker(threshold=3, cooldown=300)

    cb.record_failure()
    cb.record_failure()
    assert cb.failures == 2

    cb.record_success()
    assert cb.failures == 0
    assert cb.is_open() is False


def test_circuit_breaker_recovers_after_cooldown_elapses():
    """熔断器开启后超过 cooldown 自动恢复 (半开 → 关闭)。"""
    cb = CircuitBreaker(threshold=3, cooldown=10)

    cb.record_failure()
    cb.record_failure()
    cb.record_failure()
    assert cb.is_open() is True

    # 模拟 11s 后再次检查 — 应自动恢复
    cb.last_failure_time = time.monotonic() - 11
    assert cb.is_open() is False
    # 恢复后失败计数被清零
    assert cb.failures == 0


def test_circuit_breaker_stays_open_within_cooldown():
    """cooldown 窗口内即使再调用 is_open() 也必须仍处于开启状态。"""
    cb = CircuitBreaker(threshold=3, cooldown=300)

    cb.record_failure()
    cb.record_failure()
    cb.record_failure()
    assert cb.is_open() is True
    # 再次检查 — 仍开启，失败计数仍为 3
    assert cb.is_open() is True
    assert cb.failures == 3


# -------------------- random_delay --------------------


def test_random_delay_sleeps_within_range(monkeypatch):
    """random_delay 必须把 time.sleep 调用一次，秒数落在 [min, max] 区间内。"""
    captured = []

    monkeypatch.setattr(
        "app.utils.rate_limiter.time.sleep", lambda d: captured.append(d)
    )

    random_delay(8.0, 20.0)

    assert len(captured) == 1
    assert 8.0 <= captured[0] <= 20.0


def test_random_delay_handles_equal_bounds(monkeypatch):
    """min == max 时应睡一个确定的秒数。"""
    captured = []
    monkeypatch.setattr(
        "app.utils.rate_limiter.time.sleep", lambda d: captured.append(d)
    )

    random_delay(5.0, 5.0)

    assert captured == [5.0]
