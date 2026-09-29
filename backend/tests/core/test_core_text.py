"""`app/core/text.py` — 금액 표시 문자열과 숫자 칸의 `Decimal` 변환.

★ 옮기기 전 부서 함수와 **같은 값**을 내는지를 잰다(2026-09-29 이동). 특히 `None` 을
  받는 법이 이름마다 다르다 — `to_decimal` 은 안 받고, `decimal_or_zero` 는 0 으로 읽는다.
"""

from decimal import Decimal, InvalidOperation

import pytest

from app.core.text import decimal_or_zero, format_manwon, format_won, to_decimal


def test_원_단위_표시는_반올림하고_쉼표와_원을_붙인다():
    assert format_won(Decimal(1234567)) == "1,234,567원"
    assert format_won(Decimal("1234.4")) == "1,234원"
    assert format_won(Decimal(0)) == "0원"
    assert format_won(Decimal(-1500000)) == "-1,500,000원"


def test_만원_단위는_숫자만_내고_단위_글자는_안_붙인다():
    assert format_manwon(Decimal(12345678)) == "1,235"
    assert format_manwon(Decimal(9999)) == "1"
    assert format_manwon(Decimal(0)) == "0"


def test_반올림은_Decimal_기본_문맥_그대로_짝수_쪽이다():
    """옮기기 전 두 벌(재무·판매 화면)도 같은 `quantize` 였다 — 2.5 는 2, 3.5 는 4."""
    assert format_won(Decimal("2.5")) == "2원"
    assert format_won(Decimal("3.5")) == "4원"
    assert format_manwon(Decimal(25000)) == "2"
    assert format_manwon(Decimal(35000)) == "4"


def test_to_decimal_은_Decimal_을_그대로_둔다():
    value = Decimal("1.50")

    assert to_decimal(value) is value


def test_to_decimal_은_str_을_거쳐_float_오차를_안_들인다():
    assert to_decimal(0.1) == Decimal("0.1")
    assert to_decimal(3) == Decimal(3)
    assert to_decimal("12.30") == Decimal("12.30")


def test_to_decimal_은_None_을_0으로_읽지_않는다():
    """물류 콘솔·과거 조회의 규약 — `None` 은 부르는 쪽이 먼저 다룬다."""
    with pytest.raises(InvalidOperation):
        to_decimal(None)


def test_decimal_or_zero_는_None_을_0으로_읽는다():
    """재무·판매 대시보드와 판매 거래처의 규약 — NULL 칸은 0 이다."""
    value = Decimal("7.25")

    assert decimal_or_zero(None) == Decimal(0)
    assert decimal_or_zero(value) is value
    assert decimal_or_zero(0.1) == Decimal("0.1")
