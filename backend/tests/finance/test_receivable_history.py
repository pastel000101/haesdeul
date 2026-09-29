"""기준일 시점의 채권 상태 — **미래 수금은 과거 화면에 들어가지 않는다.**

🔴 이 파일은 재무와 판매 **두 벌**을 함께 잠근다. 두 도메인은 서로를 import 하지 않는
   것이 계약이라 규칙이 두 곳에 있고, 갈리는 순간 판정이 갈린다.
"""

import ast
import pathlib
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.finance import receivable_history as finance_history
from app.sales.domain import receivable_history as sales_rule
from app.sales.repository import receivable_history as sales_sql

#: ★ 2026-09-29 BL-013: 판매 쪽은 상태 규칙(`domain/`)과 SQL 조각(`repository/`)으로 나뉘었다.
#:   두 벌 대조는 그대로 한다 — 판매 쪽 두 파일을 한 벌로 묶어 재무 한 파일과 맞댄다.
#:   두 벌을 합치는 일은 재무 계층화(BL-014 · 설계 쟁점 6)다.
sales_history = SimpleNamespace(
    projected_status=sales_rule.projected_status,
    history_join=sales_sql.history_join,
    history_columns=sales_sql.history_columns,
)

MODULES = pytest.mark.parametrize(
    "history", [finance_history, sales_history], ids=["finance", "sales"]
)

ORIGINAL = Decimal(282426)


# ── 상태 규칙 ─────────────────────────────────────────────────────────────


@MODULES
def test_no_collection_yet_is_open(history):
    """① 수금 전 날짜 — 원금 전액이 미수다."""
    assert (
        history.projected_status(
            original_amount_krw=ORIGINAL, received_amount_krw=Decimal(0)
        )
        == "OPEN"
    )


@MODULES
def test_a_partial_collection_is_partial(history):
    """② 부분 수금 후 날짜 — 일부만 미수다."""
    assert (
        history.projected_status(
            original_amount_krw=ORIGINAL, received_amount_krw=Decimal(100000)
        )
        == "PARTIAL"
    )


@MODULES
def test_a_full_collection_is_collected(history):
    """③ 완납 이후 날짜 — 미수가 0이다."""
    assert (
        history.projected_status(
            original_amount_krw=ORIGINAL, received_amount_krw=ORIGINAL
        )
        == "COLLECTED"
    )


@MODULES
def test_a_zero_collection_is_open_not_partial(history):
    """⑥ 0원 수금은 «일부 받았다» 가 아니다. 0 과 «받았다» 를 가른다."""
    assert (
        history.projected_status(
            original_amount_krw=ORIGINAL, received_amount_krw=Decimal(0)
        )
        == "OPEN"
    )


@MODULES
def test_an_overshooting_total_is_still_collected(history):
    """누적이 원금을 넘겨 들어와도 «더 받았다» 라는 상태를 새로 만들지 않는다."""
    assert (
        history.projected_status(
            original_amount_krw=ORIGINAL, received_amount_krw=ORIGINAL + Decimal(1)
        )
        == "COLLECTED"
    )


@MODULES
def test_a_zero_amount_receivable_is_collected_rather_than_open(history):
    """원금이 0이면 받을 것이 없다 — 영원히 OPEN 으로 남지 않는다."""
    assert (
        history.projected_status(
            original_amount_krw=Decimal(0), received_amount_krw=Decimal(0)
        )
        == "COLLECTED"
    )


# ── SQL 계약 ──────────────────────────────────────────────────────────────


@MODULES
def test_only_events_up_to_the_as_of_are_used(history):
    """④ 미래 수금 event 가 과거 as_of 에 반영되지 않는다."""
    text = str(history.history_join("haetdeul"))

    assert "collection_date <= %s" in text
    assert "collection_date >=" not in text
    assert "collection_date =" not in text


@MODULES
def test_the_last_cumulative_total_is_taken_not_the_sum(history):
    """`target_received_total_krw` 는 누적값이다 — 합이 아니라 마지막 값을 쓴다."""
    text = str(history.history_join("haetdeul"))

    assert "ORDER BY event.collection_date DESC" in text
    assert "LIMIT 1" in text
    assert "SUM(" not in text


@MODULES
def test_same_day_events_resolve_to_the_largest_cumulative(history):
    """⑦ 같은 날 event 가 여럿이면 누적이 가장 큰 것이 그날의 상태다."""
    text = str(history.history_join("haetdeul"))

    assert "event.target_received_total_krw DESC" in text


