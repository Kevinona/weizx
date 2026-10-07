# 与原项目 weix 的差异

本文档记录 weizx 相对于上游 [zqaini002/weix](https://github.com/zqaini002/weix)
的修改。weizx 是 weix 的 fork，目标是专注于 macOS 实现，删除 Windows 分支。

## 删除的代码

### 核心模块

| 文件 | 原因 |
|------|------|
| `backend/app/core/db_reader_windows.py` | 仅 Windows 可用 |
| `backend/app/core/key_extractor_windows.py` | `ReadProcessMemory` 仅 Win32 |
| `backend/app/core/sender_windows.py` | pyautogui + Win32 API |
| `backend/app/core/wechat_paths_windows.py` | Windows 微信路径查找 |

### 工具脚本

| 文件 | 原因 |
|------|------|
| `backend/diagnose_weixin_windows.py` | Windows 专用诊断 |
| `backend/live_reply_probe_windows.py` | Windows 实机验收脚本 |
| `scripts/build_backend.bat` | Windows 构建 |
| `scripts/setup.bat` | Windows 安装 |
| `scripts/start.bat` | Windows 启动 |
| `scripts/start_weix.bat` | Windows 启动变体 |

### 测试

| 文件 | 原因 |
|------|------|
| `backend/tests/test_db_reader_windows.py` | Windows DB reader 测试 |
| `backend/tests/test_sender_windows.py` | Windows sender 测试 |
| `backend/tests/test_live_reply_probe.py` | Windows 实机验收测试 |
| `backend/tests/test_platform_vision_sender.py` | Windows 分发器测试 |
| `backend/tests/test_key_extractor_local_env.py` | Windows 密钥加载测试 |
| `backend/tests/test_wcdb_cipher_config.py` | Windows 密钥恢复测试 |
| `backend/tests/test_vision_integration.py` | Windows vision 集成测试 |
| `backend/tests/test_platform_api.py` 部分 | 删掉了 2 个 Windows 专用断言 |

## 修改的代码

| 文件 | 改动 |
|------|------|
| `backend/app/config.py` | 删除 `windows_sender` 字段；`platform` 默认值改为 `darwin`；删除 `sys.platform` 分发 |
| `backend/app/core/platform.py` | 重写：只保留 macOS 实现，无 `is_windows` 分支；新增 `reset()` 用于测试 |
| `backend/app/core/auto_reply_pipeline.py` | 删除 3 处 Windows 分支（sender 选择 + windows_sender 配置访问） |
| `backend/app/api/platform_api.py` | 删除 Windows 错误消息分支，删除 `WindowsDBReader` 引用 |
| `backend/app/main.py` | 删除 `sys.platform == "win32"` 分支 |
| `backend/launcher.py` | 删除 `_is_admin` / `_request_admin` / UAC；非 darwin 平台直接退出 |
| `backend/requirements.txt` | 删除 `wcferry / pywin32 / pygetwindow`；新增 `pyobjc-framework-Quartz` |
| `.github/workflows/build.yml` | 删除 `build-windows` job；`release` job 只上传 macOS artifact |
| `README.md` | 重写，明确 macOS only；引用本差异文档 |

## 保留的 macOS 代码

未做实质修改的 macOS 实现（仅做导入与文案校对）：

- `backend/app/core/db_reader_macos.py`
- `backend/app/core/key_extractor_macos.py`
- `backend/app/core/sender_macos.py`
- `backend/app/core/screenshot_helper.py`
- `backend/app/core/mach_helper/`（C 桥接 mach_vm_read_overwrite）
- `backend/app/core/ocr_helper.swift`
- `backend/app/ai/*`（LangChain + LangGraph）
- `backend/app/workflow/*`（规则 / 模板 / 工作流引擎）
- `backend/app/api/*`（REST API）
- `frontend/`（Vue 3 + Element Plus）
- `scripts/setup.sh` + `scripts/start.sh`（macOS 启动入口）

## 上游依赖关系

```
weizx (本项目)
  └─ 派生自 zqaini002/weix (master @ 2026-09-30)
       License: MIT
       Attribution: 见 README 顶部 badge
```

后续若原计划恢复，我们**不主动同步上游**；如需 cherry-pick，按 commit 而非 fork 同步。