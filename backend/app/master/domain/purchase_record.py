"""실매입 기록의 검사와 계산 — 등급 · 수량 · 단가 · 날짜 검사, 계획 대비 차이, 기록 시나리오.

★ 2026-09-30 재구성 BL-018: `master/purchase_record.py` 에서 옮겼다 — `BEFORE_APPROVAL_MESSAGE`,
  `CLOSED_DUE_DATE_MESSAGE`, `SAME_DAY_DUE_DATE_MESSAGE`, `PURCHASE_DATE_MESSAGE`,
  `WHOLE_QTY_MESSAGE`, `MULTI_GRADE_MESSAGE`, `_세기`, `WHOLE_UNIT_PRICE_MESSAGE`,
  `check_single_grade`, `check_recordable_values`, `differs_from_plan`, `_as_number`,
  `_grade_lines`, `recorded_scenario`, `recorded_unit_price`, `plan_unit_price`.
"""

from __future__ import annotations

import copy
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Any

from app.contracts.commitment import ApprovedCommitment
from app.master.domain.commitment import RecordedLeg
from app.master.schemas.approval import CurrentApproval
from app.master.schemas.decision import DecisionRejected

BEFORE_APPROVAL_MESSAGE = "승인한 날보다 앞선 매입일은 기록할 수 없습니다 — 매입일을 확인해 주세요"
"""매입일이 승인 실행 기준일보다 앞설 때 화면에 나가는 한 줄 (§4-6 ②)."""

CLOSED_DUE_DATE_MESSAGE = "지급기일이 이미 마감된 날보다 앞입니다 — 매입일을 확인해 주세요"
"""회차 지급기일이 마지막 재무 일마감일보다 앞일 때 화면에 나가는 한 줄 (§4-6 ② · 2026-09-16)."""

SAME_DAY_DUE_DATE_MESSAGE = (
    "지급기일이 이미 마감된 날과 같습니다 — 마감한 그날 승인한 그날 매입만 기록할 수 있습니다"
)
"""지급기일이 마감일과 같은데 동일일 예외(승인일 = 매입일 = 마감일)가 아닐 때의 한 줄."""

PURCHASE_DATE_MESSAGE = "매입일은 승인한 날({as_of})과 같아야 합니다 — {seq}회차"
"""첫 회차 매입일이 승인 실행 as_of 와 다를 때의 한 줄 (선검사 · 2026-09-16)."""

WHOLE_QTY_MESSAGE = "수량은 1kg 단위로 적어 주세요 — {seq}회차"
"""수량에 소수점이 있을 때의 한 줄 (선검사 · 2026-09-16)."""

MULTI_GRADE_MESSAGE = (
    "이 안은 등급이 {세기}입니다 ({등급}). 실매입 기록은 한 등급만 받습니다"
    " — 매입 화면에서 등급이 하나인 다른 안을 골라 주세요."
)
"""승인한 안의 등급이 둘 이상일 때 화면에 나가는 한 줄 (선검사 · 2026-09-16)."""

#: 사람이 읽는 등급 가짓수. 없는 수는 `N개` 로 떨어진다 — 없는 말을 지어내지 않는다.
_세기 = {2: "둘", 3: "셋", 4: "넷"}

WHOLE_UNIT_PRICE_MESSAGE = "단가는 원 단위 정수로 적어 주세요 — {seq}회차"
"""단가가 원 단위 정수로 안 떨어질 때의 한 줄 (선검사 · 2026-09-16).

★ 입력이 단가로 바뀐 뒤(`PurchaseRecordLegIn.unit_price_krw`)로는 Pydantic 이 소수점
  단가를 먼저 막는다. 그래도 이 문장을 남기는 이유는 **입구를 안 지나는 부름** 때문이다
  — `RecordedLeg` 를 직접 짓는 자리에서 금액이 수량으로 안 나뉘면 원장의 단가가 소수가
  되고, DB CHECK 가 그때 알기 어려운 말로 막는다.
"""


