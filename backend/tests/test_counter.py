"""weizx — counter.py 测试。

模块级单 global 计数器：3 个测试，1 个 reset 收尾。
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ai import counter  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_counter():
    """每个测试前重置，避免测试间相互污染。"""
    counter.reset()
    yield
    counter.reset()


def test_increment_increases_count():
    assert counter.get_count() == 0
    counter.increment()
    assert counter.get_count() == 1
    counter.increment()
    counter.increment()
    assert counter.get_count() == 3


def test_reset_returns_to_zero():
    counter.increment()
    counter.increment()
    assert counter.get_count() == 2
    counter.reset()
    assert counter.get_count() == 0


def test_get_count_does_not_mutate():
    counter.increment()
    before = counter.get_count()
    counter.get_count()
    counter.get_count()
    assert counter.get_count() == before
