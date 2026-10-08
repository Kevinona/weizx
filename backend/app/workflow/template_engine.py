"""
Template engine for rendering message templates.

Supports four template types:
  - text  : plain text with ``{var_name}`` placeholders
  - card  : decorative card with borders
  - form  : structured form layout
  - list  : bullet list layout
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import MessageTemplate

logger = logging.getLogger(__name__)

# Decorative border characters
_CARD_CORNER_TL = "+"
_CARD_CORNER_TR = "+"
_CARD_CORNER_BL = "+"
_CARD_CORNER_BR = "+"
_CARD_H = "-"
_CARD_V = "|"
_CARD_WIDTH = 40


class TemplateEngine:
    """Loads and renders message templates from the database.

    Templates are stored in the ``message_templates`` table and rendered
    by substituting ``{var_name}`` placeholders with values from the
    *variables* dict.  Missing variables are silently replaced with an
    empty string.
    """

    def __init__(self, session_factory: Any = None) -> None:
        self._session_factory = session_factory
        self._cache: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def load_templates(self, session: AsyncSession | None = None) -> None:
        """Pre-load all templates into the in-memory cache."""
        if session is not None:
            await self._load(session)
        elif self._session_factory is not None:
            async with self._session_factory() as sess:
                await self._load(sess)
        else:
            logger.error("No session or session_factory available; cannot load templates.")

    async def render(
        self,
        template_name: str,
        variables: dict[str, Any] | None = None,
        session: AsyncSession | None = None,
    ) -> str:
        """Render a template by name.

        Args:
            template_name: Unique name of the template in the database.
            variables: Key-value pairs used to substitute ``{key}`` placeholders.
            session: Optional explicit session for lazy loading.

        Returns:
            The rendered string.  Returns an empty string if the template
            is not found.
        """
        tpl = await self._get_template(template_name, session=session)
        if tpl is None:
            logger.warning("Template not found: %s", template_name)
            return ""

        return self._render_content(tpl, variables or {})

    async def render_card(
        self,
        template_name: str,
        variables: dict[str, Any] | None = None,
        session: AsyncSession | None = None,
    ) -> str:
        """Render a template with a decorative card border.

        The output looks like::

            +--------------------------------------+
            |               TITLE                  |
            |                                      |
            |         content line 1               |
            |         content line 2               |
            +--------------------------------------+
        """
        tpl = await self._get_template(template_name, session=session)
        if tpl is None:
            logger.warning("Template not found: %s", template_name)
            return ""

        return self._render_card_content(tpl, variables or {})

    async def render_form(
        self,
        template_name: str,
        variables: dict[str, Any] | None = None,
        session: AsyncSession | None = None,
    ) -> str:
        """Render a form-type template.

        The output uses a label:value layout::

            ---- FORM ----
            游戏: {game}
            段位: {rank}
            时长: {hours} 小时
            预算: {budget} 元
            备注: {notes}
            --------------
        """
        tpl = await self._get_template(template_name, session=session)
        if tpl is None:
            logger.warning("Template not found: %s", template_name)
            return ""

        return self._render_form_content(tpl, variables or {})

    async def render_list(
        self,
        template_name: str,
        variables: dict[str, Any] | None = None,
        session: AsyncSession | None = None,
    ) -> str:
        """Render a list-type template with bullet points."""
        tpl = await self._get_template(template_name, session=session)
        if tpl is None:
            logger.warning("Template not found: %s", template_name)
            return ""

        return self._render_list_content(tpl, variables or {})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_template(
        self, name: str, session: AsyncSession | None = None
    ) -> dict[str, Any] | None:
        """Retrieve a template from cache or database."""
        if name in self._cache:
            return self._cache[name]

        # Lazily load all templates if cache is empty.
        if not self._cache and session is not None:
            await self._load(session)

        return self._cache.get(name)

    async def _load(self, session: AsyncSession) -> None:
        """Load all templates from the database into cache."""
        result = await session.execute(select(MessageTemplate))
        rows = result.scalars().all()
        self._cache = {
            r.name: {
                "id": r.id,
                "name": r.name,
                "type": r.type,
                "title": r.title or "",
                "content": r.content or "",
                "footer": r.footer or "",
            }
            for r in rows
        }
        logger.info("Loaded %d message templates into cache", len(self._cache))

    def _render_content(self, tpl: dict[str, Any], variables: dict[str, Any]) -> str:
        """Render a template based on its type."""
        tpl_type = tpl.get("type", "text")
        if tpl_type == "card":
            return self._render_card_content(tpl, variables)
        elif tpl_type == "form":
            return self._render_form_content(tpl, variables)
        elif tpl_type == "list":
            return self._render_list_content(tpl, variables)
        else:  # plain text
            return self._render_text_content(tpl, variables)

    def _render_text_content(self, tpl: dict[str, Any], variables: dict[str, Any]) -> str:
        """Render plain-text template."""
        parts = []
        if tpl.get("title"):
            parts.append(_safe_format(tpl["title"], variables))
        if tpl.get("content"):
            parts.append(_safe_format(tpl["content"], variables))
        if tpl.get("footer"):
            parts.append(_safe_format(tpl["footer"], variables))
        return "\n".join(parts)

    def _render_card_content(self, tpl: dict[str, Any], variables: dict[str, Any]) -> str:
        """Render a card with decorative ASCII borders."""
        title = _safe_format(tpl.get("title") or "", variables)
        content = _safe_format(tpl.get("content") or "", variables)
        footer = _safe_format(tpl.get("footer") or "", variables)

        w = _CARD_WIDTH
        hr = _CARD_CORNER_TL + _CARD_H * w + _CARD_CORNER_TR

        lines: list[str] = [hr]

        if title:
            lines.append(f"{_CARD_V}{title.center(w)}{_CARD_V}")
            lines.append(f"{_CARD_V}{' ' * w}{_CARD_V}")

        for line in content.split("\n"):
            # Wrap long lines
            while len(line) > w:
                lines.append(f"{_CARD_V}{line[:w]:<{w}}{_CARD_V}")
                line = line[w:]
            lines.append(f"{_CARD_V}{line:<{w}}{_CARD_V}")

        if footer:
            lines.append(f"{_CARD_V}{' ' * w}{_CARD_V}")
            lines.append(f"{_CARD_V}{footer.center(w)}{_CARD_V}")

        lines.append(_CARD_CORNER_BL + _CARD_H * w + _CARD_CORNER_BR)
        return "\n".join(lines)

    def _render_form_content(self, tpl: dict[str, Any], variables: dict[str, Any]) -> str:
        """Render form-type template with label-value layout."""
        title = _safe_format(tpl.get("title") or "---- FORM ----", variables)
        content = _safe_format(tpl.get("content") or "", variables)
        footer = _safe_format(tpl.get("footer") or "-------------", variables)

        lines: list[str] = [title]
        if content:
            lines.append(content)
        lines.append(footer)
        return "\n".join(lines)

    def _render_list_content(self, tpl: dict[str, Any], variables: dict[str, Any]) -> str:
        """Render list-type template with bullet points."""
        title = _safe_format(tpl.get("title") or "", variables)
        content = _safe_format(tpl.get("content") or "", variables)
        footer = _safe_format(tpl.get("footer") or "", variables)

        lines: list[str] = []
        if title:
            lines.append(title)
            lines.append("")
        if content:
            # Each non-empty content line gets a bullet.
            for line in content.split("\n"):
                stripped = line.strip()
                if stripped:
                    lines.append(f"  - {stripped}")
                else:
                    lines.append("")
        if footer:
            lines.append("")
            lines.append(footer)
        return "\n".join(lines)


class _SafeDict(dict):
    """Dict subclass that returns ``""`` for missing keys.

    Used with ``str.format_map`` to prevent ``KeyError`` during template
    rendering when a referenced variable is absent from *variables*.
    """

    def __missing__(self, key: str) -> str:
        return ""


def _safe_format(text: str, variables: dict[str, Any]) -> str:
    """``str.format_map`` wrapper that tolerates stray braces.

    ``format_map`` raises ``ValueError`` on a stray ``{`` or ``}`` in the
    template text (e.g. literal JSON, C-style braces, broken copy/paste).
    For user-authored templates we degrade gracefully by returning the
    raw text instead of crashing the render call.
    """
    if not text:
        return text or ""
    try:
        return text.format_map(_SafeDict(variables))
    except (ValueError, KeyError, IndexError, TypeError):
        return text