def check_single_grade(plan: ApprovedCommitment) -> None:
    """**등급이 둘 이상인 안에는 실매입을 못 적는다** (선검사 · 2026-09-16).

    🔴 **왜 막는가 — 기록이 원장 가드를 우회하기 때문이다.** 원장은 등급이 둘 이상인
       약정을 일부러 막는다 (`ledger.ledger_block_reason`): `purchase_items` 는 품목당
       한 줄이고 `grade` 는 그 한 줄에 한 칸이라, 아무 등급이나 고르면 어느 등급이
       남는지가 줄 순서에 걸리고 합치면 없는 등급을 마스터가 지어낸 것이 된다.

       ★ 그런데 **기록의 `grade` 는 기록 전체에 하나다.** 약정 덮기
         (`commitment.with_purchase_record`)가 그 한 등급을 `sourcing_plan` 의 **모든
         줄에** 얹으므로, 등급 줄이 둘인 안에 기록하면 두 줄이 사람이 적은 한 등급으로
         덮인다. 그러면 `commitment.grades` 가 1개가 되어 **가드가 더는 안 막고, 에러
         없이 틀린 등급이 원장에 선다.** 막히는 것보다 나쁘다 — 아무도 모른다.

    ```text
    실측  2026-09-16 · 실 DB 읽기만
      매입 파트 전수   안 8,809개 중 등급 2개인 안 31개 (무 22 · 양파 9 · 전부 '상'·'중')
                       그 31개가 속한 실행 20개가 **20/20 APPROVE** — 승인된 안이 바로 그 안
      마스터 실측      SIM-CHAIN-REH-0916 에 2등급 안 8개 · 그중 6개는 승인까지 났는데
                       **원장 0행** (`ledger_block_reason` 이 막아 매입이 아예 안 섰다)
      지금 그 20건은 `decided_by = AUTO-BACKFILL` 이라 「사람 승인」 검사가 먼저 거른다
        — 구멍이 막힌 것이 아니라 **아직 구멍에 닿지 않은 것**이다 (9/11 부터 사람 승인)
    ```

    🔴 **막는 규칙의 주인은 `ledger_block_reason` 이다.** 여기는 그 규칙을 **다시 쓰지도
       부르지도 않는다** — 그 함수는 약정을 받는데 여기는 아직 기록 사본을 만들기 전이다.
       기록이 그 규칙을 **우회하지 않는다**는 것만 입구에서 지킨다.

    ⚠️ **등급 줄이 하나도 없는 안은 지금 그대로 지난다** (`sourcing_plan` 이 비었거나
      등급이 없는 안 · 실측 6개). 덮을 줄이 없으니 우회할 가드도 없고, 그때는 기록의
      `grade` 로 한 줄을 세우는 것이 맞다 (`with_purchase_record` 의 `else` 갈래).
    """
    grades = plan.grades
    if len(grades) < 2:
        return
    raise DecisionRejected(
        MULTI_GRADE_MESSAGE.format(
            세기=_세기.get(len(grades), f"{len(grades)}개"), 등급=" · ".join(grades)
        )
    )


