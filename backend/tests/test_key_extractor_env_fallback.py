"""weizx — key_extractor_macos.load_keys env var fallback 测试。

覆盖点：
- 没有 cache、没有 env var → 返回空
- 有 env var → 解析为 dict[db_label, hex_key]
- env var 值非 hex 字符不校验（仍写入 cache，但 key 解析时会失败）
- 有 cache 时 env var 被忽略（cache 优先）
- env var 写入 cache 后下次启动可直接用
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.core import key_extractor_macos  # noqa: E402
from app.core.key_extractor_macos import MacOSKeyExtractor  # noqa: E402


@pytest.fixture
def empty_data_dir(tmp_path, monkeypatch):
    """每个测试用独立 tmp_path 作为 data dir，避免污染全局 cache。"""
    monkeypatch.setattr(key_extractor_macos, "get_data_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """每个测试前清掉相关的 env var。"""
    for k in ("WEIZX_WECHAT_DB_KEY", "WEIZX_WECHAT_CONTACT_DB_KEY"):
        monkeypatch.delenv(k, raising=False)


# ---------------------------------------------------------------------------
# Cache 不存在 + env 缺失
# ---------------------------------------------------------------------------


def test_load_keys_returns_empty_when_nothing_configured(empty_data_dir):
    ext = MacOSKeyExtractor.__new__(MacOSKeyExtractor)
    ext.ALL_KEYS_FILE = empty_data_dir / "all_keys.json"
    ext._keys = {}
    result = ext.load_keys()
    assert result == {}


# ---------------------------------------------------------------------------
# Env var fallback
# ---------------------------------------------------------------------------


def test_load_keys_falls_back_to_env_vars(empty_data_dir, monkeypatch):
    monkeypatch.setenv("WEIZX_WECHAT_DB_KEY", "a" * 64)
    monkeypatch.setenv("WEIZX_WECHAT_CONTACT_DB_KEY", "b" * 64)

    ext = MacOSKeyExtractor.__new__(MacOSKeyExtractor)
    ext.ALL_KEYS_FILE = empty_data_dir / "all_keys.json"
    ext._keys = {}

    result = ext.load_keys()
    assert "message_0.db" in result
    assert "contact.db" in result
    assert result["message_0.db"] == "a" * 64
    assert result["contact.db"] == "b" * 64


def test_load_keys_env_fallback_persists_to_cache(empty_data_dir, monkeypatch):
    monkeypatch.setenv("WEIZX_WECHAT_DB_KEY", "c" * 64)

    ext = MacOSKeyExtractor.__new__(MacOSKeyExtractor)
    ext.ALL_KEYS_FILE = empty_data_dir / "all_keys.json"
    ext._keys = {}

    ext.load_keys()
    # cache 文件被自动写入
    assert ext.ALL_KEYS_FILE.exists()
    cached = json.loads(ext.ALL_KEYS_FILE.read_text(encoding="utf-8"))
    assert cached == {"message_0.db": "c" * 64}


def test_load_keys_only_one_env_var_works(empty_data_dir, monkeypatch):
    """只设 message key，不设 contact key：后者不出现在结果里。"""
    monkeypatch.setenv("WEIZX_WECHAT_DB_KEY", "x" * 64)
    # WEIZX_WECHAT_CONTACT_DB_KEY 不设

    ext = MacOSKeyExtractor.__new__(MacOSKeyExtractor)
    ext.ALL_KEYS_FILE = empty_data_dir / "all_keys.json"
    ext._keys = {}

    result = ext.load_keys()
    assert "message_0.db" in result
    assert "contact.db" not in result


def test_load_keys_empty_env_value_ignored(empty_data_dir, monkeypatch):
    """env var 设了但值为空 → 视为没设。"""
    monkeypatch.setenv("WEIZX_WECHAT_DB_KEY", "")
    monkeypatch.setenv("WEIZX_WECHAT_CONTACT_DB_KEY", "   ")

    ext = MacOSKeyExtractor.__new__(MacOSKeyExtractor)
    ext.ALL_KEYS_FILE = empty_data_dir / "all_keys.json"
    ext._keys = {}

    result = ext.load_keys()
    assert result == {}


# ---------------------------------------------------------------------------
# Cache 优先
# ---------------------------------------------------------------------------


def test_cache_takes_precedence_over_env(empty_data_dir, monkeypatch):
    """即使 env var 设了，cache 文件存在就用 cache。"""
    cache_data = {"message_0.db": "from_cache" + "0" * 53}
    empty_data_dir.joinpath("all_keys.json").write_text(
        json.dumps(cache_data), encoding="utf-8"
    )
    monkeypatch.setenv("WEIZX_WECHAT_DB_KEY", "x" * 64)

    ext = MacOSKeyExtractor.__new__(MacOSKeyExtractor)
    ext.ALL_KEYS_FILE = empty_data_dir / "all_keys.json"
    ext._keys = {}

    result = ext.load_keys()
    # 用 cache 的值（"from_cache0..."），不用 env
    assert result["message_0.db"].startswith("from_cache")
    assert result["message_0.db"] != "x" * 64
