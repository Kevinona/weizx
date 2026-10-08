# weizx WeChatHelper

Swift HTTP server that bridges the macOS sandbox limitation: Python
can't directly read the WeChat container directory, but a `.app`
bundle with Full Disk Access can. This helper serves DB bytes over
localhost to the weizx Python backend.

## Why

WeChat stores its SQLite DBs in
`~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/`,
which is sandboxed by macOS TCC. Even with Full Disk Access granted
to Terminal.app, Python subprocesses don't inherit that access for
unsigned binaries. This helper:

1. Runs as a `.app` bundle (with proper Info.plist + ad-hoc codesign)
2. Receives FDA via System Settings when first launched
3. Reads the WeChat container on behalf of the Python backend
4. Serves DB bytes over `http://127.0.0.1:12345` (localhost only)

## Build & install

```bash
cd tools/macos-helper
./build-app.sh                       # builds .app, ad-hoc codesigns
open WeChatHelper.app                 # registers with Launch Services
# macOS prompts: System Settings → Privacy & Security
#   → Full Disk Access → enable WeChatHelper
pkill WeChatHelper
open WeChatHelper.app                 # restart to apply FDA
```

Verify with:
```bash
curl -s http://127.0.0.1:12345/api/health
curl -s http://127.0.0.1:12345/api/db/list | python3 -m json.tool | head -30
```

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | liveness check; returns build info + db_root |
| GET | `/api/db/list` | list all DBs across wxid accounts |
| GET | `/api/db/raw?path=...` | fetch raw encrypted DB bytes |

Path traversal is **not** protected — same-user machine assumption.
If you need to expose this over network, put it behind a reverse proxy
with auth.

## Data sources (priority order)

1. `WEIXX_DB_ROOT` env var (explicit override)
2. `~/Library/Containers/com.tencent.xinWeChat/.../xwechat_files/` — needs FDA via .app
3. `~/xwechat_files/` — manual copy fallback, no FDA needed

## Limitations

- **Key extraction is NOT done by this helper.** The keys needed to
  decrypt SQLCipher pages must be provided in `.env` (e.g.
  `WEIZX_WECHAT_DB_KEY=...`). Auto-extraction from process memory
  (`mach_vm_region`) is blocked by macOS Hardened Runtime, which
  FDA does not unlock.
- The helper is a local-only HTTP server bound to `127.0.0.1`. Do
  not expose it to a network.
- It does NOT run as a launchd daemon. The `open` command keeps
  the process alive while the .app is in Launch Services. For a
  proper daemon, wrap it in a `launchd` plist.

## Architecture

```
weizx Python ──HTTP──▶ Swift helper (this binary, FDA)
                          │
                          └─File I/O──▶ ~/xwechat_files/
                                            or ~/Library/Containers/.../xwechat_files/
```

SQLCipher decryption is done in Python via `pycryptodome`. The helper
intentionally does NOT do decryption — it just serves raw bytes.
This keeps the helper small and avoids porting a SQLCipher build.

## Development

```bash
swift build -c release
.build/release/WeChatHelper
# or with extra logging:
WEIXX_DEBUG=1 .build/release/WeChatHelper
```

The `stateUpdateHandler` prints to stderr when FDA appears to be
missing (`/api/db/list` returns 0 entries, or the sandbox path
returns PermissionError).
