# 实机验收 Runbook — `live_reply_probe.py` (macOS)

> 内部文档，给 Kevin + backend-platform-engineer 看的真机验收步骤。
> 适用：weizx macOS 路径 Phase 1 验收（T1 / T3 / T17），详见 `docs/team-plan.yaml`。
> 上游：AGENTS.md "Live test 守则"。

---

## 1. Pre-conditions

按 AGENTS.md "Live test 守则" 执行，开始前**全部满足** 才往下：

- [ ] **测试账号必须是个人不重要的小号**。封号可接受，不在工作号 / 主号 / 重要联系人群上跑。AGENTS.md 明示 "绝不在生产账号上跑"。
- [ ] **两方联系人显式同意**。`--receiver` 和 `--alternate-with` 都得是真人，对话前必须提前告知「这是 AI 自动回复测试，会发真实消息」。把对方拉进白名单前确认 OK。
- [ ] **单会话间隔 ≥ 5 分钟**。脚本内部若触发频率限制，停止后续 round 等 ≥ 5 分钟再继续。AGENTS.md 明文规定。
- [ ] **macOS 权限已授予**：系统设置 → 隐私与安全性
  - 辅助功能：当前终端（Terminal / iTerm / PyQt 启动器）
  - 屏幕录制：当前终端
- [ ] 微信客户端已登录，目标账号在线（手机 + Mac 同时在线不影响）。
- [ ] `data/all_keys.json` 已存在且不超过 24h；否则先跑 T2 重新提取。
- [ ] `config/config.yaml` 中 `auto_reply.enabled` 临时设 `true`，跑完回 `false`（见 §6）。

---

## 2. Test Script Usage

脚本路径：`backend/live_reply_probe.py`（**T1 待写**，镜像 `live_reply_probe_windows.py` 功能；本节据 T1 规范描述）。**不写完别跑**。

最简示例：

```bash
cd /Users/kevin/Documents/GitHub/weizx
source venv/bin/activate
python backend/live_reply_probe.py \
  --receiver "测试账号A" --alternate-with "测试账号B" \
  --allow-vision --send
```

### Flags

| Flag | 用途 |
|------|------|
| `--receiver` | 主收件人（必填，本地备注或 wxid）。脚本解析为 wxid。 |
| `--alternate-with` | 第二收件人，参与 round 3 的 A→B 交替。 |
| `--send` | 启用真实发送。**省略则仅检查联系人可达性**（默认 safe mode）。 |
| `--allow-vision` | 启用视觉定位（标题识别 / 坐标定位）。需要屏幕录制权限。 |
| `--package-dir` | WeChat `.app` 路径，默认 `/Applications/WeChat.app`。 |
| `--keys` | 密钥 JSON 路径，默认 `data/all_keys.json`。 |

参数命名与 T1 spec 严格对齐（见 `docs/team-plan.yaml` T1）。

---

## 3. Step-by-step

### 默认模式（无 `--send`）：仅探活

```
python backend/live_reply_probe.py --receiver "测试账号A" --alternate-with "测试账号B"
```

- 解析两个 wxid，确认在微信通讯录中可达。
- 写 `data/live_reply_probe_result.json`（含 `pass=false` 的占位 summary）。
- **不发任何消息**。这条只是验通探针。

### `--send` 模式：3 轮 6 条消息

每轮间隔 ≥ 5 分钟（AGENTS.md）。脚本会在 step 间 `time.sleep(300)`，不要中断。

| Round | 发送目标 | 验证 |
|-------|----------|------|
| 1 | A | DB readback：A 会话出现新消息，标题 = A 的备注/wxid |
| 2 | B | DB readback：B 会话出现新消息，标题 = B 的备注/wxid |
| 3 | A → B（交替） | A、B 会话各出现一条，**按发送顺序**回读 |

合计 6 条消息，全部命中正确会话 → `summary.pass = true`。

### 任一步骤失败

- **立即停止**，不重试，不自动 resend。
- 在 `data/live_reply_probe_result.json` 的 `errors[]` 写入失败 step + 异常。
- 排查顺序见 §5，常见三类问题对应三套 workaround。
- 修复后**从 round 1 重跑**，不要 round 续跑。

