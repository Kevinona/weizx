"""FastAPI TestClient tests for /api/knowledge/* endpoints.

Uses an isolated FastAPI app so we only mount the knowledge router and override
the ``verify_token`` dependency. The ``VectorStoreManager`` (ChromaDB) and the
embedding model are mocked via monkeypatch — no real model load, no disk I/O.
"""

from __future__ import annotations

import os
import sys

# Allow ``from app...`` imports when pytest is invoked from backend/.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth import verify_token
from app.api.knowledge import router as knowledge_router


class FakeVectorStore:
    """In-memory replacement for ``VectorStoreManager``."""

    def __init__(self) -> None:
        self.docs = {
            "doc-1": {
                "id": "doc-1",
                "text": "阿七喜欢短句回复。",
                "metadata": {"topic": "manual", "priority": "high"},
            },
        }
        self.added: list[dict] = []
        self.deleted: list[str] = []

    def list_knowledge(self, limit: int = 100):
        return list(self.docs.values())[:limit]

    def add_knowledge(self, *, texts, embeddings, metadatas, ids=None):
        for text, emb, meta in zip(texts, embeddings, metadatas):
            doc_id = (ids or ["kb-auto"]).pop(0) if ids else f"auto-{len(self.docs)}"
            self.docs[doc_id] = {
                "id": doc_id,
                "text": text,
                "metadata": meta,
            }
            self.added.append({"text": text, "embedding": emb, "meta": meta})

    def delete_knowledge(self, doc_id: str) -> bool:
        self.deleted.append(doc_id)
        return self.docs.pop(doc_id, None) is not None


class FakeEmbeddingManager:
    def __init__(self, provider: str = "local"):
        self.provider = provider

    def embed_query(self, text: str):
        return [0.0] * 4  # deterministic, schema-wise valid vector


@pytest.fixture
def client(monkeypatch):
    store = FakeVectorStore()

    def _get_vs():
        return store

    def _get_em(provider: str = "local"):
        return FakeEmbeddingManager(provider=provider)

    monkeypatch.setattr("app.ai.vector_store.get_vector_store", _get_vs)
    monkeypatch.setattr(
        "app.ai.embeddings.get_embedding_manager", _get_em
    )

    app = FastAPI()
    app.include_router(knowledge_router)
    # Skip JWT verification in tests.
    app.dependency_overrides[verify_token] = lambda: {"sub": "tester"}

    return TestClient(app), store


def test_list_knowledge_returns_documents(client):
    test_client, store = client

    response = test_client.get("/api/knowledge")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["documents"][0]["id"] == "doc-1"
    assert body["documents"][0]["topic"] == "manual"
    assert body["documents"][0]["priority"] == "high"


def test_add_knowledge_stores_document(client):
    test_client, store = client
    payload = {
        "text": "我喜欢简短的口语化回复。",
        "topic": "manual",
        "priority": "high",
    }

    response = test_client.post("/api/knowledge/add", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert len(store.added) == 1
    assert store.added[0]["text"] == payload["text"]
    assert store.added[0]["meta"]["topic"] == payload["topic"]
    assert store.added[0]["meta"]["priority"] == payload["priority"]


def test_delete_knowledge_removes_document(client):
    test_client, store = client

    response = test_client.delete("/api/knowledge/doc-1")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert "已删除" in body["message"]
    assert "doc-1" in store.deleted
    assert "doc-1" not in store.docs
