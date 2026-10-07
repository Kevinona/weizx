"""weizx — models.py 单元测试。

不引入真实 langchain_openai 依赖；通过 ``sys.modules`` 桩注入
``ChatOpenAI``，仅验证 ``LLMConfig`` 与 ``create_llm`` 的契约。
"""

import os
import sys
from types import ModuleType, SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# --- mock langchain_openai BEFORE importing app.ai.models ----------------
_fake = ModuleType("langchain_openai")
_captured: dict = {}


class _FakeChatOpenAI:
    def __init__(self, **kwargs):
        _captured.clear()
        _captured.update(kwargs)
        self.kwargs = kwargs


_fake.ChatOpenAI = _FakeChatOpenAI
sys.modules["langchain_openai"] = _fake

# 强制重载 models 模块（避免被缓存的 ChatOpenAI 真实类污染）
from app.ai import models  # noqa: E402
from app.ai.models import (  # noqa: E402
    LLMConfig,
    PROVIDER_BASE_URLS,
    create_llm,
)


def _reset_captured():
    _captured.clear()


# ---------------------------------------------------------------------------
# 1. LLMConfig.__post_init__ 按 provider 填默认 base_url
# ---------------------------------------------------------------------------


def test_llmconfig_post_init_sets_default_base_url_per_provider():
    cfg = LLMConfig(provider="dashscope")
    assert cfg.base_url == PROVIDER_BASE_URLS["dashscope"]

    cfg_deepseek = LLMConfig(provider="deepseek")
    assert cfg_deepseek.base_url == PROVIDER_BASE_URLS["deepseek"]

    # 未知 provider 应当回落到 dashscope 默认
    cfg_unknown = LLMConfig(provider="does-not-exist")  # type: ignore[arg-type]
    assert cfg_unknown.base_url == PROVIDER_BASE_URLS["dashscope"]


# ---------------------------------------------------------------------------
# 2. LLMConfig 保留用户显式传入的 base_url
# ---------------------------------------------------------------------------


def test_llmconfig_keeps_explicit_base_url_overriding_default():
    custom = "https://my-proxy.example.com/v1"
    cfg = LLMConfig(provider="dashscope", base_url=custom)

    assert cfg.base_url == custom


# ---------------------------------------------------------------------------
# 3. create_llm 传递的 kwargs 必须包含 api_key / base_url / model 等
# ---------------------------------------------------------------------------


def test_create_llm_passes_required_kwargs_to_chat_openai():
    _reset_captured()
    cfg = LLMConfig(
        provider="openai",
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
        temperature=0.3,
        max_tokens=512,
    )

    create_llm(cfg)

    assert _captured.get("model") == "gpt-4o-mini"
    assert _captured.get("api_key") == "sk-test"
    assert _captured.get("base_url") == "https://api.openai.com/v1"
    assert _captured.get("temperature") == 0.3
    assert _captured.get("max_tokens") == 512


# ---------------------------------------------------------------------------
# 4. create_llm 在 api_key 为空时不应塞入 api_key / openai_api_key
# ---------------------------------------------------------------------------


def test_create_llm_omits_api_key_when_empty():
    _reset_captured()
    cfg = LLMConfig(provider="openai", api_key="", model="gpt-4o-mini")

    create_llm(cfg)

    # 空 api_key 不应当把空串写到 ChatOpenAI（避免 OpenAI 客户端立即 401）
    assert "api_key" not in _captured
    assert "openai_api_key" not in _captured
    # base_url 仍然按 provider 默认填上
    assert _captured.get("base_url") == PROVIDER_BASE_URLS["openai"]


# ---------------------------------------------------------------------------
# 5. create_llm 让 config.extra 与 kwargs 覆盖默认参数
# ---------------------------------------------------------------------------


def test_create_llm_kwargs_and_extra_override_defaults(monkeypatch):
    _reset_captured()
    cfg = LLMConfig(
        provider="openai",
        api_key="sk-test",
        model="gpt-4o-mini",
        temperature=0.7,
        extra={"timeout": 30},
    )

    # 调用方额外传 temperature = 0.1，应当覆盖 config.temperature
    create_llm(cfg, temperature=0.1)

    assert _captured.get("temperature") == 0.1
    assert _captured.get("timeout") == 30
    assert _captured.get("model") == "gpt-4o-mini"