def check_recordable_values(approval: CurrentApproval, legs: Sequence[RecordedLeg]) -> None:
    """**선검사** — 재검증이 알기 어려운 말로 막기 전에 사람 말로 거부한다 (2026-09-16).

    ```text
    첫 회차 매입일 == 승인 실행 as_of
    회차마다 수량이 정수 · 금액 ÷ 수량(= 단가)이 정수
    ```

    ★ **왜 매입일을 승인일에 묶는가.** 기록값 재검증은 안 사본을 매입안 계약
      (`purchase_agent.schemas.proposal.PurchaseProposal.validate_proposal_rules`)으로 다시 읽는데,
      그 계약이 `split_plan[0].date == meta.as_of` 를 요구한다. 사본의 `meta.as_of` 는
      **승인 실행의 as_of** 라(`decision_service.revalidate_recorded`), 사람이 첫 회차
      매입일을 승인일과 다른 날로 적으면 계약이 그 자리에서 떨어진다. 그때 사람이
      보는 것은 「재검증 통과 못 함」뿐이라 무엇을 고쳐야 하는지 알 수 없다.

      ★ 계약이 묶는 것은 **첫 회차 하나뿐이다.** 2회차 이후 매입일은 선정안대로
        뒤 날짜여도 된다 — 여기서 같이 묶으면 분할 선정안을 그대로 기록하는 것조차
        막힌다.

      🔴 **발표 뒤 과제 — 계약을 넓힌다.** 실매입은 승인한 날과 다른 날에도 일어날 수
        있다. 옳은 자리는 「기록 사본의 as_of 를 기록 매입일로 싣는다」이거나 「재검증
        경로에서 첫 회차 날짜 규칙을 푼다」이고, 둘 다 매입 계약을 건드린다. 발표
        전에는 입구에서 막아 사람이 알아볼 수 있는 말을 듣게 한다.

    ★ **왜 정수인가.** 매입안 계약의 수량 · 등급 단가 칸이 정수라(`SourcingPlanItem`),
      소수점 수량 · 금액은 재검증에서 떨어진다. 반올림해 통과시키면 **기록한 값과 다른
      값이 검증을 지난다** — `#727` 이 그래서 반올림 대신 막아 두었고 그 판단을 유지한다.

    ★ **단가는 금액 ÷ 수량으로 잰다.** 입력이 단가로 바뀐 뒤(2026-09-16) 정상 경로로는
      늘 정수지만(`PurchaseRecordLegIn` 이 수량 · 단가를 `int` 로 받고 금액을 곱해 만든다),
      여기는 `RecordedLeg` 를 받는 자리라 입구를 안 지나는 부름도 온다. 원장이 만드는
      단가가 바로 이 나눗셈이므로(`ledger._row_for_leg`), **같은 식으로 미리 잰다.**

    ⚠️ **승인 실행 as_of 를 못 읽으면 매입일은 안 잰다.** 못 잰 것을 틀렸다고 하지 않는다
      — 뒤의 경계(`_check_purchase_dates`)도 같은 규율이다.
    """
    for leg in legs:
        if not float(leg.qty_kg).is_integer():
            raise DecisionRejected(WHOLE_QTY_MESSAGE.format(seq=leg.seq))
        # ⚠️ 수량이 0 이면 단가를 잴 수 없다. 여기서 새 문장을 지어내지 않는다 —
        #    `PurchaseRecordLegIn` 이 `gt=0` 으로 이미 막았고, 뒤의 원장이 제 말로 막는다.
        if leg.qty_kg > 0 and not float(leg.amount_krw / leg.qty_kg).is_integer():
            raise DecisionRejected(WHOLE_UNIT_PRICE_MESSAGE.format(seq=leg.seq))
    as_of = approval.as_of
    if as_of is None or not legs:
        return
    # ★ 안의 `split_plan[0]` 에 얹히는 회차가 첫 회차다 (`recorded_scenario` 는 seq 로 짝짓고,
    #   계약의 `validate_split_sequence` 가 seq 를 1부터 세게 한다).
    first = min(legs, key=lambda one: one.seq)
    if first.purchase_date != as_of:
        raise DecisionRejected(PURCHASE_DATE_MESSAGE.format(as_of=as_of, seq=first.seq))


def differs_from_plan(plan: ApprovedCommitment, legs: Sequence[RecordedLeg], grade: str) -> bool:
    """기록값이 선정안과 **하나라도 다른가.** 같으면 재검증을 생략한다 (§4-6 ①).

    ★ 등급은 NFC 로 비교한다 (`ApprovedCommitment.grades` 와 같은 규율). 선정안 등급이
      하나가 아니면 다른 것으로 본다 — 한 기록 = 한 등급이다.
    """
    planned = {leg.seq: leg for leg in plan.arrival_schedule}
    for one in legs:
        leg = planned.get(one.seq)
        if leg is None:
            return True
        if (
            leg.qty_kg != one.qty_kg
            or leg.amount_krw != one.amount_krw
            or leg.purchase_date != one.purchase_date
            or leg.arrival_date != one.arrival_date
        ):
            return True
    grades = plan.grades
    return len(grades) != 1 or unicodedata.normalize("NFC", grades[0]) != unicodedata.normalize(
        "NFC", grade
    )


def _as_number(value: float) -> int | float:
    """정수로 떨어지면 정수로 싣는다 — 매입 안의 수량 · 금액 칸이 정수다."""
    return int(value) if float(value).is_integer() else value


