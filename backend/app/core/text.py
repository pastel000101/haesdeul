"""작은 값 변환 — 금액 표시 문자열과 숫자 칸의 `Decimal` 변환.

2026-09-29 부서마다 같은 몸통으로 복제돼 있던 것만 모았다. 옮기기 전과 입력 · 반환 ·
반올림 · 표시 문구가 같다.

```text
format_won(x)       원 단위 반올림 · 쉼표 · "원"             api/{finance,sales}/presenter
format_manwon(x)    만 원 단위 반올림 · 쉼표 · 단위 글자 없음  위 두 파일
to_decimal(x)       Decimal 은 그대로, 나머지는 str 을 거친다  logistics/console_service
                    · None 을 안 받는다                         logistics/historical_repository
decimal_or_zero(x)  to_decimal 과 같되 None(NULL) 은 0          finance/dashboard · sales/dashboard
                                                                sales/console_partners
```

★ **`None` 을 어떻게 받는지를 이름에 적는다.** 옮기기 전에는 셋 다 `_decimal` 이라는 같은
  이름이었는데, 하나는 `None` 을 0 으로 읽고 하나는 받지 않았다(`Decimal("None")` →
  `InvalidOperation`). 같은 이름이면 부르는 자리에서 그 차이가 안 보인다.

★ **`str` 을 거친다.** `Decimal(0.1)` 은 float 의 이진 오차를 그대로 들여온다 — 숫자 칸이
  float 로 와도 `Decimal(str(x))` 로 읽는다.

★ **반올림은 `Decimal` 기본 문맥을 따른다** (`ROUND_HALF_EVEN` — 2.5 → 2, 3.5 → 4).
  옮기기 전 두 벌도 같은 `quantize` 였다.

🔴 **부서 업무 규칙은 두지 않는다.** 수량 검증(`_quantity`) · 빈 글자 거절(`_require_text`)
  은 부서 예외와 문구를 쓰는 업무 검증이고, SQL 결과 행 읽기(`_cell` · `_rows`)는
  repository 의 일이라 각 부서에 남겼다. 이름만 비슷하고 규약이 다른 함수(마스터 보고서의
  `_won` 처럼 `None` 을 "—" 로 쓰는 것)도 합치지 않았다.
"""

from decimal import Decimal

__all__ = [
    "decimal_or_zero",
    "format_manwon",
    "format_won",
    "to_decimal",
]

_ZERO = Decimal(0)
_ONE = Decimal(1)
_TEN_THOUSAND = Decimal(10000)


def format_won(value: Decimal) -> str:
    """원 단위 표시. `Decimal("1234.5")` → `"1,234원"`."""
    return f"{value.quantize(_ONE):,.0f}원"


def format_manwon(value: Decimal) -> str:
    """만 원 단위 숫자. `Decimal("12345678")` → `"1,235"`. 단위 글자는 부르는 쪽이 붙인다."""
    return f"{(value / _TEN_THOUSAND).quantize(_ONE):,.0f}"


def to_decimal(value: object) -> Decimal:
    """숫자 칸을 `Decimal` 로. **`None` 은 0 이 아니다** — 부르는 쪽이 먼저 다룬다."""
    return value if isinstance(value, Decimal) else Decimal(str(value))


def decimal_or_zero(value: object) -> Decimal:
    """숫자 칸을 `Decimal` 로. **`None`(NULL) 은 0 으로 읽는다.**"""
    return _ZERO if value is None else to_decimal(value)
