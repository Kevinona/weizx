#!/bin/bash
# build-app.sh — 把 Swift helper 打包成 .app bundle
# 用法：cd tools/macos-helper && ./build-app.sh
# 产物：WeChatHelper.app  → 拖到 /Applications/ 启动
# 第一次启动会弹"完全磁盘访问权限"授权；之后 helper 能直接读沙箱。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="WeChatHelper"
BUNDLE_ID="com.weizx.helper"

cd "$SCRIPT_DIR"

echo "[1/4] swift build -c release"
swift build -c release

BIN_PATH=".build/release/$APP_NAME"
if [ ! -f "$BIN_PATH" ]; then
    echo "ERROR: $BIN_PATH not built" >&2
    exit 1
fi

echo "[2/4] copy binary to .app/Contents/MacOS/"
APP_PATH="$SCRIPT_DIR/$APP_NAME.app"
mkdir -p "$APP_PATH/Contents/MacOS"
cp "$BIN_PATH" "$APP_PATH/Contents/MacOS/$APP_NAME"
chmod +x "$APP_PATH/Contents/MacOS/$APP_NAME"

echo "[3/4] ad-hoc codesign (needed for FDA + Gatekeeper)"
codesign --force --deep --sign - "$APP_PATH"
# Adhoc 签名足够本地使用；如需对外发布可换 Developer ID

echo "[4/4] verify"
codesign -dv "$APP_PATH" 2>&1 | head -5
echo ""
echo "✅ Built: $APP_PATH"
echo ""
echo "Next steps:"
echo "  1. open '$APP_PATH'   # 触发 macOS 弹窗：'完全磁盘访问权限'"
echo "  2. 在 系统设置 → 隐私与安全性 → 完全磁盘访问 启用 $APP_NAME"
echo "  3. weizx Python 端会自动用 helper 的 /api/db/list / /api/db/raw"
echo ""
echo "Test from terminal:"
echo "  curl http://127.0.0.1:12345/api/db/list | python3 -m json.tool | head -20"
