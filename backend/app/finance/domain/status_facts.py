"""«지금 자금 상황» 조회의 계산 — 위험 지급일 · 날짜별 유출 · 근거 한 줄 · 정책 출처.

★ 2026-09-29 재구성 BL-014: `finance/adapter.py` 에서 옮겼다(몸통 그대로, 밑줄만 뗐다). 조회 순서는
  `service/status_query.py` · `service/pre_sales_facts.py`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from app.contracts.core import Evidence
from app.finance.domain.evidence import resolve_optional_source_ref
from app.finance.schemas.agent import CashflowProjection, FinancePolicy

# 재무 1차 Tool Set — T-FIN-01~06 (2026-08-27 확정). 그날 실제로 부른 것만 남긴다.
T_POSITION = "assess_finance_position"
T_CASHFLOW = "project_cashflow"
T_CAP = "calculate_purchase_finance_cap"
T_PRESSURE = "analyze_payment_pressure"

# 매입이 읽는 판정 필드. 대문자라 휴리스틱도 잡지만, 소문자로 바뀌어도 살아남게 선언한다.
JUDGMENT_FIELDS = ("payment_pressure",)


def find_critical_payment_dates(
    projection: CashflowProjection,
    outflow_by_date: Mapping[date, Decimal],
    floor: Decimal,
) -> list[str]:
    """**지급일 중** 위험한 날 (2026-08-27 재무 정의).

    ```text
    critical_payment_dates = minimum_cash 위반 지급일  ∪  최대 일일 유출 지급일
    ```

    ★ **제 초안을 재무가 되돌렸다.** 처음에는 *"잔액이 최소현금 아래인 날 + 투영
      최저일"* 로 정의했는데, 그건 `critical_cash_date` 지 `critical_payment_dates` 가
      아니다 — **현금이 위험한 날**과 **지급이 몰린 날**은 다르다.

      매입은 이 값으로 *"분할 회차 지급일이 이미 지급부담이 큰 날과 겹치는가"* 를
      본다. 지급이 없는 날은 겹칠 수가 없으므로 **후보는 지급일뿐이다.**

    따라서 실제 예정 유출이 있는 날만 후보로 두고, 그중 두 종류를 고른다.
    """
    payment_dates = {d for d, amount in outflow_by_date.items() if amount > 0}
    if not payment_dates:
        return []

    balance_at = {p.projection_date: p.cash_balance_krw for p in projection.projected_cash_by_date}
    picked = {d for d in payment_dates if balance_at.get(d, floor) < floor}

    peak = max(outflow_by_date[d] for d in payment_dates)
    picked |= {d for d in payment_dates if outflow_by_date[d] == peak}

    return sorted(d.isoformat() for d in picked)


def outflows_by_date(events: Sequence[Any]) -> dict[date, Decimal]:
    """날짜별 **유출** 합계. 유입은 세지 않는다 — 지급 집중도를 보는 값이다."""
    out: dict[date, Decimal] = {}
    for event in events:
        if event.direction == "INFLOW":
            continue
        out[event.event_date] = out.get(event.event_date, Decimal(0)) + event.amount_krw
    return out


def safe_ratio(numerator: Decimal, denominator: Decimal) -> float:
    return float(numerator / denominator) if denominator else 0.0


def as_float(value: Decimal | float) -> float:
    return float(value)


def policy_ref(policy: FinancePolicy, key: str, missing: list[str]) -> str | None:
    """정책값 근거는 **Policy 의 출처**를 가리킨다. 없으면 `None` 이다.

    🔴 예전에는 없을 때 **스냅샷 참조로 떨어졌다.** 정책에서 온 값에 재무 상태 행의
       id(`FIN-DAY30-LOAN`)를 달면 *"재무 상태 행에서 온 수"* 라고 말하는 것이라
       **거짓 출처**다 — 나중에 따라가면 엉뚱한 곳에 닿고, 닿았다는 사실만 남는다.

    ★ 조회는 **낼 수 있는 것만 낸다** (§3.7.6). 그래서 근거를 못 다는 claim 은
      payload 에서도 빼고 `missing_data` 로 밝힌다 — 일부가 빠졌다고 조회 전체를
      세우지는 않는다. 현재 잔액처럼 근거가 멀쩡한 값은 그대로 답한다.

    규칙 자체는 `evidence.resolve_optional_source_ref` 가 갖는다. 여기서는 **어디에
    적을지**만 정한다 — 조회는 상태가 아니라 지역 목록에 모은다.
    """
    def record(name: str) -> None:
        if name not in missing:
            missing.append(name)

    return resolve_optional_source_ref(policy, key, record)


def evidence_item(
    claim: str,
    value: Any,
    unit: str,
    ref: str,
    detail: str = "",
    grade: str = "OFFICIAL",
) -> Evidence:
    """★ `value` 에는 **판정을 만든 근거 수치**를 넣는다.

    목록형 claim 에 항목 **개수**를 넣고 싶어지는데, 그건 답의 길이를 세어 답이라고
    적는 것이다. 나중에 *"왜 그날이 위험일인가"* 를 보는 사람에게 아무것도 말해 주지
    않는다. **그 목록을 만든 임계값**을 넣는다.
    """
    return Evidence(
        claim=claim,
        source="finance",
        ref_ids=(ref,),
        value=float(value),
        unit=unit,
        evidence_grade=grade,
        evidence_detail=detail,
    )
