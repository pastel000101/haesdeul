"""그래프 칸 값 도우미 — `app/api/primitives.py` 의 `three_ticks` · `spread_labels`.

2026-09-30 재구성 BL-019 전에는 재무 · 판매 화면이 글자까지 같은 몸통(`_ticks` · `_spread_labels`)을
한 벌씩 들고 있었다. 한 자리로 모으며 값을 잡아 둔다 — 두 화면이 같은 함수를 쓰는지도 본다.
"""

from __future__ import annotations

import pytest

from app.api import primitives
from app.api.finance import presenter as finance_presenter
from app.api.sales import presenter as sales_presenter


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (0, 5.0, [0, 2.5, 5.0]),
        (10, 25, [10, 17.5, 25]),
        (-3.2, 4.8, [-3.2, 0.8, 4.8]),
    ],
)
def test_three_ticks_are_bottom_middle_top(start, end, expected):
    assert primitives.three_ticks(start, end) == expected


@pytest.mark.parametrize(
    ("count", "visible"),
    [
        (0, []),
        (1, [0]),
        (2, [0, 1]),
        (3, [0, 2]),
        (15, [0, 6, 13, 14]),
        (30, [0, 6, 13, 20, 27, 29]),
    ],
)
def test_spread_labels_keep_first_last_and_every_seventh(count, visible):
    labels = [f"L{i}" for i in range(count)]

    spread = primitives.spread_labels(labels)

    assert len(spread) == count
    assert [i for i, label in enumerate(spread) if label] == visible
    assert all(spread[i] == labels[i] for i in visible)


def test_finance_and_sales_screens_share_the_chart_helpers():
    """두 화면이 다시 따로 들지 않는다 — 같은 함수 객체를 쓴다."""
    for screen in (finance_presenter, sales_presenter):
        assert screen.three_ticks is primitives.three_ticks
        assert screen.spread_labels is primitives.spread_labels
        assert not hasattr(screen, "_ticks") and not hasattr(screen, "_spread_labels")