def _grade_lines(total_qty: float, total_amount: float) -> list[tuple[int | float, int | float]]:
    """기록 총량 · 총액을 **등급 배분 줄**로 편다. `(수량, 단가)` 목록이다.

    ```text
    계약   sourcing_plan[].grade_unit_price 는 정수 원/kg (`SourcingPlanItem`)
    검사   total_amount_krw == Σ(qty_kg × grade_unit_price)   (`Scenario.validate_quadruple_match`)
    ```

    🔴 **금액을 고치지 않는다.** 기록 총액은 사람이 실제로 낸 돈이고 그것이 정본이다.
      그래서 총액 ÷ 총량이 정수로 안 떨어지면 **단가를 반올림해 총액을 흔드는 대신**
      나머지를 한 줄 더 얹는다.

      ```text
      300kg · 271,000원   →  (197kg × 903) + (103kg × 904) = 271,000
      ```

      ★ 합이 **정확히** 총액이다 — `divmod` 의 몫과 나머지를 그대로 쓴다. 새 업무
        숫자를 만든 것이 아니라, 기록한 한 사실을 계약이 요구하는 정수 단가 모양으로
        적은 것이다.

    ⚠️ **등급이 둘이 되는 것이 아니다.** 두 줄 다 기록한 그 등급이고, 부르는 쪽이
      등급 이름을 얹는다 — 여기는 수량과 단가만 센다.

    🔴 **정수가 아닌 기록은 그대로 흘린다** (kg 에 소수점이 있는 경우). 매입 계약의
      수량 · 금액 칸이 정수라 부서 파싱에서 걸리는데, **여기서 반올림해 통과시키면
      기록값과 다른 값이 검증을 지난다.** 못 적는 것은 못 적는 대로 막힌다.

      ⚠️ **입력이 단가라 이 갈래는 안 불린다** (2026-09-16). 사람은 회차마다 정수
        단가를 적고 금액은 수량 × 단가로 나므로, 수량도 금액도 언제나 정수다.
        **마지막 방어선으로 남긴다**: 입구(`PurchaseRecordLegIn` · `check_recordable_values`)를
        안 지나는 부름이 생겨도 반올림한 값이 조용히 검증을 지나면 안 된다.

      ⚠️ **나머지 줄(`rest != 0`)도 같다.** 회차 단가가 회차마다 다르면 총액 ÷ 총량이
        정수로 안 떨어져 여전히 두 줄이 난다 — 그 갈래는 살아 있다.
    """
    if not (float(total_qty).is_integer() and float(total_amount).is_integer()):
        return [(total_qty, total_amount / total_qty if total_qty else total_amount)]
    qty = int(total_qty)
    unit, rest = divmod(int(total_amount), qty)
    if rest == 0:
        return [(qty, unit)]
    # `rest < qty` 라 앞 줄 수량은 항상 1 이상이다.
    return [(qty - rest, unit), (rest, unit + 1)]


