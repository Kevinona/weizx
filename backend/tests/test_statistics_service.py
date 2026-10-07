"""StatisticsService 单元测试。

被测模块: backend/app/services/statistics_service.py

不依赖真实数据库 —— 用最小 AsyncSession 桩把 SQLAlchemy 结果换成内存里的行。
不依赖 jieba —— 环境内通常未安装，生产代码会走 regex 分支。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# -------------------- fakes --------------------


class _FakeResult:
    """模拟 SQLAlchemy AsyncResult。

    - `__iter__` 用于 `for r in result`
    - `scalar()` 用于 `result.scalar()`，返回首列；空结果返回 None
    """

    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def scalar(self):
        if not self._rows:
            return None
        first = self._rows[0]
        if isinstance(first, (list, tuple)):
            return first[0]
        return first


class _FakeSession:
    """最小 AsyncSession 桩。

    按顺序消费预先排好队的 execute 结果。
    记录 `add` / `commit` 用于验证写入路径。
    """

    def __init__(self, execute_results):
        self._queue = list(execute_results)
        self._idx = 0
        self.added = []
        self.committed = False

    async def execute(self, query):  # noqa: ARG002
        if self._idx < len(self._queue):
            r = self._queue[self._idx]
            self._idx += 1
            return r
        return _FakeResult([])

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True


# -------------------- 1. speaker ranking --------------------


@pytest.mark.asyncio
async def test_get_ranking_orders_speakers_by_message_count_desc():
    """get_ranking: 按 message_count 降序返回 wxid / name / count。"""
    from app.services.statistics_service import StatisticsService

    rows = [
        ("wxid_b", "Bob", 5),
        ("wxid_a", "Alice", 10),
        ("wxid_c", "Charlie", 3),
    ]
    session = _FakeSession([_FakeResult(rows)])
    svc = StatisticsService(session)

    ranking = await svc.get_ranking(period="day")

    assert [r["user_wxid"] for r in ranking] == ["wxid_a", "wxid_b", "wxid_c"]
    assert ranking[0] == {
        "user_wxid": "wxid_a",
        "user_name": "Alice",
        "message_count": 10,
    }


@pytest.mark.asyncio
async def test_get_ranking_falls_back_to_wxid_when_sender_name_null():
    """edge case: sender_name 为 None 时必须回退到 wxid，避免前端展示 None。"""
    from app.services.statistics_service import StatisticsService

    rows = [
        ("wxid_anon", None, 1),
    ]
    session = _FakeSession([_FakeResult(rows)])
    svc = StatisticsService(session)

    ranking = await svc.get_ranking(period="day")

    assert ranking[0]["user_name"] == "wxid_anon"


# -------------------- 2. time heatmap --------------------


@pytest.mark.asyncio
async def test_get_timeline_returns_all_24_hours_filling_missing_with_zero():
    """get_timeline: 即使 DB 只返回部分小时，也必须返回完整的 24 桶，缺失桶为 0。"""
    from app.services.statistics_service import StatisticsService

    rows = [
        (3, 7),
        (10, 4),
    ]
    session = _FakeSession([_FakeResult(rows)])
    svc = StatisticsService(session)

    timeline = await svc.get_timeline(date="2024-01-15")

    assert len(timeline) == 24
    assert [h["hour"] for h in timeline] == list(range(24))
    assert timeline[3]["count"] == 7
    assert timeline[10]["count"] == 4
    assert timeline[0]["count"] == 0
    assert timeline[23]["count"] == 0


# -------------------- 3. TF-IDF keywords --------------------


@pytest.mark.asyncio
async def test_get_keywords_extracts_top_words_and_drops_stop_words():
    """get_keywords: 提取词、过滤停用词、按频次排序，返回 count+score。

    不依赖 jieba 是否安装 —— 测试只断言两条路径共有的不变量:
    - schema 完整且类型正确
    - 停用词被过滤
    - 词长度 ≥ 2（单字过滤）
    - 按 count 降序排列
    """
    from app.services.statistics_service import StatisticsService

    rows = [
        ("今天天气很好",),
        ("今天我们去郊游",),
        ("天气真好啊",),
    ]
    session = _FakeSession([_FakeResult(rows)])
    svc = StatisticsService(session)

    keywords = await svc.get_keywords(period="week")

    # 非空 + 每条都加下了必要字段
    assert len(keywords) > 0
    for k in keywords:
        assert set(k.keys()) == {"word", "count", "score"}
        assert isinstance(k["word"], str) and len(k["word"]) >= 2
        assert isinstance(k["count"], int) and k["count"] >= 1
        assert 0 < k["score"] <= 1

    # 停用词必须过滤掉
    words = {k["word"] for k in keywords}
    assert words.isdisjoint({"的", "了", "是", "很", "啊", "我", "你", "他"})

    # 按 count 降序排列（Counter.most_common 保证）
    counts = [k["count"] for k in keywords]
    assert counts == sorted(counts, reverse=True)


# -------------------- 4. AI summary (overview) --------------------


@pytest.mark.asyncio
async def test_get_overview_returns_summary_dict_with_three_counts():
    """get_overview: 返回 total_messages / active_users / active_rooms 三项概览。"""
    from app.services.statistics_service import StatisticsService

    # get_overview 内部串行调用三次 execute
    session = _FakeSession(
        [
            _FakeResult([(100,)]),  # total_messages
            _FakeResult([(5,)]),    # active_users
            _FakeResult([(2,)]),    # active_rooms
        ]
    )
    svc = StatisticsService(session)

    overview = await svc.get_overview()

    assert overview == {
        "total_messages": 100,
        "active_users": 5,
        "active_rooms": 2,
    }


@pytest.mark.asyncio
async def test_get_overview_empty_db_returns_zero_dict():
    """edge case: 空数据库时 scalar() 为 None，须通过 `or 0` 回退。"""
    from app.services.statistics_service import StatisticsService

    session = _FakeSession(
        [
            _FakeResult([]),
            _FakeResult([]),
            _FakeResult([]),
        ]
    )
    svc = StatisticsService(session)

    overview = await svc.get_overview()

    assert overview == {
        "total_messages": 0,
        "active_users": 0,
        "active_rooms": 0,
    }


# -------------------- 5. empty data / edge cases --------------------


@pytest.mark.asyncio
async def test_get_keywords_empty_corpus_returns_empty_list():
    """empty data: 语料库为空时返回 [] 而非崩溃。"""
    from app.services.statistics_service import StatisticsService

    session = _FakeSession([_FakeResult([])])
    svc = StatisticsService(session)

    keywords = await svc.get_keywords(period="week")

    assert keywords == []


@pytest.mark.asyncio
async def test_get_keywords_single_char_stop_word_returns_empty_list():
    """edge case: 单字 CJK 输入在两条路径下都被过滤 → 空列表，不除零崩溃。

    - jieba 路径: 切出 ["的"]，len("的")=1 被 length 过滤掉
    - regex 路径: [一-鿿]{2,} 要求 ≥2 字符，单字不命中
    """
    from app.services.statistics_service import StatisticsService

    rows = [
        ("的",),
    ]
    session = _FakeSession([_FakeResult(rows)])
    svc = StatisticsService(session)

    keywords = await svc.get_keywords(period="week")

    assert keywords == []


@pytest.mark.asyncio
async def test_get_timeline_with_no_messages_returns_all_zeros():
    """empty data: 数据库无消息时仍返回 24 个桶，全 0。"""
    from app.services.statistics_service import StatisticsService

    session = _FakeSession([_FakeResult([])])
    svc = StatisticsService(session)

    timeline = await svc.get_timeline(date="2024-01-15")

    assert len(timeline) == 24
    assert all(h["count"] == 0 for h in timeline)