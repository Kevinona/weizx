"""RuleEngine 三层匹配策略的单元测试。

覆盖范围：
  - keyword / regex / intent 三个 tier 各自的命中路径
  - 无任何命中时的兜底返回（matched=False）
  - keyword > regex > intent 的优先级
  - 边界：空消息 / 非法正则 pattern

设计原则：
  - 直接构造 engine._rules，绕开 SQLAlchemy / AutoReplyRule 模型加载
  - 不导入 langchain / pydantic / aiosqlite 等重型依赖
  - async 方法走 @pytest.mark.asyncio，同步 tier 方法直接调用
"""

from __future__ import annotations

import pytest

from app.workflow.rule_engine import RuleEngine


# -------------------- helpers --------------------


def _rule(
    *,
    name: str,
    type_: str,
    patterns: list,
    reply: str = "",
    workflow: str = "",
    trigger_intents: list | None = None,
    enabled: bool = True,
) -> dict:
    """构造一条与 _load() 输出格式一致的 rule 字典。

    字段必须与 rule_engine.RuleEngine._load() 的输出完全对齐，否则
    keyword_match / regex_match / intent_match 会因字段缺失而短路。
    """
    return {
        "id": hash(name) & 0x7FFFFFFF,
        "name": name,
        "type": type_,
        "patterns": patterns,
        "trigger_intents": trigger_intents if trigger_intents is not None else [],
        "reply": reply,
        "workflow": workflow,
        "priority": 0,
        "enabled": enabled,
    }


def _engine(*rules: dict) -> RuleEngine:
    """返回一个已注入规则的 RuleEngine，跳过数据库加载。"""
    eng = RuleEngine()
    eng._rules = list(rules)
    return eng


# -------------------- Tier 1: keyword --------------------


def test_keyword_match_returns_hit_when_msg_equals_keyword():
    """keyword tier：精确匹配（大小写不敏感、首尾空白不敏感）。"""
    eng = _engine(
        _rule(
            name="kw-hello",
            type_="keyword",
            patterns=["hello", "hi"],
            reply="你好",
            workflow="wf_kw",
        )
    )

    result = eng.keyword_match("HELLO")

    assert result["matched"] is True
    assert result["reply"] == "你好"
    assert result["workflow"] == "wf_kw"
    assert result["rule"]["name"] == "kw-hello"


def test_keyword_match_returns_miss_for_substring():
    """keyword tier 只接受整词相等，不能是子串匹配（与 regex 的 search 不同）。"""
    eng = _engine(
        _rule(name="kw-hi", type_="keyword", patterns=["hi"], reply="hi reply")
    )

    assert eng.keyword_match("history")["matched"] is False
    assert eng.keyword_match("hi")["matched"] is True


# -------------------- Tier 2: regex --------------------


def test_regex_match_extracts_named_group_into_reply_template():
    """regex tier：named group 注入 reply 模板，未捕获的占位符不报错。"""
    eng = _engine(
        _rule(
            name="rx-weather",
            type_="regex",
            patterns=[r"weather in (?P<city>\w+)"],
            reply="Looking up {city}... ({missing} OK)",
            workflow="wf_rx",
        )
    )

    result = eng.regex_match("weather in Paris")

    assert result["matched"] is True
    assert result["reply"] == "Looking up Paris... ( OK)"
    assert result["variables"]["city"] == "Paris"
    assert result["workflow"] == "wf_rx"


# -------------------- Tier 3: intent --------------------


def test_intent_match_detects_default_intent_keyword_in_message():
    """intent tier：消息含 _DEFAULT_INTENTS['order'] 的关键词 → 命中。"""
    eng = _engine(
        _rule(
            name="i-order",
            type_="intent",
            patterns=["order"],          # intent 名
            reply="已收到 {intent}",
            workflow="wf_intent",
        )
    )

    result = eng.intent_match("我想点单")

    assert result["matched"] is True
    assert result["intent"] == "order"
    assert "order" in result["reply"]
    assert result["workflow"] == "wf_intent"


