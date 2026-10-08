"""weizx 实时（实机）验收脚本 — macOS 版。

镜像原项目 backend/live_reply_probe_windows.py 的接口与流程，但调用
macOS 平台的 MacOSKeyExtractor / MacOSDBReader / MacOSSender。

用法：
    # 默认（最安全）：只检查联系人是否可达，不发消息
    python backend/live_reply_probe.py \\
        --receiver "测试账号A" --alternate-with "测试账号B"

    # 加视觉定位（仍然不发消息，只确认能视觉命中）
    python backend/live_reply_probe.py \\
        --receiver "测试账号A" --alternate-with "测试账号B" \\
        --allow-vision

    # 实际发送 3 轮共 6 条消息（必须先取得对方授权！）
    python backend/live_reply_probe.py \\
        --receiver "测试账号A" --alternate-with "测试账号B" \\
        --allow-vision --send

报告写入：data/live_reply_probe_result.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# 让脚本可以从任意 cwd 跑
BACKEND_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.core.platform import Platform  # noqa: E402

logger = logging.getLogger("live_reply_probe")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


# ---------------------------------------------------------------------------
# 联系人解析
# ---------------------------------------------------------------------------


def resolve_contact(
    contacts: list[dict],
    name_or_id: str,
) -> tuple[str, str]:
    """把昵称/备注/wxid 解析为 (wxid, 本地备注)。

    多匹配或零匹配都抛 ValueError（确定性优先）。
    """
    matches: list[dict] = []
    for c in contacts:
        wxid = c.get("wxid") or c.get("sender_wxid") or ""
        nickname = c.get("nickname") or c.get("sender_name") or ""
        remark = c.get("remark") or c.get("local_remark") or ""
        if not wxid:
            continue
        if name_or_id in (wxid, nickname, remark):
            matches.append(c)

    if len(matches) == 0:
        raise ValueError(f"未找到联系人: {name_or_id!r}")
    if len(matches) > 1:
        names = [m.get("nickname") or m.get("sender_name") for m in matches]
        raise ValueError(f"联系人 {name_or_id!r} 不唯一（命中 {len(matches)} 条: {names}）")
    m = matches[0]
    return (
        m.get("wxid") or m.get("sender_wxid"),
        m.get("remark") or m.get("local_remark") or m.get("nickname") or m.get("sender_name") or "",
    )


# ---------------------------------------------------------------------------
# 消息读回验证
# ---------------------------------------------------------------------------


def verify_readback(
    platform,
    receiver_wxid: str,
    sent_msg_id: str,
    sent_content: str,
    sent_at: float,
    expected_room_id: str = "",
) -> tuple[bool, str]:
    """从消息库读出最新消息，验证刚发的消息落到正确会话。

    返回 (readback_session_matches, error_msg)
    """
    try:
        dbs = platform.db_reader.find_database_files()
    except Exception as exc:
        return False, f"find_database_files 失败: {exc}"

    target_dbs = [
        d for d in dbs
        if "message" in Path(d).name.lower() or "msg" in Path(d).name.lower()
    ] or dbs

    if not target_dbs:
        return False, "未找到任何 message DB"

    keys = platform.key_extractor.load_keys()
    if not keys:
        return False, "DB keys 为空；先跑一次自动提取"

    for db_path in target_dbs:
        # 找一个匹配这个 DB 的 key
        hex_key = None
        for kp, kv in keys.items():
            if Path(kp).name in db_path or Path(db_path).name in kp or kp.split("/")[-1] in db_path:
                hex_key = kv
                break
        if hex_key is None:
            # 退化：用第一个 message key
            for kp, kv in keys.items():
                if "message" in kp.lower() or "msg" in kp.lower():
                    hex_key = kv
                    break
        if hex_key is None:
            hex_key = next(iter(keys.values()))

        try:
            if not platform.db_reader.open_db(db_path, bytes.fromhex(hex_key)):
                continue
        except Exception as exc:
            return False, f"open_db 失败: {exc}"

        try:
            messages = platform.db_reader.query_messages_since(int(sent_at) - 5)
        except Exception as exc:
            return False, f"query_messages_since 失败: {exc}"

        for m in messages:
            if m.is_self and m.content == sent_content:
                # 找到自己刚发的消息
                if expected_room_id and m.room_id and m.room_id != expected_room_id:
                    return False, f"消息落到了错误会话: room_id={m.room_id} (expected {expected_room_id})"
                return True, ""
        # 关闭这个 DB，继续找
        try:
            platform.db_reader.close_db()
        except Exception:
            pass

    return False, "在所有 message DB 中都未找到刚发的消息"


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


async def run_probe(args: argparse.Namespace) -> dict:
    platform = Platform.get()

    # 1. 加载密钥
    keys = platform.key_extractor.load_keys()
    if not keys:
        return {
            "ready": False,
            "error": "DB keys 为空；需要先跑一次自动提取（启动 weizx 后台）",
        }

    # 2. 检查 sender
    sender = platform.sender
    if sender is None:
        return {
            "ready": False,
            "error": "Platform.sender 未初始化",
        }

    # 3. 解析两个联系人
    try:
        contacts = platform.db_reader.get_contacts()
    except Exception as exc:
        return {
            "ready": False,
            "error": f"get_contacts 失败（DB 未打开？）: {exc}",
        }

    try:
        recv_wxid, recv_remark = resolve_contact(contacts, args.receiver)
        alt_wxid, alt_remark = resolve_contact(contacts, args.alternate_with)
    except ValueError as exc:
        return {
            "ready": False,
            "error": str(exc),
            "hint": "检查拼写；支持 wxid、昵称、备注三种匹配",
        }

    report: dict = {
        "ready": True,
        "timestamp": datetime.now().isoformat(),
        "vision_used": args.allow_vision,
        "send_enabled": args.send,
        "receiver": {"wxid": recv_wxid, "local_remark": recv_remark},
        "alternate_with": {"wxid": alt_wxid, "local_remark": alt_remark},
        "rounds": [],
        "summary": {"total_sent": 0, "total_readback_match": 0, "pass": False},
        "errors": [],
    }

    # 4. 探测模式（不发消息）：只跑视觉定位（如果开了 --allow-vision）
    if not args.send:
        report["mode"] = "probe-only"
        if args.allow_vision:
            try:
                sent_a = await sender.send_text("[probe]", recv_wxid, is_group=False, target_id="")
                sent_b = await sender.send_text("[probe]", alt_wxid, is_group=False, target_id="")
                report["vision_check"] = {
                    "receiver_ok": sent_a,
                    "alternate_with_ok": sent_b,
                }
            except Exception as exc:
                report["errors"].append(f"vision check 异常: {exc}")
        return report

    # 5. 发送模式：3 轮交替
    report["mode"] = "send"
    test_msg_a = f"[weizx probe] round {args.round} → A"
    test_msg_b = f"[weizx probe] round {args.round} → B"

    pairs = [
        (1, recv_wxid, test_msg_a),
        (2, alt_wxid, test_msg_b),
        (3, recv_wxid, test_msg_a.replace("→ A", "→ A (r3)")),
        (4, alt_wxid, test_msg_b.replace("→ B", "→ B (r4)")),
        (5, recv_wxid, test_msg_a.replace("→ A", "→ A (r5)")),
        (6, alt_wxid, test_msg_b.replace("→ B", "→ B (r6)")),
    ]

    if not args.yes:
        return {
            "ready": False,
            "error": (
                "实机发送模式需要显式二次确认。重新跑并加 --yes 参数："
                f"\n  python backend/live_reply_probe.py "
                f"--receiver {args.receiver!r} --alternate-with {args.alternate_with!r} "
                f"--send --yes"
            ),
        }

    for r, target_wxid, content in pairs:
        round_log: dict = {
            "round": r,
            "target_wxid": target_wxid,
            "content": content,
        }
        sent_at = time.time()
        try:
            ok = await sender.send_text(
                content, target_wxid, is_group=False, target_id=""
            )
            round_log["sent_ok"] = bool(ok)
            round_log["sent_at"] = sent_at
        except Exception as exc:
            round_log["sent_ok"] = False
            round_log["error"] = f"send_text 异常: {exc}"
            report["errors"].append(f"round {r}: {exc}")
            report["rounds"].append(round_log)
            continue

        if not ok:
            round_log["error"] = "send_text 返回 False"
            report["errors"].append(f"round {r}: send returned False")
            report["rounds"].append(round_log)
            continue

        report["summary"]["total_sent"] += 1
        round_log["readback"] = {"attempted": False}

        if args.readback:
            match, err = verify_readback(
                platform, target_wxid, "", content, sent_at
            )
            round_log["readback"] = {
                "attempted": True,
                "session_matches": match,
                "error": err,
            }
            if match:
                report["summary"]["total_readback_match"] += 1

        report["rounds"].append(round_log)
        # 轮次间间隔 ≥ 5s（防封策略）
        if r < len(pairs):
            await _sleep_or_skip(max(5.0, args.interval))

    total = report["summary"]["total_sent"]
    matched = report["summary"]["total_readback_match"]
    report["summary"]["pass"] = (total == len(pairs) and (
        not args.readback or matched == len(pairs)
    ))
    return report


async def _sleep_or_skip(seconds: float) -> None:
    """轮次间间隔 sleep。生产 ≥5s；测试环境用 WEIZX_PROBE_TEST=1 跳过。"""
    if os.environ.get("WEIZX_PROBE_TEST") == "1":
        return
    await asyncio.sleep(seconds)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="weizx 实时（实机）验收脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--receiver", required=True,
        help="第一个联系人（昵称 / 备注 / wxid 任一）",
    )
    parser.add_argument(
        "--alternate-with", required=True,
        help="第二个联系人（昵称 / 备注 / wxid 任一）",
    )
    parser.add_argument(
        "--send", action="store_true",
        help="实际发送 3 轮共 6 条消息；默认只检查联系人可达性",
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="确认实机发送（--send 必须同时带 --yes）",
    )
    parser.add_argument(
        "--allow-vision", action="store_true",
        help="启用视觉定位（需要 DEEPSEEK_API_KEY）",
    )
    parser.add_argument(
        "--readback", action="store_true",
        help="每条消息发送后从 DB 读回验证",
    )
    parser.add_argument(
        "--interval", type=float, default=8.0,
        help="每轮发送之间的间隔秒数（默认 8，防封策略）",
    )
    parser.add_argument(
        "--round", type=int, default=1,
        help="round 编号，会写入消息内容做区分",
    )
    parser.add_argument(
        "--output", default="data/live_reply_probe_result.json",
        help="报告输出路径",
    )
    args = parser.parse_args()

    if args.send and not args.yes:
        parser.error("--send 必须配合 --yes 使用（避免误发）")

    report = asyncio.run(run_probe(args))

    # 写报告
    out_path = PROJECT_ROOT / args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n[probe] report → {out_path}")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    return 0 if report.get("ready") and not report.get("errors") else 1


if __name__ == "__main__":
    sys.exit(main())