---

## 4. Report Interpretation

报告路径：`data/live_reply_probe_result.json`（T1 写死路径）。结构由 T1 决定：

```json
{
  "receiver": {
    "input": "测试账号A",
    "wxid": "wxid_xxx",
    "remark": "测试账号A"
  },
  "alternate_with": {
    "input": "测试账号B",
    "wxid": "wxid_yyy",
    "remark": "测试账号B"
  },
  "rounds": [
    {
      "round": 1,
      "target": "A",
      "sent_at": "2026-10-07T18:01:23+08:00",
      "readback_at": "2026-10-07T18:01:28+08:00",
      "readback_session_matches": true
    }
    // ... 6 entries total
  ],
  "summary": {
    "total_sent": 6,
    "total_readback_match": 6,
    "pass": true
  },
  "errors": []
}
```

### 字段含义

- `receiver` / `alternate_with`：用户输入 + 解析后的 wxid + 本地备注（解析失败时 `wxid=null`）。
- `rounds[].target`：A 或 B。
- `rounds[].readback_session_matches`：DB readback 找到的新消息是否落在目标会话（按 `session_user_name` 匹配）。
- `summary.pass`：`total_sent == total_readback_match == 6` 才为 `true`。
- `errors`：step-level 错误列表；任一非空 → 验收失败，即使 `summary.pass=true` 也要人工 review。

判定标准（T17 verify）：`summary.pass=true` 且 `errors=[]` 且 `total_sent=6`。

---

## 5. Known Issues & Workarounds

| 问题 | 现象 | Workaround |
|------|------|------------|
| **视觉模型超时** | `--allow-vision` 模式下 `vision_client` 调用 > 30s 抛 `TimeoutError`；round 1 失败 | 重跑时去掉 `--allow-vision`（即 `live_reply_probe.py --receiver ... --alternate-with ... --send`，**drop vision**）；视觉是非必要路径，可降级 |
| **标题误识别** | DB readback 命中错误会话（`readback_session_matches: false`）；视觉模型把 A 会话识别成 B | 调 `backend/app/core/vision_client.py` 的 prompt（多塞 1-2 个会话标题样例 + "返回最相似的 wxid 备注"指令）；改完跑 round 1 单步确认再继续 |
| **发送失败** | AppleScript 抛 `osascript` 错误 / `MachKeyExtractor` 返回 None | (a) 检查 `data/all_keys.json` 是否过期（> 24h 重跑 T2）；(b) 系统设置 → 辅助功能 重新授权当前进程；(c) 单独跑 AppleScript：`osascript -e 'tell application "WeChat" to activate'` 验证可达。**不自动重试**，按 §3 失败即停 |

---

## 6. Cleanup

跑完后**立刻执行**，不留痕：

1. **删除报告**：
   ```bash
   rm -f /Users/kevin/Documents/GitHub/weizx/data/live_reply_probe_result.json
   ```
   报告含 wxid / 备注 / 时间戳，属于敏感数据，不入 git、不进 backup。
2. **关闭自动回复**：编辑 `config/config.yaml`：
   ```yaml
   auto_reply:
     enabled: false
   ```
   避免后台继续触发真实发送。
3. **重启 bot**：
   ```bash
   pkill -f "python -m app.main" || true
   cd backend && python -m app.main &
   ```
   让 bot 进程加载新 config（`enabled=false`）。
4. **撤回 macOS 权限**（可选，长期不跑）：
   - 系统设置 → 隐私与安全性 → 辅助功能 → 取消勾选当前终端
   - 同 → 屏幕录制 → 取消勾选
5. **通知两方联系人**：告知测试结束，下次跑前再提前打招呼（AGENTS.md "两方显式同意"）。

---

## 参考

- AGENTS.md "Live test 守则"（≥5 分钟间隔、低频、不在生产号）
- `docs/team-plan.yaml` T1（`live_reply_probe.py` 脚本规范）
- `docs/team-plan.yaml` T3（DB 监听 → AI 回复 → AppleScript 发送）
- `docs/team-plan.yaml` T17（真实 E2E 实测，6 条消息全部回读）
- `CHANGES_FROM_WEIX.md`（macOS-only 平台说明）