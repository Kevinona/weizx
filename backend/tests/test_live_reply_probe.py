"""weizx — live_reply_probe.py 测试。

只测脚本的辅助逻辑（resolve_contact、报告结构、CLI 解析）。
不测实机发送（需要真 WeChat + 权限）。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 让 live_reply_probe 的 sleep 在测试里直接 no-op
os.environ.setdefault("WEIZX_PROBE_TEST", "1")

# Make backend importable
BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

# Import the module under test
import live_reply_probe  # noqa: E402
from live_reply_probe import resolve_contact  # noqa: E402


# ---------------------------------------------------------------------------
# resolve_contact
# ---------------------------------------------------------------------------


def test_resolve_contact_by_wxid():
    contacts = [
        {"wxid": "wxid_alice", "nickname": "Alice", "remark": "A"},
        {"wxid": "wxid_bob", "nickname": "Bob", "remark": "B"},
    ]
    wxid, remark = resolve_contact(contacts, "wxid_bob")
    assert wxid == "wxid_bob"
    # remark 优先
    assert remark == "B"


def test_resolve_contact_by_nickname():
    contacts = [{"wxid": "wxid_1", "nickname": "Alice", "remark": ""}]
    wxid, remark = resolve_contact(contacts, "Alice")
    assert wxid == "wxid_1"
    # remark 为空，fallback 到 nickname
    assert remark == "Alice"


def test_resolve_contact_by_remark():
    contacts = [{"wxid": "wxid_1", "nickname": "Alice", "remark": "我的闺蜜"}]
    wxid, remark = resolve_contact(contacts, "我的闺蜜")
    assert wxid == "wxid_1"
    assert remark == "我的闺蜜"


def test_resolve_contact_raises_on_missing():
    contacts = [{"wxid": "wxid_1", "nickname": "Alice", "remark": ""}]
    with pytest.raises(ValueError, match="未找到联系人"):
        resolve_contact(contacts, "Charlie")


def test_resolve_contact_raises_on_ambiguous():
    contacts = [
        {"wxid": "wxid_1", "nickname": "Alice", "remark": ""},
        {"wxid": "wxid_2", "nickname": "Alice", "remark": ""},
    ]
    with pytest.raises(ValueError, match="不唯一"):
        resolve_contact(contacts, "Alice")


def test_resolve_contact_handles_sender_wxid_field_name():
    """DB reader 可能用 sender_wxid 字段名（兼容）。"""
    contacts = [{"sender_wxid": "wxid_x", "sender_name": "X", "local_remark": "xr"}]
    wxid, remark = resolve_contact(contacts, "X")
    assert wxid == "wxid_x"
    assert remark == "xr"


def test_resolve_contact_skips_empty_wxid_entries():
    contacts = [
        {"wxid": "", "nickname": "Alice"},  # 无效：wxid 为空
        {"wxid": "wxid_1", "nickname": "Alice"},  # 唯一有效
    ]
    wxid, _ = resolve_contact(contacts, "Alice")
    assert wxid == "wxid_1"


# ---------------------------------------------------------------------------
# CLI 参数
# ---------------------------------------------------------------------------


def test_cli_requires_receiver_and_alternate_with(monkeypatch):
    """缺关键参数应报错。"""
    with pytest.raises(SystemExit):
        monkeypatch.setattr(sys, "argv", ["live_reply_probe.py"])
        live_reply_probe.main()


def test_cli_send_requires_yes(monkeypatch):
    """--send 单独使用应报错（避免误发）。"""
    with pytest.raises(SystemExit):
        monkeypatch.setattr(
            sys, "argv",
            ["live_reply_probe.py", "--receiver", "A",
             "--alternate-with", "B", "--send"]
        )
        live_reply_probe.main()


# ---------------------------------------------------------------------------
# run_probe 集成行为（用 mock 平台）
# ---------------------------------------------------------------------------


def _mock_platform(receiver_wxid="wxid_recv", alt_wxid="wxid_alt"):
    """构造一个 fake platform，给 resolve_contact / send_text / db_reader 配好。"""
    p = MagicMock()
    p.key_extractor.load_keys.return_value = {"message_0.db": "AB" * 32}
    p.sender = MagicMock()
    p.sender.send_text = AsyncMock(return_value=True)
    p.db_reader.get_contacts.return_value = [
        {"wxid": receiver_wxid, "nickname": "A-recv", "remark": "RecvRem"},
        {"wxid": alt_wxid, "nickname": "B-alt", "remark": "AltRem"},
    ]
    # Platform.get() should return this object directly
    p.get = MagicMock(return_value=p)
    return p


def test_run_probe_no_send_mode():
    """默认模式：只检查联系人可达性，不发消息。"""
    p = _mock_platform()
    with patch.object(live_reply_probe, "Platform", return_value=p) as PlatformMock:
        PlatformMock.get = MagicMock(return_value=p)
        args = SimpleNamespace(
            receiver="A-recv", alternate_with="B-alt",
            send=False, yes=False, allow_vision=False,
            readback=False, interval=8.0, round=1,
        )
        report = asyncio.run(live_reply_probe.run_probe(args))

    assert report["ready"] is True
    assert report["mode"] == "probe-only"
    assert report["receiver"]["wxid"] == "wxid_recv"
    assert report["alternate_with"]["wxid"] == "wxid_alt"
    # 没发消息
    p.sender.send_text.assert_not_called()
    assert report["summary"]["total_sent"] == 0


def test_run_probe_send_requires_yes():
    """--send 不带 --yes 应被拒绝（即使我们已经在 main() 里检查过，run_probe 也守一次）。"""
    p = _mock_platform()
    with patch.object(live_reply_probe, "Platform", return_value=p) as PlatformMock:
        PlatformMock.get = MagicMock(return_value=p)
        args = SimpleNamespace(
            receiver="A-recv", alternate_with="B-alt",
            send=True, yes=False, allow_vision=False,
            readback=False, interval=8.0, round=1,
        )
        report = asyncio.run(live_reply_probe.run_probe(args))

    assert report["ready"] is False
    assert "--yes" in report["error"]


def test_run_probe_no_keys_returns_error():
    """DB keys 缺失时直接返回错误。"""
    p = _mock_platform()
    p.key_extractor.load_keys.return_value = {}  # empty
    with patch.object(live_reply_probe, "Platform", return_value=p) as PlatformMock:
        PlatformMock.get = MagicMock(return_value=p)
        args = SimpleNamespace(
            receiver="A", alternate_with="B",
            send=False, yes=False, allow_vision=False,
            readback=False, interval=8.0, round=1,
        )
        report = asyncio.run(live_reply_probe.run_probe(args))

    assert report["ready"] is False
    assert "DB keys" in report["error"]


def test_run_probe_contact_not_found():
    p = _mock_platform()
    with patch.object(live_reply_probe, "Platform", return_value=p) as PlatformMock:
        PlatformMock.get = MagicMock(return_value=p)
        args = SimpleNamespace(
            receiver="NotARealContact", alternate_with="B-alt",
            send=False, yes=False, allow_vision=False,
            readback=False, interval=8.0, round=1,
        )
        report = asyncio.run(live_reply_probe.run_probe(args))

    assert report["ready"] is False
    assert "未找到" in report["error"]


def test_run_probe_send_mode_no_readback():
    """--send + --yes + 没 --readback：6 条全发，但不读回验证。"""
    p = _mock_platform()
    with patch.object(live_reply_probe, "Platform", return_value=p) as PlatformMock:
        PlatformMock.get = MagicMock(return_value=p)
        args = SimpleNamespace(
            receiver="A-recv", alternate_with="B-alt",
            send=True, yes=True, allow_vision=False,
            readback=False, interval=0.0, round=1,
        )
        report = asyncio.run(live_reply_probe.run_probe(args))

    assert report["ready"] is True
    assert report["mode"] == "send"
    assert len(report["rounds"]) == 6
    # 6 条全 send
    assert p.sender.send_text.call_count == 6
    assert report["summary"]["total_sent"] == 6
    # 没 readback
    assert report["summary"]["total_readback_match"] == 0
    # pass 条件：total_sent == 6 AND no readback requested
    assert report["summary"]["pass"] is True


def test_run_probe_send_mode_with_readback():
    """--readback 启用时：6 条全发，每条都尝试读回；readback 失败时 round 仍计入 send 但 readback_match 不增。"""
    p = _mock_platform()
    # find_database_files 返回空，verify_readback 会快速失败（但计入 readback 尝试）
    p.db_reader.find_database_files.return_value = []
    with patch.object(live_reply_probe, "Platform", return_value=p) as PlatformMock:
        PlatformMock.get = MagicMock(return_value=p)
        args = SimpleNamespace(
            receiver="A-recv", alternate_with="B-alt",
            send=True, yes=True, allow_vision=False,
            readback=True, interval=0.0, round=1,
        )
        report = asyncio.run(live_reply_probe.run_probe(args))

    assert report["ready"] is True
    assert report["mode"] == "send"
    assert p.sender.send_text.call_count == 6
    assert report["summary"]["total_sent"] == 6
    # readback 失败：total_readback_match == 0，pass = False
    assert report["summary"]["total_readback_match"] == 0
    assert report["summary"]["pass"] is False


def test_run_probe_send_failure_records_error():
    """send_text 返回 False 时该 round 计入 errors，但不崩。"""
    p = _mock_platform()
    p.sender.send_text = AsyncMock(side_effect=[True, False, True, True, True, True])
    with patch.object(live_reply_probe, "Platform", return_value=p) as PlatformMock:
        PlatformMock.get = MagicMock(return_value=p)
        args = SimpleNamespace(
            receiver="A-recv", alternate_with="B-alt",
            send=True, yes=True, allow_vision=False,
            readback=False, interval=0.0, round=1,
        )
        report = asyncio.run(live_reply_probe.run_probe(args))

    assert report["summary"]["total_sent"] == 5
    assert len(report["errors"]) == 1
    assert "round 2" in report["errors"][0]
    # pass = False 因为 5 != 6
    assert report["summary"]["pass"] is False


# ---------------------------------------------------------------------------
# 报告可序列化（JSON）
# ---------------------------------------------------------------------------


def test_report_is_json_serializable():
    """报告 dict 必须能 JSON 序列化（无 datetime、None 等）。"""
    p = _mock_platform()
    with patch.object(live_reply_probe, "Platform", return_value=p) as PlatformMock:
        PlatformMock.get = MagicMock(return_value=p)
        args = SimpleNamespace(
            receiver="A-recv", alternate_with="B-alt",
            send=False, yes=False, allow_vision=False,
            readback=False, interval=8.0, round=1,
        )
        report = asyncio.run(live_reply_probe.run_probe(args))

    # 必须能 json.dumps 不报错
    s = json.dumps(report, ensure_ascii=False)
    assert "wxid_recv" in s
    assert "wxid_alt" in s
    assert "RecvRem" in s
    assert "AltRem" in s
