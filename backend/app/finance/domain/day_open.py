"""재무 개장 판정 — 개장 축 하나 · 그날 상태 정확히 하나.

★ 2026-09-29 재구성 BL-014: `finance/day_open.py` 를 판정 · 순서 · SQL 로 나눴다. 마스터 개장
  Protocol 표면은
  `adapter.py` 의 `FinanceDayOpening`.
"""

from __future__ import annotations

from app.finance.schemas.data_port import FinanceDataNotReady


def day_open_axis(rows: list) -> tuple[str, str]:
    """이 개장이 서 있는 재무 축. **주어진 실행 안에서만 고른다.**

    ★ 축이 모호하다는 말은 *"같은 실행 안에서 조달 방식이 갈렸다"* 여야 한다.
      다른 실행이 하나 더 서 있다는 사실은 이 실행을 모호하게 만들지 않는다.
    """
    if not rows:
        # 🔴 **없으면 없는 것이다.** 다른 실행의 축으로 대신하지 않는다.
        raise FinanceDataNotReady("historical_finance_position")
    if len(rows) != 1:
        raise FinanceDataNotReady("finance_runtime_axis_ambiguous")
    row = rows[0]
    if isinstance(row, dict):
        return str(row["sim_run_id"]), str(row["financing_mode"])
    return str(row[0]), str(row[1])


def has_exact_state(rows: list) -> bool:
    """그날 상태가 있는지. **둘 이상이면 고르지 않고 막는다.**"""
    if len(rows) > 1:
        raise FinanceDataNotReady("finance_state_ambiguous")
    return bool(rows)