def test_intent_match_prefers_trigger_intents_over_patterns():
    """intent tier：trigger_intents 字段存在时优先于 patterns。"""
    eng = _engine(
        _rule(
            name="i-mixed",
            type_="intent",
            patterns=["greet"],          # 不应生效
            trigger_intents=["help"],    # 应当生效
            reply="{intent}",
        )
    )

    result = eng.intent_match("请问怎么用")

    assert result["matched"] is True
    assert result["intent"] == "help"


# -------------------- fallback --------------------


@pytest.mark.asyncio
async def test_match_falls_back_to_unmatched_when_no_rule_fires():
    """没有任何 tier 命中时，match 返回 matched=False 的兜底字典。"""
    eng = _engine(
        _rule(name="kw", type_="keyword", patterns=["hello"], reply="x"),
        _rule(name="rx", type_="regex", patterns=[r"foo\d+"], reply="y"),
        _rule(name="i",  type_="intent", patterns=["order"], reply="z"),
    )

    result = await eng.match("random gibberish xyz")

    assert result == {"matched": False, "reply": "", "rule": {}, "workflow": ""}


# -------------------- priority --------------------


@pytest.mark.asyncio
async def test_match_priority_keyword_beats_regex_beats_intent():
    """同一消息被三层均能命中时，必须按 keyword > regex > intent 顺序返回。"""
    eng = _engine(
        _rule(name="i",  type_="intent",  patterns=["order"], reply="INTENT"),
        _rule(name="rx", type_="regex",    patterns=[r"order"], reply="REGEX"),
        _rule(name="kw", type_="keyword",  patterns=["order"], reply="KEYWORD"),
    )

    result = await eng.match("order")

    assert result["matched"] is True
    assert result["reply"] == "KEYWORD"
    assert result["rule"]["name"] == "kw"


# -------------------- edge cases --------------------


def test_keyword_match_handles_empty_input_gracefully():
    """空消息不能误命中任何非空 keyword。"""
    eng = _engine(
        _rule(name="kw", type_="keyword", patterns=["hello", "hi"], reply="r"),
        _rule(name="kw-empty", type_="keyword", patterns=[""], reply="empty reply"),
    )

    # 空串与空 keyword 相等：仍命中那条规则，但这是显式配置的语义，不算 bug。
    result = eng.keyword_match("")
    assert result["matched"] is True
    assert result["rule"]["name"] == "kw-empty"

    # 纯空白：经过 match() 的 strip 后是空串，等价于上面；这里验证
    # keyword_match 自身对非空但无匹配的消息返回 miss。
    assert eng.keyword_match("goodbye")["matched"] is False


def test_regex_match_skips_malformed_pattern_without_crashing():
    """一条 regex 规则里混着非法 pattern，不能让整个 regex_match 抛异常。

    这是 defensive fix 的回归测试：原始 list comprehension 在 re.error
    时会向外抛出，导致任何消息（包括走 keyword / intent tier 的消息）
    在没有更早命中的情况下都会崩溃。
    """
    eng = _engine(
        _rule(
            name="rx-mixed",
            type_="regex",
            # 第一个 pattern 非法（未闭合的分组），第二个合法
            patterns=[r"(", r"hello (?P<name>\w+)"],
            reply="hi {name}",
            workflow="wf_rx",
        )
    )

    # 必须不抛
    result = eng.regex_match("hello alice")

    assert result["matched"] is True
    assert result["reply"] == "hi alice"
    assert result["variables"]["name"] == "alice"


# -------------------- bonus: disabled rules --------------------


def test_disabled_rules_are_ignored():
    """enabled=False 的规则在任何 tier 里都应被跳过。"""
    eng = _engine(
        _rule(name="kw-off", type_="keyword", patterns=["hello"], reply="x", enabled=False),
        _rule(name="rx-off", type_="regex",   patterns=[r"hello"], reply="y", enabled=False),
        _rule(name="i-off",  type_="intent",  patterns=["order"], reply="z", enabled=False),
    )

    assert eng.keyword_match("hello")["matched"] is False
    assert eng.regex_match("hello")["matched"] is False
    assert eng.intent_match("点单")["matched"] is False
