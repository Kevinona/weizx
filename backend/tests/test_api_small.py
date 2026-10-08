"""weizx — small API router tests (auth / dashboard / messages / statistics).

Each of the four routers is tiny (≤70 LoC), so we bundle them into one file
with one test per endpoint. Upstream services and config are mocked so the
tests do not touch a real database, macOS platform, or the filesystem.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Imports AFTER sys.path tweak so ``app.*`` resolves regardless of where
# pytest is invoked from.
from app.api import auth as api_auth  # noqa: E402
from app.api import dashboard as api_dashboard  # noqa: E402
from app.api import messages as api_messages  # noqa: E402
from app.api import statistics as api_statistics  # noqa: E402
from app.api.auth import verify_token  # noqa: E402
from app.deps import (  # noqa: E402
    get_message_service,
    get_report_service,
    get_session,
    get_statistics_service,
)
from jose import jwt as jose_jwt  # noqa: E402


# ---------------------------------------------------------------------------
# Test app fixture + reusable fakes
# ---------------------------------------------------------------------------


class _FakeSession:
    """Bare-minimum AsyncSession stub. Records executes; returns empty results."""


def _build_app(
    *,
    message_service=None,
    statistics_service=None,
    report_service=None,
) -> FastAPI:
    """Build a FastAPI app with all 4 routers and dependency_overrides wired.

    Having all routers in one app lets every test exercise the real route
    stack (URL parsing, status codes, response_model) without spinning up an
    ASGI server.
    """
    app = FastAPI()
    app.include_router(api_auth.router)
    app.include_router(api_dashboard.router)
    app.include_router(api_messages.router)
    app.include_router(api_statistics.router)

    # Skip the real JWT signature check — tests do not need to forge tokens.
    app.dependency_overrides[verify_token] = lambda: {"sub": "admin"}

    # Replace DB-bound service providers with whichever fakes the test injects.
    app.dependency_overrides[get_session] = lambda: _FakeSession()

    def _ms():
        return message_service or SimpleNamespace()

    def _ss():
        return statistics_service or SimpleNamespace()

    def _rs():
        return report_service or SimpleNamespace()

    app.dependency_overrides[get_message_service] = _ms
    app.dependency_overrides[get_statistics_service] = _ss
    app.dependency_overrides[get_report_service] = _rs
    return app


# ---------------------------------------------------------------------------
# auth.py — POST /api/auth/login
# ---------------------------------------------------------------------------


def test_login_returns_token_for_plaintext_admin(monkeypatch):
    """Login with the plaintext-password branch must mint a JWT."""
    cfg = SimpleNamespace(admin={
        "username": "admin",
        "password": "secret123",
        "jwt_secret": "test-secret",
    })
    monkeypatch.setattr(api_auth, "get_config", lambda: cfg)

    app = _build_app()
    client = TestClient(app)

    resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "secret123"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    # Generated token must be decodable with the same secret + alg.
    decoded = jose_jwt.decode(body["access_token"], "test-secret", algorithms=["HS256"])
    assert decoded["sub"] == "admin"


# ---------------------------------------------------------------------------
# dashboard.py — GET /api/dashboard/overview
# ---------------------------------------------------------------------------


def test_dashboard_overview_returns_aggregated_metrics(monkeypatch):
    """All five fields must be populated from the mocked services."""
    fake_sender = SimpleNamespace(
        is_wechat_running=_async_return(True),
    )
    fake_platform = SimpleNamespace(name="macos", sender=fake_sender)
    monkeypatch.setattr(api_dashboard, "Platform").get = lambda: fake_platform

    # Replace the helper at the import site so we don't import the real
    # counter module's globals.
    monkeypatch.setattr(api_dashboard, "get_ai_call_count", lambda: 42)

    svc = SimpleNamespace(
        get_today_message_count=_async_return(7),
        get_active_rooms=_async_return(3),
    )

    app = _build_app(message_service=svc)

    class _ScalarOne:
        def scalar(self):
            return 5

    class _FakeResult:
        def scalar(self):
            return 5

    class _ExecSession:
        async def execute(self, *_a, **_kw):
            return _FakeResult()

    app.dependency_overrides[get_session] = lambda: _ExecSession()

    client = TestClient(app)
    resp = client.get("/api/dashboard/overview")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {
        "platform": "macos",
        "wechat_online": True,
        "today_messages": 7,
        "active_rooms": 3,
        "ai_calls": 42,
        "pending_orders": 5,
    }


# ---------------------------------------------------------------------------
# messages.py — GET /api/messages  +  POST /api/messages/send
# ---------------------------------------------------------------------------


def test_list_messages_returns_paginated_payload(monkeypatch):
    msg = SimpleNamespace(
        msg_id="m1", msg_type=1, content="hi",
        sender_wxid="u1", sender_name="U",
        room_id="r1", room_name="R", is_group=False,
        create_time="2026-01-01T00:00:00",
    )
    svc = SimpleNamespace(get_messages=_async_return(([msg], 1)))

    app = _build_app(message_service=svc)
    client = TestClient(app)

    resp = client.get("/api/messages?page=1&size=10")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["page"] == 1
    assert body["size"] == 10
    assert len(body["items"]) == 1
    assert body["items"][0]["msg_id"] == "m1"


def test_send_message_calls_platform_sender_with_target_id(monkeypatch):
    """Bug fix regression: req.aters must NOT be passed positionally as
    force_skip; it must reach the sender as `target_id`."""
    captured = {}

    async def fake_send_text(msg, receiver, **kwargs):
        captured["msg"] = msg
        captured["receiver"] = receiver
        captured["kwargs"] = kwargs
        return True

    fake_sender = SimpleNamespace(send_text=fake_send_text)
    fake_platform = SimpleNamespace(sender=fake_sender)
    monkeypatch.setattr(api_messages, "Platform").get = lambda: fake_platform

    app = _build_app()
    client = TestClient(app)

    resp = client.post(
        "/api/messages/send",
        json={"msg": "hello", "receiver": "wxid_x", "aters": "@wxid_y"},
    )
    assert resp.status_code == 200, resp.text
    assert captured["msg"] == "hello"
    assert captured["receiver"] == "wxid_x"
    # The critical assertion: aters reaches `target_id`, NEVER `force_skip`.
    assert "force_skip" not in captured["kwargs"]
    assert captured["kwargs"].get("target_id") == "@wxid_y"
    assert resp.json()["success"] is True


# ---------------------------------------------------------------------------
# statistics.py — 5 endpoints
# ---------------------------------------------------------------------------


def test_statistics_ranking_returns_period_and_data():
    svc = SimpleNamespace(get_ranking=_async_return([{"user_wxid": "u", "user_name": "U", "message_count": 9}]))
    app = _build_app(statistics_service=svc)
    client = TestClient(app)
    resp = client.get("/api/statistics/ranking?period=day&room_id=&limit=20")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["period"] == "day"
    assert body["ranking"][0]["message_count"] == 9


def test_statistics_timeline_returns_24h_distribution():
    svc = SimpleNamespace(get_timeline=_async_return([{"hour": 0, "count": 0}] * 24))
    app = _build_app(statistics_service=svc)
    client = TestClient(app)
    resp = client.get("/api/statistics/timeline?date=&room_id=")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["date"] == ""
    assert isinstance(body["timeline"], list)
    assert len(body["timeline"]) == 24


def test_statistics_keywords_rejects_bad_period():
    """The `pattern` constraint must reject periods other than day/week/month."""
    svc = SimpleNamespace(get_keywords=_async_return([]))
    app = _build_app(statistics_service=svc)
    client = TestClient(app)
    resp = client.get("/api/statistics/keywords?period=bogus")
    assert resp.status_code == 422


def test_statistics_generate_summary_returns_report_string(monkeypatch):
    async def _ranking(*_a, **_kw):
        return [{"user_wxid": "u", "user_name": "U", "message_count": 3}]

    async def _timeline(*_a, **_kw):
        return [{"hour": 10, "count": 5}]

    async def _keywords(*_a, **_kw):
        return [{"word": "hi", "count": 1, "score": 1.0}]

    async def _daily_report(*_a, **_kw):
        return "# markdown report"

    svc = SimpleNamespace(
        get_ranking=_ranking,
        get_timeline=_timeline,
        get_keywords=_keywords,
    )
    rs = SimpleNamespace(generate_daily_report=_daily_report)
    app = _build_app(statistics_service=svc, report_service=rs)
    client = TestClient(app)

    resp = client.post("/api/statistics/summary/generate?room_id=")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"summary": "# markdown report"}


def test_statistics_overview_calls_service_overview_and_composes_response():
    async def _overview():
        return {"total_messages": 100, "active_users": 7, "active_rooms": 4}

    async def _ranking(*_a, **_kw):
        return [{"user_wxid": "u", "user_name": "U", "message_count": 2}]

    async def _timeline(*_a, **_kw):
        return [{"hour": 9, "count": 2}]

    async def _keywords(*_a, **_kw):
        return [{"word": "hi", "count": 1, "score": 0.5}]

    svc = SimpleNamespace(
        get_overview=_overview,
        get_ranking=_ranking,
        get_timeline=_timeline,
        get_keywords=_keywords,
    )
    app = _build_app(statistics_service=svc)
    client = TestClient(app)
    resp = client.get("/api/statistics/overview")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_messages"] == 100
    assert body["active_users"] == 7
    assert body["active_rooms"] == 4
    assert isinstance(body["ranking"], list)
    assert isinstance(body["timeline"], list)
    assert isinstance(body["keywords"], list)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _async_return(value):
    """Make a tiny async-returning mock without importing unittest.mock here."""

    async def _coro(*_a, **_kw):
        return value

    return _coro
