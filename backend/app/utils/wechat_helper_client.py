"""weizx — Swift helper HTTP client。

封装对 tools/macos-helper/WeChatHelper 启动的本地 HTTP 服务的调用，
绕开 macOS TCC：Python 不能直接读 ~/Library/Containers/.../xwechat_files，
但 helper 拿到了 Full Disk Access（或用 ~/xwechat_files fallback），
我们通过 HTTP 让它把文件读出来给 Python。

架构：
  weizx Python ──HTTP──▶ Swift helper (FDA)
                            │
                            └─File I/O──▶ ~/xwechat_files/
                                                       或 ~/Library/Containers/.../

调用顺序（与 db_reader 集成时）：
1. find_database_files() 通过 helper 列出 37 个 DB
2. get_contacts() / query_messages_since() 通过 helper
   fetch 加密字节 → 本地 pycryptodome 解密 → 内存 SQLite
"""

from __future__ import annotations

import json
import logging
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

# Default helper URL — same as Package.swift LISTEN_HOST:LISTEN_PORT.
DEFAULT_HELPER_URL = "http://127.0.0.1:12345"

# Per-request timeout. Helper is local so a slow response means it's stuck.
HELPER_TIMEOUT_SEC = 5.0

# How long to remember "helper is down" before retrying. Avoids hammering
# the helper for every DB read when it's not running.
HELPER_DOWN_BACKOFF_SEC = 30.0

# Module-level state — shared across calls in the same process.
_last_helper_check_at: float = 0.0
_last_helper_status: bool | None = None  # None = unknown


def _base_url() -> str:
    """Read helper URL from env, fall back to default."""
    import os
    return os.environ.get("WEIXX_HELPER_URL", DEFAULT_HELPER_URL)


def helper_alive(timeout: float = 1.0) -> bool:
    """Check if the helper is reachable. Caches result for backoff window.

    Returns False if helper is not running (or not yet started). Used by
    db_reader to decide HTTP vs local-file path.
    """
    global _last_helper_check_at, _last_helper_status

    now = time.time()
    if _last_helper_status is not None and (now - _last_helper_check_at) < HELPER_DOWN_BACKOFF_SEC:
        return _last_helper_status

    try:
        req = Request(f"{_base_url()}/api/health", method="GET")
        with urlopen(req, timeout=timeout) as resp:
            ok = resp.status == 200
    except (URLError, OSError, TimeoutError) as exc:
        logger.debug("helper health check failed: %s", exc)
        ok = False

    _last_helper_check_at = now
    _last_helper_status = ok
    return ok


def list_databases() -> list[dict[str, str]] | None:
    """Return [{wxid, category, filename, path}, ...] from helper, or None if down.

    Each path is an absolute path on the helper host (same as local
    machine when helper runs on the same Mac).
    """
    try:
        req = Request(f"{_base_url()}/api/db/list", method="GET")
        with urlopen(req, timeout=HELPER_TIMEOUT_SEC) as resp:
            data = json.loads(resp.read())
            return data.get("databases", [])
    except (URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
        logger.debug("helper list_databases failed: %s", exc)
        return None


def fetch_db_bytes(path: str, timeout: float = HELPER_TIMEOUT_SEC) -> bytes | None:
    """Fetch raw encrypted DB bytes via helper.

    Returns None if helper is down. On success returns the file content.
    The caller is responsible for SQLCipher decryption.
    """
    try:
        url = f"{_base_url()}/api/db/raw?path={path}"
        req = Request(url, method="GET")
        with urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                logger.warning("helper fetch failed: %s %s", resp.status, path)
                return None
            return resp.read()
    except (URLError, OSError, TimeoutError) as exc:
        logger.debug("helper fetch_db_bytes failed: %s", exc)
        return None


def fetch_db_to_tempfile(path: str) -> Path | None:
    """Fetch DB bytes via helper, write to a temp file, return its Path.

    Use this when you need to hand a file path to SQLCipher / sqlite3
    (both require file path, not bytes).
    """
    data = fetch_db_bytes(path)
    if data is None:
        return None
    # NamedTemporaryFile so multiple concurrent calls don't collide.
    f = tempfile.NamedTemporaryFile(
        prefix="weix-helper-", suffix=".db", delete=False
    )
    f.write(data)
    f.close()
    return Path(f.name)
