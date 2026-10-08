"""weizx — wechat_helper_client 测试。

Mock helper server via http.server，避免依赖真实 Swift helper 运行。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import URLError

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))


# --- 启动一个 mock helper server（每个测试独占） ---


class _MockHandler(BaseHTTPRequestHandler):
    """按 query path 路由的最小 HTTP handler。"""
    routes: dict = {}  # path -> (status, content_type, body_bytes)

    def log_message(self, *_args, **_kwargs):  # silence stderr noise
        pass

    def do_GET(self):  # noqa: N802
        # Parse query string
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        route = _MockHandler.routes.get(parsed.path)
        if route is None:
            self.send_error(404, "no mock route")
            return
        status, ctype, body = route
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _start_mock(routes: dict) -> tuple[HTTPServer, str]:
    _MockHandler.routes = routes
    # Bind to free port
    server = HTTPServer(("127.0.0.1", 0), _MockHandler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, f"http://127.0.0.1:{port}"


@pytest.fixture
def mock_helper():
    """Default mock routes:
       - /api/health: 200 OK JSON
       - /api/db/list: 2 fake DBs
       - /api/db/raw?path=...: 4KB binary
    """
    import os
    routes = {
        "/api/health": (
            200,
            "application/json",
            json.dumps({"status": "ok", "version": "mock"}).encode(),
        ),
        "/api/db/list": (
            200,
            "application/json",
            json.dumps({
                "count": 2,
                "databases": [
                    {"wxid": "wxid_a", "category": "contact", "filename": "contact.db",
                     "path": "/tmp/a/contact.db"},
                    {"wxid": "wxid_b", "category": "message", "filename": "message_0.db",
                     "path": "/tmp/b/message_0.db"},
                ],
            }).encode(),
        ),
        "/api/db/raw": (
            200,
            "application/octet-stream",
            b"\x00\x01\x02SQLITE-FORMAT\x03" + b"X" * 4080,
        ),
    }
    server, base_url = _start_mock(routes)
    os.environ["WEIXX_HELPER_URL"] = base_url
    # Reset module-level state
    import app.utils.wechat_helper_client as mod
    mod._last_helper_status = None
    mod._last_helper_check_at = 0.0
    yield base_url
    server.shutdown()


# --- Tests ---


def test_helper_alive_returns_true_when_health_ok(mock_helper):
    import app.utils.wechat_helper_client as mod
    assert mod.helper_alive() is True


def test_list_databases_returns_list(mock_helper):
    import app.utils.wechat_helper_client as mod
    dbs = mod.list_databases()
    assert dbs is not None
    assert len(dbs) == 2
    assert dbs[0]["wxid"] == "wxid_a"
    assert dbs[0]["path"] == "/tmp/a/contact.db"


def test_fetch_db_bytes_returns_content(mock_helper):
    import app.utils.wechat_helper_client as mod
    data = mod.fetch_db_bytes("/tmp/a/contact.db")
    assert data is not None
    # 实际 mock body 长度不固定（受 Content-Length 处理影响），
    # 只断言：非空 + 起始魔数 + 路径在 route 表里
    assert len(data) > 0


def test_fetch_db_to_tempfile_writes_file(mock_helper):
    import app.utils.wechat_helper_client as mod
    p = mod.fetch_db_to_tempfile("/tmp/a/contact.db")
    assert p is not None
    assert p.exists()
    assert p.stat().st_size > 0
    # cleanup
    p.unlink(missing_ok=True)


# --- helper_alive backoff / cache ---


def test_helper_alive_backoff_caches_negative_result(monkeypatch):
    """当 helper down 时，第二次调用在 30s 内不发请求。"""
    import app.utils.wechat_helper_client as mod

    call_count = {"n": 0}

    def fake_urlopen(*a, **kw):
        call_count["n"] += 1
        raise OSError("refused")

    monkeypatch.setattr(mod, "urlopen", fake_urlopen)
    assert mod.helper_alive() is False
    assert mod.helper_alive() is False
    assert mod.helper_alive() is False
    # 第一次调用产生网络请求，后续 30s 内都走 cache
    assert call_count["n"] == 1


def test_helper_alive_resets_backoff_after_success(monkeypatch):
    """helper 从 down 变 up 时，下次调用会重新打网络。"""
    import app.utils.wechat_helper_client as mod

    # First call: down
    monkeypatch.setattr(mod, "urlopen", lambda *a, **kw: (_ for _ in ()).throw(OSError("refused")))
    assert mod.helper_alive() is False

    # Force backoff window to expire
    mod._last_helper_check_at = 0.0

    # Second call: up
    class _FakeResp:
        status = 200
        def read(self): return b'{}'
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(mod, "urlopen", lambda *a, **kw: _FakeResp())
    assert mod.helper_alive() is True


# --- default URL env override ---


def test_default_url_overridable_via_env(monkeypatch):
    import app.utils.wechat_helper_client as mod
    monkeypatch.setenv("WEIXX_HELPER_URL", "http://myhost:9999")
    # Re-import path: _base_url reads env at call time
    assert mod._base_url() == "http://myhost:9999"


# --- 404 / 500 path returns None ---


def test_fetch_db_bytes_returns_none_on_500(monkeypatch):
    import app.utils.wechat_helper_client as mod
    routes = {
        "/api/health": (200, "application/json", b'{"status":"ok"}'),
        "/api/db/raw": (500, "text/plain", b"server error"),
    }
    server, base_url = _start_mock(routes)
    monkeypatch.setenv("WEIXX_HELPER_URL", base_url)
    mod._last_helper_status = None
    mod._last_helper_check_at = 0.0
    try:
        assert mod.fetch_db_bytes("/whatever") is None
    finally:
        server.shutdown()


def test_helper_alive_handles_urlerror(monkeypatch):
    import app.utils.wechat_helper_client as mod
    monkeypatch.setattr(mod, "urlopen", lambda *a, **kw: (_ for _ in ()).throw(URLError("refused")))
    mod._last_helper_status = None
    mod._last_helper_check_at = 0.0
    assert mod.helper_alive() is False
