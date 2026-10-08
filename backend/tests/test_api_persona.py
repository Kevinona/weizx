"""FastAPI TestClient tests for /api/persona/* endpoints.

Uses an isolated FastAPI app so we only mount the persona router and override
the ``verify_token`` dependency. All upstream services (``StyleDistiller``,
``Platform``/key extractor, ``MacOSDBReader``) are mocked via monkeypatch to
keep the test suite hermetic — no WeChat DB, no real LLM, no disk I/O.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

# Allow ``from app...`` imports when pytest is invoked from backend/.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth import verify_token
from app.api.persona import router as persona_router


SKILL = {
    "meta": {"name": "测试名"},
    "self_memory_md": "## Self Memory\n\n重视自由。",
    "persona_md": "## Persona\n\n短句。",
    "runtime_prompt_private": "你正在作为测试名本人的微信镜像回复。",
    "runtime_prompt_group": "你仍然是「测试名」微信助手。",
    "mode": "contextual",
}


class FakeDistiller:
    """Drop-in replacement for ``app.ai.style_distiller.StyleDistiller``."""

    cleared = False

    def __init__(self) -> None:
        self.has_persona = True
        self.mode = SKILL["mode"]
        self.meta = SKILL["meta"]
        self.self_memory_md = SKILL["self_memory_md"]
        self.persona_md = SKILL["persona_md"]

    def build_prompt(self, is_group: bool = False) -> str:
        return SKILL["runtime_prompt_group" if is_group else "runtime_prompt_private"]

    async def analyze(self, messages, force: bool = False):
        return SKILL

    def save_edits(
        self,
        *,
        self_memory_md=None,
        persona_md=None,
        runtime_prompt_private=None,
        runtime_prompt_group=None,
        meta=None,
        mode=None,
    ):
        return {
            "mode": mode or SKILL["mode"],
            "meta": meta or SKILL["meta"],
            "self_memory_md": self_memory_md or SKILL["self_memory_md"],
            "persona_md": persona_md or SKILL["persona_md"],
            "runtime_prompt_private": runtime_prompt_private
            or SKILL["runtime_prompt_private"],
            "runtime_prompt_group": runtime_prompt_group
            or SKILL["runtime_prompt_group"],
        }

    def clear_cache(self) -> None:
        FakeDistiller.cleared = True


class FakeDBReader:
    def find_database_files(self):
        return ["/tmp/message_0.db"]

    def open_db(self, path, key):
        return None

    def get_my_messages(self, limit: int, since_days: int):
        return [{"content": "你好"}, {"content": "确实"}]

    def close(self):
        return None


@pytest.fixture
def client(monkeypatch):
    # Mock all upstream services the persona endpoint lazily imports.
    monkeypatch.setattr("app.ai.style_distiller.StyleDistiller", FakeDistiller)
    monkeypatch.setattr(
        "app.core.platform.Platform.get",
        lambda: SimpleNamespace(
            key_extractor=SimpleNamespace(
                load_keys=lambda: {"message_0.db": "aa" * 32},
            ),
        ),
    )
    monkeypatch.setattr("app.core.db_reader_macos.MacOSDBReader", FakeDBReader)

    FakeDistiller.cleared = False

    app = FastAPI()
    app.include_router(persona_router)
    # Skip JWT verification in tests.
    app.dependency_overrides[verify_token] = lambda: {"sub": "tester"}

    return TestClient(app)


def test_get_persona_returns_skill_preview(client):
    response = client.get("/api/persona")

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["mode"] == SKILL["mode"]
    assert body["private_prompt"] == SKILL["runtime_prompt_private"]
    assert body["group_prompt"] == SKILL["runtime_prompt_group"]
    assert body["meta"] == SKILL["meta"]


def test_analyze_persona_returns_extracted_persona(client):
    response = client.post("/api/persona/analyze", params={"force": True})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["total_messages"] == 2
    assert body["sample_size"] == 2
    assert body["private_prompt"] == SKILL["runtime_prompt_private"]
    assert body["group_prompt"] == SKILL["runtime_prompt_group"]


def test_update_persona_saves_manual_edits(client):
    payload = {
        "meta": {"name": "手动版"},
        "self_memory": "## Self Memory\n\n人工记忆",
        "persona": "## Persona\n\n人工风格",
        "private_prompt": "私聊人工 prompt",
        "group_prompt": "群聊人工 prompt",
        "mode": "contextual",
    }

    response = client.put("/api/persona", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["ready"] is True
    assert body["meta"] == payload["meta"]
    assert body["self_memory"] == payload["self_memory"]
    assert body["persona"] == payload["persona"]
    assert body["private_prompt"] == payload["private_prompt"]
    assert body["group_prompt"] == payload["group_prompt"]


def test_clear_persona_resets_cached_skill(client):
    response = client.delete("/api/persona")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert "默认" in body["message"]
    assert FakeDistiller.cleared is True