def recorded_scenario(
    scenario: Mapping[str, Any],
    *,
    legs: Sequence[RecordedLeg],
    grade: str,
    purchase_payment_days: Any,
) -> dict[str, Any]:
    """선정안 **안 사본**에 기록값을 맞춘다. 🔴 **원본을 안 건드린다.**

    ```text
    split_plan[]         qty_kg · amount_krw · date(매입일) · expected_arrival_date
    total_qty_kg · total_amount_krw   기록 회차 합
    payment_schedule[]   purchase_date · payment_date(매입일 + N5) · qty_kg · amount_krw
                         (amount_max_krw 는 qty_kg × max_price 로 다시 센다 · 안에 있을 때만)
    sourcing_plan[]      grade · qty_kg · grade_unit_price 를 기록값으로 다시 놓는다
    ```

    ⚠️ **없는 칸을 만들지 않는다.** `payment_schedule` 이 없는 안(일괄 1회차)에는 싣지
      않는다 — 재무가 `split_plan` 에서 재구성한다.

    🔴 **`sourcing_plan` 은 줄을 다시 놓는다** (2026-09-16 실측 · `#722` 뒤). 전에는
      `grade` 만 덮고 줄이 하나일 때만 `qty_kg` 를 총량으로 바꿨다. `grade_unit_price`
      는 선정안 값 그대로였으므로 **기록 총액과 등급 배분 금액이 어긋났고**, 두 부서가
      이 payload 를 `PurchaseProposal` 로 파싱하다 그 자리에서 떨어졌다.

      ```text
      실측  SIM-TEST-PURREC-0916 · 01-05 배추 · 300kg 270,000원 기록
            finance   ERROR  payload {"validation_errors": ["scenarios.0"]}
            inventory RUNTIME_NOT_READY  missing_data ["purchase_proposal"]
            Scenario.validate_quadruple_match
              「total_amount_krw must equal sourcing_plan amount total」
      ```

      ★ **한 기록 = 한 등급이다** (`differs_from_plan` 과 같은 규율). 그래서 기록값
        배분은 그 등급 한 줄이고, 총액이 정수 단가로 안 떨어질 때만 나머지 줄이 하나
        더 붙는다 (`_grade_lines`).
    """
    by_seq = {leg.seq: leg for leg in legs}
    out: dict[str, Any] = copy.deepcopy(dict(scenario))
    # ★ N5 는 약정 덮기(`with_purchase_record`)가 이미 일수로 읽히는지 막았다.
    days = (
        int(purchase_payment_days)
        if isinstance(purchase_payment_days, (int, float))
        and not isinstance(purchase_payment_days, bool)
        else None
    )

    split_plan = out.get("split_plan")
    if isinstance(split_plan, list):
        for index, raw in enumerate(split_plan, 1):
            if not isinstance(raw, dict):
                continue
            one = by_seq.get(int(raw.get("seq") or index))
            if one is None:
                continue
            raw["qty_kg"] = _as_number(one.qty_kg)
            raw["amount_krw"] = _as_number(one.amount_krw)
            raw["date"] = one.purchase_date.isoformat()
            raw["expected_arrival_date"] = one.arrival_date.isoformat()

    total_qty = sum(leg.qty_kg for leg in legs)
    out["total_qty_kg"] = _as_number(total_qty)
    out["total_amount_krw"] = _as_number(sum(leg.amount_krw for leg in legs))

    schedule = out.get("payment_schedule")
    max_price = out.get("max_price")
    if isinstance(schedule, list):
        for index, raw in enumerate(schedule, 1):
            if not isinstance(raw, dict):
                continue
            one = by_seq.get(int(raw.get("seq") or index))
            if one is None:
                continue
            raw["purchase_date"] = one.purchase_date.isoformat()
            if days is not None:
                raw["payment_date"] = (one.purchase_date + timedelta(days=days)).isoformat()
            raw["qty_kg"] = _as_number(one.qty_kg)
            raw["amount_krw"] = _as_number(one.amount_krw)
            if "amount_max_krw" in raw and isinstance(max_price, (int, float)):
                raw["amount_max_krw"] = _as_number(one.qty_kg * max_price)

    sourcing = out.get("sourcing_plan")
    if isinstance(sourcing, list):
        lines = [line for line in sourcing if isinstance(line, dict)]
        if lines:
            # ★ 등급 밖의 칸(지금은 `market`)은 선정안 첫 줄에서 그대로 온다 — 기록이
            #   시장을 바꾸지 않는다 (`PurchaseRecordIn` 은 시장을 받지 않는다).
            keep = {
                key: value
                for key, value in lines[0].items()
                if key not in {"grade", "qty_kg", "grade_unit_price"}
            }
            out["sourcing_plan"] = [
                {
                    **keep,
                    "grade": grade,
                    "qty_kg": _as_number(qty),
                    "grade_unit_price": _as_number(unit),
                }
                for qty, unit in _grade_lines(total_qty, sum(leg.amount_krw for leg in legs))
            ]
    return out


def recorded_unit_price(row: Mapping[str, Any]) -> float | None:
    """기록 한 줄의 단가. **`amount_krw ÷ quantity_kg` 다** (2026-09-16).

    ⚠️ 수량이 0 이거나 못 읽으면 `None` — 없는 것을 0 으로 채우지 않는다 (§1.2-10).
    """
    qty = float(row["quantity_kg"])
    return float(row["amount_krw"]) / qty if qty else None


def plan_unit_price(plan: ApprovedCommitment | None) -> float | None:
    """폼이 미리 채울 **선정안 단가**. 안이 적은 `sourcing_plan[].grade_unit_price` 다.

    🔴 **금액 ÷ 수량으로 지어내지 않는다** (2026-09-16). 그것은 안이 적은 단가가 아니라
      마스터가 만든 숫자다. 안에 단가가 없으면 `None` 으로 두고, 화면이 빈 칸으로 열어
      사람이 실제로 산 단가를 적게 한다 (§1.2-10).

    ⚠️ **등급 줄이 여럿이면 `None` 이다.** 줄마다 단가가 다를 수 있어 「이 회차의 단가」가
      하나로 정해지지 않는다 — 그중 하나를 집으면 근거 없는 값이 폼에 앉는다.
    """
    if plan is None or len(plan.sourcing_plan) != 1:
        return None
    return plan.sourcing_plan[0].grade_unit_price
