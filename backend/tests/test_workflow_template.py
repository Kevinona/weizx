"""weizx — workflow/template_engine.py 测试。

覆盖点：
- TemplateEngine.load_templates：显式 session / session_factory / 无可用退化
- TemplateEngine.render：text 类型、缺失变量降级为 ""
- TemplateEngine.render_card：ASCII 边框 + title/content/footer
- TemplateEngine.render_form：label/value + 默认 title/footer
- TemplateEngine.render_list：bullet 前缀
- 模板未找到统一返回 ""
- Bug T2 (回归): stray '{' 导致 ``format_map`` 抛 ValueError 时，
  _safe_format 把内容降级为原文，不应崩溃
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.workflow.template_engine import (  # noqa: E402
    TemplateEngine,
    _SafeDict,
    _safe_format,
    _CARD_CORNER_TL,
    _CARD_H,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeAsyncSession:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, stmt):  # noqa: ARG002
        return _FakeResult(self.rows)


def _row(id=1, name="hello", type_="text", title="", content="", footer=""):
    """MessageTemplate 行替代品."""
    return SimpleNamespace(
        id=id, name=name, type=type_, title=title,
        content=content, footer=footer,
    )


# ---------------------------------------------------------------------------
# load_templates
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_load_templates_with_explicit_session():
    rows = [
        _row(name="a", type_="text", content="hi {name}"),
        _row(id=2, name="b", type_="card", title="TITLE"),
    ]
    engine = TemplateEngine()
    await engine.load_templates(session=_FakeAsyncSession(rows))
    assert set(engine._cache) == {"a", "b"}
    assert engine._cache["a"]["type"] == "text"
    assert engine._cache["b"]["title"] == "TITLE"


@pytest.mark.asyncio
async def test_load_templates_with_session_factory():
    rows = [_row(name="x")]
    cm = AsyncMock()
    cm.__aenter__.return_value = _FakeAsyncSession(rows)
    cm.__aexit__.return_value = None
    factory = MagicMock(return_value=cm)

    engine = TemplateEngine(session_factory=factory)
    await engine.load_templates()

    factory.assert_called_once()
    assert "x" in engine._cache


@pytest.mark.asyncio
async def test_load_templates_without_session_logs_error(caplog):
    engine = TemplateEngine()
    with caplog.at_level("ERROR"):
        await engine.load_templates()
    assert engine._cache == {}
    assert any("cannot load templates" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# render (text)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_render_text_replaces_known_and_missing_vars():
    rows = [_row(name="hello", type_="text",
                 content="Hi {name}, code={code}")]
    engine = TemplateEngine()
    await engine.load_templates(session=_FakeAsyncSession(rows))
    out = await engine.render("hello", {"name": "Kevin"})
    assert "Kevin" in out
    # 缺失的 {code} 必须降级为 "" 而不是 KeyError
    assert "Hi Kevin, code=" in out


@pytest.mark.asyncio
async def test_render_template_not_found_returns_empty():
    engine = TemplateEngine()
    out = await engine.render("nonexistent", {"a": 1})
    assert out == ""


@pytest.mark.asyncio
async def test_render_text_tolerates_stray_brace_bug():
    """Bug T2 regression: content 里出现 unmatched '{' 不应让 render 抛 ValueError."""
    rows = [_row(name="broken", type_="text",
                 content="Hello {name, the time is {now")]
    engine = TemplateEngine()
    await engine.load_templates(session=_FakeAsyncSession(rows))
    # 在修复前这里会抛 ValueError
    out = await engine.render("broken", {"name": "Kevin"})
    assert isinstance(out, str)
    # 降级渲染保留原文（即 {name 仍以字面形式出现）
    assert "Hello" in out


# ---------------------------------------------------------------------------
# render_card
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_render_card_contains_borders_and_content():
    rows = [_row(name="greet", type_="card",
                 title="HELLO", content="line one\nline two")]
    engine = TemplateEngine()
    await engine.load_templates(session=_FakeAsyncSession(rows))
    out = await engine.render_card("greet", {})
    # 边框字符都在
    assert _CARD_CORNER_TL in out and _CARD_H in out
    assert "|" in out and "+" in out
    # title 和 content 都进了框内
    assert "HELLO" in out
    assert "line one" in out
    assert "line two" in out


@pytest.mark.asyncio
async def test_render_card_not_found_returns_empty():
    engine = TemplateEngine()
    assert await engine.render_card("missing", {}) == ""


@pytest.mark.asyncio
async def test_render_card_with_long_line_wraps():
    """边界: content 单行超过 _CARD_WIDTH 时换行, 不应死循环."""
    rows = [_row(name="wrap", type_="card", content="x" * 200)]
    engine = TemplateEngine()
    await engine.load_templates(session=_FakeAsyncSession(rows))
    # 必须能在合理时间内完成
    out = await engine.render_card("wrap", {})
    # 多行被切片后输出
    assert out.count("|" + "x" * 39 + "x" + "|") >= 1  # 至少有一段被完整填满宽度的行


# ---------------------------------------------------------------------------
# render_form
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_render_form_uses_default_title_and_footer():
    rows = [_row(name="ord", type_="form", content="游戏: {game}")]
    engine = TemplateEngine()
    await engine.load_templates(session=_FakeAsyncSession(rows))
    out = await engine.render_form("ord", {"game": "王者荣耀"})
    # 默认 title 和 footer
    assert "---- FORM ----" in out
    assert "-------------" in out
    assert "王者荣耀" in out


@pytest.mark.asyncio
async def test_render_form_with_explicit_title_footer():
    rows = [_row(name="ord", type_="form",
                 title="== ORDER ==", content="段位: {rank}", footer="== END ==")]
    engine = TemplateEngine()
    await engine.load_templates(session=_FakeAsyncSession(rows))
    out = await engine.render_form("ord", {"rank": "王者"})
    assert out.startswith("== ORDER ==")
    assert "王者" in out
    assert out.endswith("== END ==")


# ---------------------------------------------------------------------------
# render_list
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_render_list_bullets_each_line():
    rows = [_row(name="opts", type_="list", content="陪玩\n代练\n点单")]
    engine = TemplateEngine()
    await engine.load_templates(session=_FakeAsyncSession(rows))
    out = await engine.render_list("opts", {})
    assert "- 陪玩" in out
    assert "- 代练" in out
    assert "- 点单" in out


@pytest.mark.asyncio
async def test_render_list_skips_blank_lines():
    rows = [_row(name="opts", type_="list", content="陪玩\n\n代练")]
    engine = TemplateEngine()
    await engine.load_templates(session=_FakeAsyncSession(rows))
    out = await engine.render_list("opts", {})
    assert "- 陪玩" in out
    assert "- 代练" in out
    # 空行不应产生 "- " 前缀
    assert "- \n" not in out


@pytest.mark.asyncio
async def test_render_list_not_found_returns_empty():
    engine = TemplateEngine()
    assert await engine.render_list("nope", {}) == ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def test_safe_dict_missing_key_returns_empty():
    sd = _SafeDict({"x": 1})
    assert sd["x"] == 1
    assert sd["anything"] == ""


def test_safe_format_stray_brace_returns_raw_text():
    out = _safe_format("Hello {name", {"name": "Kevin"})
    # {name 没闭合 -> format_map 抛 ValueError -> 回退到原文
    assert out == "Hello {name"


def test_safe_format_succeeds_for_well_formed_text():
    out = _safe_format("Hi {name}, code={code}", {"name": "K"})
    assert out == "Hi K, code="


def test_safe_format_empty_text_returns_empty():
    assert _safe_format("", {"a": 1}) == ""
