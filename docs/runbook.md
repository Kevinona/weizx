# weizx 运维手册 (macOS)

这份手册给拿到 weizx 仓库、想在自己 Mac 上把自动回复机器人跑起来的人。

## 1. 安装

```bash
# 1. 克隆
git clone https://github.com/kevinsweizx/weizx.git
cd weizx

# 2. 系统依赖（编译 mach_helper 需要）
brew install cmake

# 3. 后端依赖
python3.12 -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt

# 4. 编译 C 桥接模块（mac_vm_read_overwrite）
cd backend/app/core/mach_helper
make
cd -

# 5. 前端构建（生成 admin UI）
cd frontend && npm ci && npm run build && cd -
```

> **版本要求** Python 3.12+，Node 20+。其他 Python 版本可能因为 pydantic / langchain 兼容性报错。

## 2. 配置文件

```bash
cp config/config.example.yaml config/config.yaml
cp .env.example .env
```

**必填**：
- `.env` 中 `DEEPSEEK_API_KEY=sk-...` — 没有这个 key 自动回复停发
- `config/config.yaml` 中 `auto_reply.private_whitelist` — 至少 1 个联系人昵称

**建议改**：
- `JWT_SECRET` — 改成随机字符串（生产环境）
- `ADMIN_PASSWORD` — 改默认 admin123

## 3. macOS 权限授予

启动 bot 前必须在「系统设置 → 隐私与安全性」授予两项权限：

| 权限 | 用途 | 必加进程 |
|------|--------|----------|
| **辅助功能** | 模拟键盘输入、AppleScript 操作 | Terminal / iTerm2 / PyCharm / WeizxLauncher |
| **屏幕录制** | 截图给视觉模型定位聊天窗口 | Terminal / iTerm2 / PyCharm / WeizxLauncher |

**加完权限必须重启对应进程**（macOS 不会动态刷新已运行进程）。

> 如果用 `bash scripts/start.sh` 启动，授予的是启动它的那个 Terminal 应用。

## 4. 首次启动

```bash
# 方式 A：直接跑后端（开发模式）
bash scripts/start.sh

# 方式 B：GUI launcher (PyQt6)
cd backend && python launcher.py
```

启动后会做：
1. 编译 macOS C 桥接（首次）
2. 验证 / 提取本机微信数据库密钥 → 写入 `data/all_keys.json`
3. 启动 FastAPI 在 `http://127.0.0.1:8000`

**验证启动成功**：
```bash
curl http://127.0.0.1:8000/api/health
# 返回 {"status": "ok", "wechat_db": "verified", ...}
```

**登录管理后台**：浏览器打开 `http://127.0.0.1:8000`，用户名默认 `admin`，密码来自 `.env` 的 `ADMIN_PASSWORD`。

## 5. 实机验收

启动之后**不要直接全开自动回复**。先用 `backend/live_reply_probe.py`（T1 待写）做最小验证：
- 默认模式：仅检查联系人是否可达，不发消息
- 加 `--send` 才发送；指定两个联系人轮流发 3 轮共 6 条

详见 [runbook-live-test.md](./runbook-live-test.md)。

## 6. 故障排查

### 6.1 密钥提取失败

**表现**：`wechat_db: "unverified"` 或日志含 `mach_vm_read_overwrite failed`。

**步骤**：
1. 确认微信已启动并登录
2. 重新授予权限 → **完全退出** Terminal → 重开 → 再启动 bot
3. `data/all_keys.json` 删除后重跑，强制重新提取
5. 仍不行就在 `.env` 手动填：
   ```
   WEIZX_WECHAT_DB_KEY=<64 hex>
   WEIZX_WECHAT_CONTACT_DB_KEY=<64 hex>
   ```
   key 通过本机数据库验证后才能用（不同微信账号的 key 不同）

### 6.2 数据库不可达

**表现**：日志含 `open_db failed` 或 `cannot find database files`。

**检查**：
- `~/Library/Containers/com.tencent.xinWeChat/` 存在？微信是 Mac App Store 安装的
- 微信是否仍登录（数据库可能因掉线被加密 / 删除）

### 6.3 AppleScript 不生效

**表现**：日志显示 send 成功但实际没发出去。

**步骤**：
1. 「系统设置 → 隐私与安全性 → 辅助功能」确认授予的进程名对得上
2. 打开 Console.app → 搜索 `WeChat` 看错误
3. 单独测试 AppleScript：
   ```bash
   osascript -e 'tell application "WeChat" to activate'
   ```

### 6.4 视觉模型超时

**表现**：日志含 `VisionClient timeout`。

**检查**：
- `DEEPSEEK_API_KEY` 是否有效（`curl https://api.deepseek.com/v1/models -H "Authorization: Bearer $DEEPSEEK_API_KEY"`）
- API 是否限流（降级路径：禁用视觉定位，纯 DB+搜索框发送）
- 网络能否访问 `api.deepseek.com`

## 7. 封号应对

**信号**：
- 微信客户端提示"账号异常"
- 自动回复后对方看不到自己刚发的
- 启动时密钥提取突然失败（可能账号被风控）

**步骤**：
1. **立即停止 bot**：`config/config.yaml` 设 `auto_reply.enabled: false`，重启
2. 不要在本机继续启动 bot 至少 24-72 小时
3. 在微信客户端里发几条正常消息"养号"
4. 24 小时后重启 bot，频率配置加倍保守（间隔 30s+）
5. 如果再次触发 → 联系腾讯客服申诉 / 换号

**降低封号风险的做法**：
- 实机验证用不重要的微信号
- 单会话间隔 ≥5 分钟
- 每天不超过 50 条自动回复
- 不在工作时间运行

## 8. 升级与维护

```bash
# 拉最新代码
git pull

# 更新 Python 依赖
pip install -r backend/requirements.txt --upgrade

# 重新编译 C 模块（升级 macOS 后必做）
cd backend/app/core/mach_helper && make clean && make
```

## 9. 备份重要数据

| 文件 | 是否备份 |
|------|----------|
| `config/config.yaml` | **是**（不含敏感但配置丢了很麻烦） |
| `data/all_keys.json` | **否**（本机专有，跨设备用会失败） |
| `.env` | **否**（含 API key，泄露风险） |
| `data/logs/` | 可选 |

`data/` 目录整体不提交（`.gitignore` 已配），所以也不会被 git 备份。