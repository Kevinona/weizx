<div align="center">

# Weizx

### 让 AI 接入你的微信回复流程 · macOS only

本地数据库收消息 · AppleScript 视觉定位 · 可配置的 AI 回复

[![Build](https://img.shields.io/badge/build-macOS-blue)](https://github.com/Kevinona/weizx/actions)
[![License](https://img.shields.io/badge/license-MIT-blue)](#license)
[![Forked from](https://img.shields.io/badge/forked%20from-zqaini002%2Fweix-14b8a6)](https://github.com/zqaini002/weix)

**[快速开始](#快速开始) · [架构](#系统架构) · [平台差异](#平台支持) · [反馈问题](https://github.com/Kevinona/weizx/issues)**

</div>

---

> **这是一个 fork 项目。** weizx 在 [zqaini002/weix](https://github.com/zqaini002/weix) 的基础上删除了 Windows 路径，专注 macOS 实现。
> 原项目验证了 Windows PC 微信 4.1.15.13 全链路；本项目重点是把 macOS 路径从"模拟测试"推进到"实机可跑"。
> 详见 [CHANGES_FROM_WEIX.md](./CHANGES_FROM_WEIX.md)。

## 这是什么

本机运行的微信自动回复机器人：
- **收消息**：直接读微信本地 SQLite 数据库（SQLCipher 4 加密，本机提取密钥）
- **AI 回复**：LangChain 0.3 + LangGraph 编排大模型（DeepSeek / OpenAI / 硅基流动）
- **发消息**：macOS AppleScript 模拟键盘输入（不注入、不 Hook）
- **可视化管理**：Vue 3 + Element Plus Web 后台

**目标**：让 macOS 路径像 Windows 路径一样稳定可跑通。

## 已验证的进展

| 项目 | 当前结果（2026-10-07） |
| --- | --- |
| macOS 密钥提取 | 代码迁移完成；`mach_vm_read_overwrite` 路径已就绪，待实机验证 |
| macOS DB 监听 | 代码迁移完成；aiosqlite 单文件可读，待实机验证 |
| macOS 发送 | AppleScript 路径已就绪；待实机验收 |
| 后端测试 | 在原项目基线上跑通；本项目代码调整后需重测 |
| Windows | **已移除**（不需要维护双平台） |

## 系统架构

```
微信客户端 ──(只读)──▶ 数据库解密层 ──▶ 消息监听器 ──▶ AI Agent（核心）
                                                         │
                                              LangChain + 大模型
                                                         │
                                          ┌──────────────┼──────────────┐
                                          ▼              ▼              ▼
                                      规则引擎       工作流引擎      工具调用
                                          │              │              │
                                          └──────────────┼──────────────┘
                                                         ▼
                                                  消息发送层（AppleScript）
```

## 平台支持

| 维度 | macOS（本项目） | Windows（原项目） |
|------|----------------|------------------|
| 状态 | **当前项目** | 已移除分支 |
| 微信版本 | Mac 微信 4.x (App Store) | ~~PC 微信 4.1.15.13 已验证~~ |
| DB 路径 | `~/Library/Containers/com.tencent.xinWeChat/...` | ~~`Documents/xwechat_files/...`~~ |
| 密钥提取 | `mach_vm_read_overwrite` (Mach VM) | ~~`ReadProcessMemory` (Win32)~~ |
| 消息发送 | AppleScript 模拟键盘输入 | ~~pyautogui + 右键粘贴~~ |
| 权限要求 | 「辅助功能」+「屏幕录制」 | ~~管理员权限~~ |

## 技术栈

- 后端：FastAPI + SQLAlchemy (async) + aiosqlite
- AI：LangChain 0.3+ + LangGraph
- 前端：Vue 3 + Element Plus + ECharts + Pinia
- 定时：APScheduler
- 数据库解密：pycryptodome (SQLCipher 4)

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/Kevinona/weizx.git
cd weizx
```

### 2. 安装依赖

```bash
# 后端
python3.12 -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt

# 前端
cd frontend && npm ci && npm run build && cd ..
```

### 3. 配置文件

```bash
cp config/config.example.yaml config/config.yaml
cp .env.example .env
# 编辑 .env 填入 DEEPSEEK_API_KEY（必须）
# 编辑 config/config.yaml 设置白名单、规则等
```

### 4. 授予 macOS 权限

```
系统设置 → 隐私与安全性
  ├─ 辅助功能：添加 Weizx（PyQt 启动器或终端）
  └─ 屏幕录制：添加 Weizx
```

### 5. 启动

```bash
bash scripts/start.sh
# 或手动
cd backend && python app/main.py
```

启动时验证本机数据库密钥；缓存失效时重新提取并验证。
`data/all_keys.json` 属于本机数据，不应从另一台电脑复制。
收消息始终依赖数据库；数据库不可用时自动回复保持停发。

## 管理后台

访问 http://localhost:5173

- **仪表盘**：在线状态、消息数、活跃群聊、订单数
- **统计报告**：发言排行、时段分布、关键词、AI 摘要
- **消息日志**：历史消息查询与详情
- **聊天配置**：群聊白名单、私聊权限、回复模式
- **自动回复规则**：关键词/正则/意图规则管理
- **消息模板**：文本/卡片/表单/列表模板编辑器
- **工作流配置**：状态机定义（默认含陪玩点单流程）
- **转发规则**：触发条件 + 目标群配置
- **AI 配置**：Provider、API Key、模型、System Prompt
- **本人 Skill**：AI 分析你的聊天记录，自动生成你的语气人设
- **定时任务**：日报/周报/健康检查/数据清理管理
- **系统配置**：日志级别、数据保留、异常告警、备份恢复

## 防封号策略

1. **只读收消息**：从本机数据库读取消息，并验证数据库密钥
2. **GUI 发送**：macOS 采用 AppleScript 模拟键盘输入；发送前核对联系人和聊天标题
3. **频率控制**：全局每分钟 ≤ 20 条，单会话冷却 30s
4. **行为模拟**：发送间隔随机化 8-20s
5. **熔断保护**：连续失败 3 次暂停 5 分钟

## 与原项目的差异

详见 [CHANGES_FROM_WEIX.md](./CHANGES_FROM_WEIX.md)。

核心变化：
- 删除所有 Windows 分支（核心模块、测试、CI、launcher、scripts）
- 移除 `wcferry / pywin32 / pygetwindow` 依赖
- 新增 `pyobjc-framework-Quartz`（macOS Quartz 截屏）
- `Platform` 类简化为 macOS only（移除 `is_windows` 分发器）
- `Config.platform` 默认值改为 `darwin`
- CI workflow 只保留 `build-macos` job

## 安全 / 风险提示

- **macOS 路径未实机验证**（原项目 README 同此说明）
- GUI 自动化有**封号风险**（项目不保证账号不受平台限制）
- 密钥提取属于灰色操作，腾讯检测到可能限制账号
- 测试时请用**个人不重要的微信账号**，间隔拉长（≥5 分钟）

## License

MIT，继承自原项目 [zqaini002/weix](https://github.com/zqaini002/weix)。