@MODULES
def test_the_run_axis_is_carried_into_the_event_lookup(history):
    """🔴 실행 축이 빠지면 다른 실행의 수금이 이 채권에 붙는다."""
    text = str(history.history_join("haetdeul"))

    assert "event.sim_run_id = r.sim_run_id" in text
    assert "event.receivable_id = r.receivable_id" in text


@MODULES
def test_a_receivable_without_events_reads_as_nothing_received(history):
    """⑤ event 가 없는 채권 — 0 으로 읽되 LEFT JOIN 이라 행이 사라지지 않는다."""
    text = str(history.history_join("haetdeul"))
    columns = str(history.history_columns())

    assert "LEFT JOIN LATERAL" in text
    assert "COALESCE(collected.target_received_total_krw, 0)" in columns


@MODULES
def test_outstanding_is_derived_from_the_original_not_the_stored_column(history):
    """🔴 `r.outstanding_amount_krw` 를 읽으면 덮인 값이 나온다."""
    columns = str(history.history_columns())

    assert "r.original_amount_krw - COALESCE(" in columns
    assert "r.outstanding_amount_krw" not in columns
    assert "r.received_amount_krw" not in columns


@MODULES
def test_the_schema_is_quoted_rather_than_interpolated(history):
    """스키마 이름을 문자열로 이어 붙이지 않는다."""
    rendered = str(history.history_join("haetdeul"))

    assert "Identifier('haetdeul')" in rendered
    assert "haetdeul.master_collection_events" not in rendered


# ── 두 벌이 갈리지 않도록 ──────────────────────────────────────────────────


def test_finance_and_sales_share_one_projection_rule():
    """⑨ 같은 채권에 대해 재무와 판매가 다른 미수를 말하면 안 된다."""
    assert str(finance_history.history_join("haetdeul")) == str(
        sales_history.history_join("haetdeul")
    )
    assert str(finance_history.history_columns()) == str(sales_history.history_columns())


@pytest.mark.parametrize(
    ("original", "received"),
    [
        (Decimal(0), Decimal(0)),
        (ORIGINAL, Decimal(0)),
        (ORIGINAL, Decimal(1)),
        (ORIGINAL, ORIGINAL - Decimal(1)),
        (ORIGINAL, ORIGINAL),
        (ORIGINAL, ORIGINAL + Decimal(1)),
    ],
)
def test_finance_and_sales_agree_on_every_status_boundary(original, received):
    assert finance_history.projected_status(
        original_amount_krw=original, received_amount_krw=received
    ) == sales_history.projected_status(
        original_amount_krw=original, received_amount_krw=received
    )


def _definitions(path: pathlib.Path) -> dict[str, str]:
    """모듈 맨 위 정의마다 **그 원문**(바로 위 `#` 주석 줄 포함)."""
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    found: dict[str, str] = {}
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            name = node.name
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
        else:
            continue
        start = node.lineno - 1
        while start > 0 and lines[start - 1].lstrip().startswith("#"):
            start -= 1
        found[name] = "\n".join(lines[start : node.end_lineno])
    return found


#: 규칙 본문을 이루는 정의. 재무 한 파일의 `_ZERO` 아래가 전부 여기 있다.
_RULE_BODY = (
    "_ZERO",
    "_HISTORY_JOIN",
    "_HISTORY_COLUMNS",
    "history_join",
    "history_columns",
    "projected_status",
)


def test_the_two_modules_stay_byte_identical_in_their_rule_bodies():
    """한쪽만 고치는 날을 빨간불로 만든다.

    ⚠️ 머리말(docstring)은 도메인마다 다를 수 있으므로 **규칙 본문만** 대조한다.

    ★ 2026-09-29 BL-013: 판매 쪽이 두 파일이 되어 «표시 줄 아래 꼬리 전체» 대신 **정의마다
      원문**(주석 포함)을 맞댄다. 재무 쪽 표시 줄 아래에 이 목록 밖의 정의가 생기면 그것도
      빨간불이다.
    """
    root = pathlib.Path("app")
    finance = _definitions(root / "finance" / "receivable_history.py")
    sales = {
        **_definitions(root / "sales" / "domain" / "receivable_history.py"),
        **_definitions(root / "sales" / "repository" / "receivable_history.py"),
    }
    finance_body = (root / "finance" / "receivable_history.py").read_text(encoding="utf-8")
    tail_names = [
        name
        for name in finance
        if finance_body.index(finance[name]) >= finance_body.index("_ZERO = Decimal(0)")
    ]

    assert tail_names == list(_RULE_BODY)
    for name in _RULE_BODY:
        assert finance[name] == sales[name], name
