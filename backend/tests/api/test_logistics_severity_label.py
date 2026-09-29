"""물류 화면이 그날 우선도를 **사람 말로** 적는 자리 — `api/logistics/query._severity_label`.

2026-09-29 (재구성 BL-012) 에 `_severity_at` 을 둘로 나눴다. 어느 코드가 그날 값인지는
물류 domain(`console_rules.severity_at` · `tests/logistics/test_logistics_console_rules.py`)
이 가리고, 화면에는 이름 사전과 «증명 불가» 일 때 칸에 붙이는 말만 남았다. 여기서는 그
둘을 잇는 규칙을 잰다 — 옮기기 전 화면이 내던 `(보일 말, 아래 붙일 한 줄)` 그대로다.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.api.logistics import query as logistics_query

AS_OF = date(2026, 1, 20)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("CRITICAL", ("매우 높음", None)),
        ("HIGH", ("높음", None)),
        ("MEDIUM", ("보통", None)),
        ("LOW", ("낮음", None)),
        #  사전에 없는 코드는 지어내지 않고 코드 그대로다
        ("WEIRD", ("WEIRD", None)),
        #  그날 우선도를 증명할 수 없다 — 칸에는 짧은 결과 한 마디 (#812)
        (None, ("—", "우선도 정보 없음")),
    ],
)
def test_severity_label_turns_domain_code_into_screen_text(monkeypatch, code, expected):
    calls: list[tuple[object, date]] = []

    def fake_severity_at(row, as_of):
        calls.append((row, as_of))
        return code

    monkeypatch.setattr(logistics_query, "severity_at", fake_severity_at)
    row = object()

    assert logistics_query._severity_label(row, AS_OF) == expected
    #  ★ 판정은 domain 한 번 — 화면이 날짜를 바꾸거나 다시 고르지 않는다
    assert calls == [(row, AS_OF)]


def test_screen_calls_the_logistics_domain_rules():
    """🔴 판정을 화면에 다시 적지 않는다 — 부르는 것이 물류 domain 의 그 함수다."""
    from app.logistics.domain import console_rules

    assert logistics_query.severity_at is console_rules.severity_at
    assert logistics_query.still_working is console_rules.still_working
    assert not hasattr(logistics_query, "_still_working")
    assert not hasattr(logistics_query, "_severity_at")
