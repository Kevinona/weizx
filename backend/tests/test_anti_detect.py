"""AntiDetect 防检测层单元测试。"""

import time

import pytest

from app.core.anti_detect import AntiDetect, AntiDetectConfig


# -------------------- 工具 --------------------


def _no_delay(*args, **kwargs):
    """替换 random_delay 的桩，避免真实 sleep 拖慢测试。"""
    return None


# -------------------- 策略 1: 全局 ≤ 20 msgs/min --------------------


def test_anti_detect_caps_at_20_messages_per_minute(monkeypatch):
    """AntiDetect.before_send 在 60s 窗口内最多放过 20 条。"""
    monkeypatch.setattr("app.core.anti_detect.random_delay", _no_delay)

    ad = AntiDetect(AntiDetectConfig())

    successes = 0
    # 用不同 session 绕开 per-session cooldown，只验证全局桶
    for i in range(25):
        if ad.before_send(f"wxid_{i}"):
            successes += 1

    assert successes == 20, (
        f"expected 20 messages per minute, got {successes}"
    )


# -------------------- 策略 2: 每会话 30s 冷却 --------------------


def test_anti_detect_enforces_30s_per_session_cooldown(monkeypatch):
    """同一 session 在 30s 内重复发送应被拒绝。"""
    monkeypatch.setattr("app.core.anti_detect.random_delay", _no_delay)

    ad = AntiDetect(AntiDetectConfig())

    # 第一次：对 wxid_a 通过
    assert ad.before_send("wxid_a") is True
    # 第二次（<30s 内）：被锁
    assert ad.before_send("wxid_a") is False

    # 模拟 31s 已过，冷却失效
    ad._rate_limiter.session_cooldowns["wxid_a"] = time.monotonic() - 31
    assert ad.before_send("wxid_a") is True

    # 不同 session 不受影响
    assert ad.before_send("wxid_b") is True


# -------------------- 策略 3: 随机延迟 8-20s (macOS specific) --------------------


def test_anti_detect_default_random_delay_range_is_8_to_20_seconds(
    monkeypatch,
):
    """默认配置下 _apply_random_delay 必须使用 macOS 8-20s 区间。"""
    captured = []
    monkeypatch.setattr(
        "app.core.anti_detect.random_delay",
        lambda mn, mx: captured.append((mn, mx)),
    )

    ad = AntiDetect(AntiDetectConfig())
    ad._apply_random_delay()

    assert len(captured) == 1
    mn, mx = captured[0]
    assert mn == 8.0, f"expected min interval 8.0s, got {mn}"
    assert mx == 20.0, f"expected max interval 20.0s, got {mx}"


@pytest.mark.asyncio
async def test_anti_detect_async_delay_uses_8_to_20_range(monkeypatch):
    """before_send_async 必须 sleep [8, 20]s 之间的一次值。"""
    captured = []

    async def fake_sleep(d):
        captured.append(d)

    monkeypatch.setattr("app.core.anti_detect.asyncio.sleep", fake_sleep)

    ad = AntiDetect(AntiDetectConfig())
    result = await ad.before_send_async("wxid_a")

    assert result is True
    assert len(captured) == 1
    assert 8.0 <= captured[0] <= 20.0


def test_anti_detect_load_from_app_config_respects_macos_block(monkeypatch):
    """_load_from_app_config 必须读取 macos 子块的 min/max_send_interval。"""
    import app.core.anti_detect as ad_mod

    class _FakeCfg:
        anti_detect = {
            "max_messages_per_minute": 20,
            "min_send_interval": 15,  # 通用配置（旧值），应被 macos 覆盖
            "max_send_interval": 45,
            "cooldown_per_session": 30,
            "circuit_breaker_threshold": 3,
            "macos": {
                "min_send_interval": 8.0,
                "max_send_interval": 20.0,
            },
        }

        def get_platform(self):
            return "darwin"

    monkeypatch.setattr(ad_mod, "get_config", lambda: _FakeCfg())

    cfg = AntiDetect._load_from_app_config()
    assert cfg.min_send_interval == 8.0
    assert cfg.max_send_interval == 20.0


# -------------------- 策略 4: 熔断器 3 次失败 → 5min 暂停 --------------------


def test_anti_detect_circuit_breaker_opens_after_3_failures():
    """after_send_failure 调用 3 次后 before_send 必须被拒绝。"""
    ad = AntiDetect(AntiDetectConfig())

    assert ad.is_blocked() is False
    ad.after_send_failure()
    ad.after_send_failure()
    assert ad.is_blocked() is False
    ad.after_send_failure()
    assert ad.is_blocked() is True

    # 开启期间 before_send 必须返回 False
    assert ad.before_send("wxid_a") is False
    assert ad.stats["circuit_open"] is True
    assert ad.stats["circuit_failures"] == 3


def test_anti_detect_circuit_breaker_recovers_after_5min_pause(monkeypatch):
    """熔断开启后 cooldown 过期自动恢复；恢复后能继续发送。"""
    monkeypatch.setattr("app.core.anti_detect.random_delay", _no_delay)

    ad = AntiDetect(AntiDetectConfig())
    ad.after_send_failure()
    ad.after_send_failure()
    ad.after_send_failure()
    assert ad.is_blocked() is True

    # 把 last_failure_time 拨回 301s 之前，模拟 5min 已过
    ad._circuit_breaker.last_failure_time = time.monotonic() - 301

    # is_blocked() 必须自动恢复 (半开 → 关闭)
    assert ad.is_blocked() is False
    assert ad._circuit_breaker.failures == 0
    assert ad._circuit_breaker.open is False

    # 恢复后必须能继续发送
    assert ad.before_send("wxid_a") is True


def test_anti_detect_success_resets_circuit_failure_count(monkeypatch):
    """after_send_success 必须清零熔断失败计数。"""
    ad = AntiDetect(AntiDetectConfig())

    ad.after_send_failure()
    ad.after_send_failure()
    assert ad._circuit_breaker.failures == 2

    ad.after_send_success()
    assert ad._circuit_breaker.failures == 0
    assert ad.is_blocked() is False


def test_anti_detect_blocks_sends_when_circuit_open(monkeypatch):
    """熔断开启期间，before_send 不应再调用 random_delay (避免给阻断路径加延迟)。"""
    sleep_calls = []
    monkeypatch.setattr(
        "app.core.anti_detect.random_delay",
        lambda mn, mx: sleep_calls.append((mn, mx)),
    )

    ad = AntiDetect(AntiDetectConfig())
    ad.after_send_failure()
    ad.after_send_failure()
    ad.after_send_failure()
    sleep_calls.clear()  # 只看阻断后的行为

    assert ad.before_send("wxid_a") is False
    assert sleep_calls == [], "blocked sends must not sleep"
