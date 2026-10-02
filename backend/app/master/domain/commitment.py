"""
commitment.py — 승인된 매입안 → 확정 입고 약정 (H1)

사람이 안을 고르면, 그 안은 물류의 미래 창고 점유 계산에 겹쳐지는 사실이 된다.
그 변환이 여기다.

```text
사용자 APPROVE → 승인된 시나리오 → ApprovedCommitment → 물류 H1 미래 점유
```

오케스트레이터를 거치지 않는다 (지시 2026-09-01). 승인 약정의 주인은 여기다. 비슷한
변환이 남은 곳은 Critic 테스트가 시나리오를 짓는 받침대(`tests/master/critic/cycle_harness.py`)
뿐이고, `tests/master/test_orchestrator_is_gone.py` 와
`tests/master/test_master_legacy_cycle_is_gone.py` 가 그 방향을 잠근다.

품목을 잃지 않는다. 회차 수량을 `sum(leg.qty_kg.values())` 로 합치면 품목이 없어지고,
물류 H1 이 총 kg 으로만 계산해 "배추 출고가 양파 재고를 대신 소진한다" (물류 질의
2026-09-01 §1).

마스터는 숫자를 만들지 않는다. 여기서 하는 계산은 둘뿐이고 둘 다 옮기기다.

```text
도착일 = 매입 실행일 + inbound_lead_days     N4 는 물류가 준다
회차 수량 = 안이 적은 회차 수량 그대로        재계산하지 않는다
회차 금액 = 안이 적은 회차 금액 그대로        총액을 회차 수로 나누지 않는다
```

두 값 다 부서가 낸 것이고, 마스터는 자리를 옮기기만 한다 (§3.2.2).

약정 타입(`ApprovedCommitment` · `ArrivalLeg` · `SourcingLine` · `CommitmentNotBuildable` ·
`ITEM_CODES`)은 부서와 같이 쓰는 계약 자리인 `app/contracts/commitment.py` 에 있다. 여기에는
승인된 안 → 약정 변환(`build_commitment`)과 실매입 기록 덮기(`with_purchase_record` ·
`RecordedLeg`)가 있다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any

from app.contracts.commitment import (
    ApprovedCommitment,
    ArrivalLeg,
    CommitmentNotBuildable,
    SourcingLine,
)


def build_commitment(
    *,
    request_id: str,
    as_of: date,
    item: str | None,
    scenario: Mapping[str, Any],
    inbound_lead_days: Any,
    decision_seq: int,
    purchase_payment_days: Any = None,
) -> ApprovedCommitment:
    """승인된 시나리오 하나를 약정으로 옮긴다.

    :param purchase_payment_days: N5. 재무가 봉투로 준다 — 마스터는 옮기기만 한다.
        `inbound_lead_days`(N4)와 완전히 같은 자리다. 없으면 `None` 이고, 그러면
        회차의 `payment_due_date` 도 `None` 으로 남는다 — 0 으로 대체하지 않는다.
    :raises CommitmentNotBuildable: 옮길 수 없을 때. 빈 약정을 만들지 않는다.
    """
    if not item:
        raise CommitmentNotBuildable("실행에 품목이 없다 — 약정에 실을 품목을 지어내지 않는다.")

    total_qty = _number(scenario.get("total_qty_kg"))
    if total_qty is None:
        raise CommitmentNotBuildable("안에 총량이 없다.")
    total_amount = _number(scenario.get("total_amount_krw"))
    if total_amount is None:
        raise CommitmentNotBuildable("안에 총액이 없다.")

    lead = _number(inbound_lead_days)
    legs, notes = _legs(
        scenario.get("split_plan"),
        item,
        as_of,
        lead,
        _number(purchase_payment_days),
    )

    return ApprovedCommitment(
        approval_id=f"H1-{request_id}-{decision_seq}",
        request_id=request_id,
        as_of=as_of,
        item=item,
        scenario_label=str(scenario.get("label") or ""),
        total_qty_kg=total_qty,
        total_amount_krw=total_amount,
        arrival_schedule=legs,
        sourcing_plan=_sourcing(scenario.get("sourcing_plan")),
        inbound_lead_days=lead,
        notes=notes,
    )


@dataclass(frozen=True)
class RecordedLeg:
    """사람이 적은 실매입 한 회차 (설계 260915 안 A §3).

    칸이 넷뿐인 이유 — 지금 코드가 실제로 읽는 값만 받는다. 시장 · 메모 · 회차
    추가는 없다 (사용자 결정 9/15).
    """

    seq: int
    qty_kg: float
    amount_krw: float
    purchase_date: date
    arrival_date: date


def with_purchase_record(
    commitment: ApprovedCommitment,
    *,
    legs: Sequence[RecordedLeg],
    grade: str,
    purchase_payment_days: Any,
) -> ApprovedCommitment:
    """선정안으로 조립한 약정의 사본에 실매입 값을 덮는다. 순수 함수다.

    ```text
    회차 qty_kg · amount_krw · purchase_date · arrival_date   기록값
    회차 payment_due_date                                     기록 매입일 + N5
    total_qty_kg · total_amount_krw                           기록 회차 합
    sourcing_plan[].grade                                     기록 등급
    ```

    약정을 새로 짓지 않는다. `build_commitment` 가 선정안으로 만든 것에서 값만
    바꾼다 — `approval_id` · 품목 · 회차 수와 `seq` 는 선정안 그대로다.

    지급일 식은 `_legs` 와 같다 (매입일 + N5). N5 가 없으면 `None` 으로 두고,
    일수로 안 읽히면 멈춘다 — 지어낸 지급일이 원장에 남는 것보다 낫다.

    :raises CommitmentNotBuildable: 회차 집합이 선정안과 다르거나 값이 모순일 때.
        고쳐 쓰지 않는다.
    """
    if not isinstance(grade, str) or not grade.strip():
        raise CommitmentNotBuildable("등급이 비어 있다.")
    planned = {leg.seq for leg in commitment.arrival_schedule}
    by_seq = {leg.seq: leg for leg in legs}
    if len(by_seq) != len(legs):
        raise CommitmentNotBuildable("같은 회차가 두 번 적혔다.")
    if not planned:
        raise CommitmentNotBuildable("선정안에 회차 일정이 없어 기록할 회차가 없다.")
    if set(by_seq) != planned:
        raise CommitmentNotBuildable(
            f"기록 회차 {sorted(by_seq)} 가 선정안 회차 {sorted(planned)} 와 다르다."
        )
    for one in legs:
        if one.qty_kg <= 0 or one.amount_krw <= 0:
            raise CommitmentNotBuildable(f"{one.seq}회차 수량과 금액은 0 보다 커야 한다.")
        if one.arrival_date < one.purchase_date:
            raise CommitmentNotBuildable(
                f"{one.seq}회차 도착일({one.arrival_date})이"
                f" 매입일({one.purchase_date})보다 앞선다."
            )

    days = _number(purchase_payment_days)
    if days is not None and (days < 0 or days != int(days)):
        raise CommitmentNotBuildable(
            f"purchase_payment_days 가 일수로 읽히지 않아({days:g}) 지급일을 계산하지 않았다."
        )

    new_legs = tuple(
        replace(
            leg,
            qty_kg=float(by_seq[leg.seq].qty_kg),
            amount_krw=float(by_seq[leg.seq].amount_krw),
            purchase_date=by_seq[leg.seq].purchase_date,
            arrival_date=by_seq[leg.seq].arrival_date,
            payment_due_date=(
                by_seq[leg.seq].purchase_date + timedelta(days=int(days))
                if days is not None
                else None
            ),
        )
        for leg in commitment.arrival_schedule
    )
    # 등급은 줄마다 기록값으로 바꾼다. 선정안이 등급 줄을 안 실었으면 한 줄을
    # 세운다 — 사람이 적은 등급이 원장 `purchase_items.grade` 로 가야 한다.
    sourcing = (
        tuple(replace(line, grade=grade) for line in commitment.sourcing_plan)
        if commitment.sourcing_plan
        else (SourcingLine(grade=grade),)
    )
    return replace(
        commitment,
        total_qty_kg=sum(leg.qty_kg for leg in new_legs),
        total_amount_krw=sum(float(leg.amount_krw or 0.0) for leg in new_legs),
        arrival_schedule=new_legs,
        sourcing_plan=sourcing,
    )


def _sourcing(raw: Any) -> tuple[SourcingLine, ...]:
    """안의 `sourcing_plan` 을 줄 수 그대로 옮긴다.

    접지 않는다. 등급별 수량을 합치거나 대표 등급 하나로 줄이면, 그 순간
    "등급이 여럿이었다" 는 사실이 사라져 원장이 막아야 할 자리를 통과한다.
    줄이 셋이면 셋을 그대로 들고 온다.

    검사하지 않는다. 수량 합이 총량과 맞는지, 단가가 양수인지는 매입
    `SourcingPlanItem` 이 이미 본다. 여기서 다시 세면 허용 오차가 갈리는 날
    같은 안을 한 곳은 통과시키고 한 곳은 막는다.

    없으면 빈 목록이다. `sourcing_plan` 이 아예 없는 안(조회·옛 응답)이 있고,
    그때 등급이 없다는 것은 정상 상태다 — 못 만든 것이 아니라 안 온 것이다.
    """
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    lines: list[SourcingLine] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        grade = entry.get("grade")
        if not isinstance(grade, str) or not grade.strip():
            # 매입 `SourcingPlanItem.grade` 는 `NonEmptyStr` 라 여기 오는 빈 등급은
            # 계약 밖 값이다. 지어내지 않고 등급을 안 싣는다 — 이 줄이 빠지면
            # 등급이 하나도 안 남아 원장이 `NULL` 로 가고, 그것이 정직한 결과다.
            continue
        market = entry.get("market")
        lines.append(
            SourcingLine(
                grade=grade,
                market=market if isinstance(market, str) else None,
                qty_kg=_number(entry.get("qty_kg")),
                grade_unit_price=_number(entry.get("grade_unit_price")),
            )
        )
    return tuple(lines)


def _legs(
    split_plan: Any,
    item: str,
    as_of: date,
    lead: float | None,
    payment_days: float | None = None,
) -> tuple[tuple[ArrivalLeg, ...], tuple[str, ...]]:
    """회차별 입고. N4 가 없으면 일정을 만들지 않는다.

    `lead` 를 0 으로 대체하면 "오늘 승인분이 오늘 도착" 이 되어 재고 전환 금지가
    무의미해진다 (§1.2-10 · §3.2.3). 일정 없이 약정만 남기고, 왜 없는지를 적는다.

    N5(`payment_days`)는 다르다 — 없어도 일정은 선다. 지급일이 없다고 입고가
    없는 것은 아니다. `payment_due_date` 만 `None` 으로 두고, 그 상태로 원장을 쓸 수
    없다는 판단은 전이 경계가 한다. 다만 일수로 읽히지 않는 값은 도착일과 같은
    태도로 막는다 — 지어낸 지급일이 원장에 남는 것보다 멈추는 편이 낫다.
    """
    if not isinstance(split_plan, Sequence) or isinstance(split_plan, (str, bytes)):
        return (), ("안에 분할 계획이 없어 회차별 입고 일정을 만들지 못했다.",)
    supplied = [
        _date(raw.get("expected_arrival_date")) for raw in split_plan if isinstance(raw, Mapping)
    ]
    filled = sum(1 for eta in supplied if eta is not None)
    if 0 < filled < len(supplied):
        # 부분 공급은 섞어 만들지 않는다 (매입 제보 2026-09-01). 섞으면 실린 회차는
        # 매입 값, 빈 회차는 마스터 계산으로 출처가 섞인 일정이 나간다. null 은 "N4
        # 미결로 매입도 못 냈다" 인데 같은 제안에서 회차마다 있고 없고는 자기모순이다 —
        # 매입도 구조적으로 부분을 안 낸다(_rounds 가 통째로 쓰거나 통째로 None). 부분은
        # 계약 이상 신호로 보고 일정을 안 만든다.
        note = (
            f"회차 도착일이 {len(supplied)}회차 중 {filled}회차만 실려 있어"
            " 일정을 만들지 않았다 — 출처를 섞어 만들지 않는다."
        )
        return (), (note,)
    # 금액도 도착일과 같은 부분 공급 규칙을 따른다. 같은 제안에서 회차마다 있고
    # 없고는 자기모순이고, 섞어 만들면 출처가 섞인 값이 원장(purchase_items)으로 간다.
    #
    # 다만 버리는 것이 다르다. 도착일이 없으면 회차가 성립하지 않아 일정을
    # 통째로 버리지만, 금액이 없어도 회차는 선다 — 오늘이 정확히 그 상태다.
    # 그래서 금액만 안 싣고 일정은 만든다.
    amounts = [_number(raw.get("amount_krw")) for raw in split_plan if isinstance(raw, Mapping)]
    amount_filled = sum(1 for a in amounts if a is not None)
    carry_amounts = bool(amounts) and amount_filled == len(amounts)
    amount_notes: tuple[str, ...] = ()
    if 0 < amount_filled < len(amounts):
        amount_notes = (
            (
                f"회차 금액이 {len(amounts)}회차 중 {amount_filled}회차만 실려 있어"
                " 금액을 싣지 않았다 — 출처를 섞어 만들지 않는다."
            ),
        )

    if filled and filled == len(supplied):
        lead = 0.0  # 계산 안 함 — 아래 게이트만 지나가는 무해한 값. 회차마다 매입 값을 쓴다
    elif lead is None:
        return (), ("물류 inbound_lead_days(N4) 가 없어 도착일을 계산하지 못했다.",)
    if lead < 0 or lead != int(lead):
        # `int(lead)` 로 자르지 않는다. 자르면 2.9 가 조용히 2일이 되고 -1 은 매입일보다
        # 과거 도착을 만든다 — 에러 없이 창고 점유가 하루 이르게 계산되는 종류다. 일수로
        # 읽을 수 없는 값이면 일정을 안 만든다. N4 를 마스터가 고쳐 주지 않는다.
        return (), (f"inbound_lead_days 가 일수로 읽히지 않아({lead:g}) 도착일을 계산하지 않았다.",)

    # N5 도 도착일과 같은 문을 지난다. 없으면(`None`) 통과하고 지급일만 안 싣는다 —
    # 그 경우가 오늘의 정상 상태다. 있는데 일수로 안 읽히면 지어내지 않고 멈춘다.
    pay_days: int | None = None
    if payment_days is not None:
        if payment_days < 0 or payment_days != int(payment_days):
            note = (
                f"purchase_payment_days 가 일수로 읽히지 않아({payment_days:g})"
                " 지급일을 계산하지 않았다."
            )
            return (), (note,)
        pay_days = int(payment_days)

    legs: list[ArrivalLeg] = []
    for index, raw in enumerate(split_plan, 1):
        if not isinstance(raw, Mapping):
            continue
        qty = _number(raw.get("qty_kg"))
        purchase_date = _date(raw.get("date"))
        if qty is None or purchase_date is None:
            return (), (f"{index}회차에 수량 또는 매입일이 없어 일정을 만들지 못했다.",)
        # 매입이 도착일을 실어 주면 계산하지 않는다 (매입 회신 2026-09-01 합의).
        # 매입은 arrival_dates(§5.5)로 같은 값을 이미 계산한다 — 같은 사실을 두 곳에서
        # 각자 계산하면 어긋나는 날이 온다. null 이면 "N4 미결로 매입도 못 냈다"이므로
        # 마스터도 계산하지 않는다(같은 N4 원천을 다시 쓰면 두-곳-계산이 재현된다).
        eta = _date(raw.get("expected_arrival_date"))
        if eta is not None and eta < purchase_date:
            # 받은 값도 모순은 막는다 (매입 참고 2026-09-01). 마스터 계산 경로가 lead<0
            # 을 막는 것과 같다 — 도착이 매입보다 앞서면 물류 점유가 이르게 계산되고
            # 에러가 안 난다. 고쳐 쓰지 않고 막는다.
            raise CommitmentNotBuildable(
                f"{index}회차 도착일({eta})이 매입일({purchase_date})보다 앞선다"
                " — 받은 값을 고쳐 쓰지 않는다."
            )
        if eta is None:
            # 매입 실행일 + N4 다. 안의 `date` 는 도착일이 아니라 매입일이다
            # (매입 `purchase_agent/schemas/proposal.py` 의 `SplitPlanItem` 주석).
            # 도착일로 읽으면 물류 cap_by_date 창(도착일 기준) 밖의 키를 조회해
            # 검사 자체가 안 돈다 — 과소 계산이 아니라 그보다 앞에서 무너진다.
            eta = purchase_date + timedelta(days=int(lead))
        legs.append(
            ArrivalLeg(
                item=item,
                qty_kg=qty,
                arrival_date=eta,
                purchase_date=purchase_date,
                seq=int(raw.get("seq") or index),
                amount_krw=_number(raw.get("amount_krw")) if carry_amounts else None,
                # 도착일 옆에서 같이 만든다. 기준은 매입일이다 — 재무 전이가
                # `purchase_date + N5` 로 만기를 세우는 것과 같은 식이어야 원장의
                # `purchases.payment_due_date` 와 `payables.due_date` 가 갈리지 않는다.
                payment_due_date=(
                    purchase_date + timedelta(days=pay_days) if pay_days is not None else None
                ),
            )
        )
    if not legs:
        return (), ("분할 계획이 비어 회차별 입고 일정을 만들지 못했다.",)
    return tuple(legs), amount_notes


def _number(value: Any) -> float | None:
    """숫자만 받는다. `bool` 은 숫자가 아니다."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None
