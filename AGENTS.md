# weizx — AI Agent 工作约定

本文件供 mavis / Claude / Codex / Cursor 等 Agent 协作时参考。**人读的文档在 README.md；本文件只写给 Agent。**

## 项目定位

- **weizx** 是 [zqaini002/weix](https://github.com/zqaini002/weix) 的 fork，专攻 macOS 实现。
- Windows 路径已全部移除；`Platform` 类只剩 macOS 实现。
- 详见 `CHANGES_FROM_WEIX.md`。

## 工作目录速查

| 路径 | 用途 |
|------|------|
| `backend/app/core/` | 平台核心：DB 读取、密钥提取、AppleScript 发送、视觉定位 |
| `backend/app/core/mach_helper/` | C 桥接代码（mach_vm_read_overwrite），需编译 |
| `backend/app/core/ocr_helper.swift` | Swift OCR 助手 |
| `backend/app/ai/` | LangChain + LangGraph Agent、工具、记忆、RAG |
| `backend/app/workflow/` | 工作流 / 规则 / 模板 / 转发引擎 |
| `backend/app/api/` | REST API 路由 |
| `backend/app/models/` | SQLAlchemy ORM + Pydantic schemas |
| `backend/app/services/` | 业务服务层 |
| `backend/launcher.py` | PyQt6 启动器（macOS） |
| `frontend/` | Vue 3 + Element Plus 管理后台 |
| `config/config.example.yaml` | 配置模板（提交）；`config/config.yaml` 本机配置（不提交） |
| `.env.example` → `.env` | API key 与本机密钥（不提交） |
| `data/` | 运行时数据，全部不提交 |

## 平台依赖（macOS only）

```bash
# 系统级
brew install cmake  # 编译 mach_helper

# Python
pip install -r backend/requirements.txt

# macOS 权限（系统设置 → 隐私与安全性）
# - 辅助功能
# - 屏幕录制
```

## 构建与运行

```bash
# 后端开发模式
cd backend
python -m venv ../venv && source ../venv/bin/activate
pip install -r requirements.txt
python -m app.main

# 前端开发模式
cd frontend && npm install && npm run dev

# 后端测试
cd backend && pytest

# 编译 mach_helper（首次或修改 .c 后）
cd backend/app/core/mach_helper && make
```

## Agent 团队协作约定

### 模块归属（avoid overlap）

| 模块 | Owner agent | 备注 |
|------|-------------|------|
| `core/db_reader_macos.py` | backend-platform-engineer | aiosqlite + SQLCipher |
| `core/key_extractor_macos.py` | backend-platform-engineer | mach_vm 桥接 |
| `core/sender_macos.py` | backend-platform-engineer | AppleScript |
| `core/auto_reply_pipeline.py` | backend-platform-engineer | 主流水线 |
| `core/anti_detect.py` | backend-platform-engineer | 频率控制 / 熔断 |
| `ai/agent.py` 等 | prompt-iteration-specialist | LangChain |
| `workflow/*` | backend-platform-engineer | 状态机 |
| `api/*` | backend-platform-engineer | FastAPI 路由 |
| `frontend/src/*` | frontend-engineer | Vue 3 |
| `tests/*` | verifier | resolver (校验收货) |
| 整体改写 / 重构 | 由 `mavis` 在 evaluate 中调多 agent 协作 | — |

### 提交前自检清单

- [ ] 不引入 Windows 依赖（pywin32 / wcferry / pygetwindow）
- [ ] 不引入 `sys.platform` 平台分支
- [ ] 不写回 `is_windows` / `windows_sender` 等字段
- [ ] AppleScript 字符串用三引号包裹，避免转义错误
- [ ] 加 SQLite 操作必须用 `aiosqlite` 异步上下文
- [ ] 频率限制走 `app.utils.rate_limiter` 统一接口
- [ ] 加新依赖更新 `backend/requirements.txt`
- [ ] 改 `config/config.example.yaml` 才允许提交（不要碰 `config.yaml`）
- [ ] 不写日志到 `data/` 外的位置
- [ ] 提交前跑 `pytest backend/tests -x` 至少 smoke 通过

### Live test 守则

- **仅低频自测**：单会话间隔 ≥5 分钟；不在工作时间跑；不在重要联系人群跑
- **绝不在生产账号上跑** — 个人不重要的小号，且接受封号后果
- 发送失败时改：先在 `data/all_keys.json` 验证密钥；再单独测试 AppleScript
- 真机验证脚本 `backend/live_reply_probe.py`（待写）模仿 Windows 版的 `live_reply_probe_windows.py`

## 已知缺口 / 待办

| 模块 | 状态 | 下一步 |
|------|------|--------|
| macOS key extractor 实机验证 | 代码迁移完成 | 在 Kevin 微信上跑一次 |
| macOS AppleScript sender 实机验证 | 代码迁移完成 | 跑 `live_reply_probe.py` |
| `live_reply_probe.py`（macOS 版） | 缺失 | 模仿 Windows 版写 |
| CI build on real WeChat | 不可能 | 只能在 Kevin 本机手测 |
| 文档：deploy 到真实 Mac 的 runbook | 缺失 | 写 `docs/runbook.md` |

## 跨 Agent 信息同步

- 提交前 `git status` 自检
- 大改动前先开 RFC：讨论 Plan
- 完成模块后更新本文档对应行
- 关键发现追加到 `agentMemory`（`/Users/kevin/.minimax/agents/<name>/memory/MEMORY.md`）而非本文