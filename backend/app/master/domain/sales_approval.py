"""판매 승인 판정 — 빠진 상거래 조건 · 재무 요약 검사, 판매 확정 입력 조립.

★ 2026-09-30 재구성 BL-018: `master/sales_approval.py` 에서 옮겼다 — `REQUIRED_COMMERCIAL_TERMS`,
  `_TERM_NAMES`, `missing_commercial_terms`, `REQUEST_MISSING_PREFIX`, `TERMS_UNRESOLVED_PREFIX`,
  `CONTRACT_FULFILLMENT_MODE`, `preferred_request_field`, `missing_term_origin_vocabulary`,
  `term_of_origin`, `missing_term_origins`, `_origin_of`, `_was_requested`, `missing_terms_reason`,
  `_names_with`, `_FINANCIAL_VALIDATION`, `REQUIRED_FINANCIAL_SUMMARY_FIELDS`, `_SUMMARY_NAMES`,
  `financial_summary_of`, `missing_financial_summary_fields`, `missing_financial_summary_reason`,
  `_decimal_of`, `confirmation_input`.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.contracts.envelope import Capability
from app.sales.schemas.proposal import SalesExecutionIdentity, SalesScenario
from app.sales.schemas.sale_ledger import (
    SalesApprovalLine,
    SalesConfirmationInput,
    SalesPersistenceConflict,
)

#: 🔴 **승인 가능한 후보의 필수조건.** 이 칸이 비어 있으면 사용자가 골라도 판매를
#: 확정할 수 없다 — `confirm_sale` 이 `sale_date` 와 수금 기일을 여기서 만든다
#: (`app/sales/domain/sale_ledger.py`: `due_date = sale_date + payment_days`).
#:
#: ★ **주인이 여기 하나다.** `sales_flow.CandidateVerdict` 가 후보를 사용자에게 올릴
#:   때 같은 목록을 읽는다 — 두 곳에 베껴 두면 *"올려도 되는 안"* 과 *"확정할 수 있는
#:   안"* 이 갈리는 날이 온다.
REQUIRED_COMMERCIAL_TERMS: tuple[str, ...] = ("delivery_date", "payment_days")

#: 사람이 읽는 이름. **칸 이름을 같이 적는다** — 화면이 무엇을 채워야 하는지 알아야 한다.
_TERM_NAMES: Mapping[str, str] = {
    "delivery_date": "납품일(delivery_date)",
    "payment_days": "수금 유예일(payment_days)",
}


def missing_commercial_terms(scenario: Mapping[str, Any]) -> tuple[str, ...]:
    """확정에 필요한 상업조건 중 **비어 있는 칸 이름**. 다 있으면 빈 튜플이다.

    ★ **`None` 만 없는 것으로 센다** (빈 문자열도 같이 센다 — JSON 왕복에서 날짜가
      `""` 로 오는 경우가 있다). `0` 은 없는 값이 아니다 — `payment_days=0` 은
      *"당일 수금"* 이라는 **정해진 조건**이다. `falsy` 로 세면 그 안이 조용히
      *"조건이 없는 안"* 이 된다.
    """
    return tuple(
        field
        for field in REQUIRED_COMMERCIAL_TERMS
        if scenario.get(field) is None or scenario.get(field) == ""
    )


#: 🔴 **호출에서 안 실렸다.** 부르는 쪽이 `preferred_<FIELD>` 를 안 보냈다 —
#: **고칠 사람은 화면 · 걷기 · API 호출자**다. 판매에 물어봐야 소용이 없다.
REQUEST_MISSING_PREFIX = "REQUEST_MISSING_"

#: 🔴 **정말 조건이 없다.** 보냈는데 결과에 안 실렸거나, 계약 이행 경로라 계약값에서
#: 나와야 하는데 안 나왔다 — **고칠 사람은 판매 · 계약**이다.
TERMS_UNRESOLVED_PREFIX = "TERMS_UNRESOLVED_"

#: 🔴 **계약 이행 경로.** 이 모드는 계약에 적힌 값을 쓰므로 마스터가 `preferred_*` 를
#: **안 보내는 것이 정상**이다. 여기서 `REQUEST_MISSING` 을 내면 없는 잘못을 화면에
#: 씌우는 것이 된다 — 호출자는 보낼 것이 없었다.
#:
#: ⚠️ **어휘의 주인은 판매다** (`SalesBusinessMode`). 그런데 `app.master.schemas` 를
#:   import 할 수 없다 — 저쪽이 이 모듈이 사는 `sales_flow` 를 import 하므로 고리가
#:   된다. 값이 갈리면 `test_missing_term_origin.py` 가 잡는다.
CONTRACT_FULFILLMENT_MODE = "CONTRACT_FULFILLMENT"


def preferred_request_field(field: str) -> str:
    """상업조건 칸 이름 → **사용자 요청에서 그 값을 싣는 칸 이름.**

    ```text
    delivery_date  ↔  user_request["preferred_delivery_date"]
    payment_days   ↔  user_request["preferred_payment_days"]
    ```

    ★ **대응이 여기 한 자리다.** 두 곳에 적으면 판매가 칸 이름을 바꾼 날 한쪽만
      고쳐지고, 그러면 *"보냈는데 안 실렸다"* 가 조용히 *"안 보냈다"* 로 뒤집힌다.
    """
    return f"preferred_{field}"


def missing_term_origin_vocabulary() -> tuple[str, ...]:
    """이 판이 낼 수 있는 어휘 전부. **`REQUIRED_COMMERCIAL_TERMS` 에서 나온다.**

    ★ **손으로 나열하지 않는다.** 상수가 늘면 어휘도 같이 늘어야 한다 — 베껴 두면
      새 칸이 하나 늘 때 그 칸만 원인 없이 나가는 날이 온다.
    """
    return tuple(
        f"{prefix}{field}"
        for field in REQUIRED_COMMERCIAL_TERMS
        for prefix in (REQUEST_MISSING_PREFIX, TERMS_UNRESOLVED_PREFIX)
    )


def term_of_origin(origin: str) -> str:
    """어휘에서 **칸 이름을 되꺼낸다.** 화면이 필드 이름만 필요할 때 쓴다.

    ★ 접두를 몰라도 되게 여기서 벗긴다 — 화면이 문자열을 직접 자르기 시작하면
      접두를 바꾸는 날 화면이 조용히 틀린 이름을 보여 준다.
    """
    for prefix in (REQUEST_MISSING_PREFIX, TERMS_UNRESOLVED_PREFIX):
        if origin.startswith(prefix):
            return origin[len(prefix) :]
    return origin


def missing_term_origins(
    scenario: Mapping[str, Any],
    *,
    user_request: Mapping[str, Any] | None = None,
    business_mode: str | None = None,
) -> tuple[str, ...]:
    """비어 있는 상업조건이 **어디서 끊겼는지.** 다 있으면 빈 튜플이다.

    ★ **더하는 것은 "어디서" 하나다.** *"무엇이 없나"* 는 `missing_commercial_terms`
      가 답하고 여기서 다시 세지 않는다 — 두 곳에서 세면 갈린다.

    ```text
    business_mode 가 CONTRACT_FULFILLMENT 가 **아니고**
      · user_request 에 preferred_<FIELD> 가 없음   → REQUEST_MISSING_<FIELD>
    그 밖 (계약 이행 경로거나 · 보냈는데 결과에 없음) → TERMS_UNRESOLVED_<FIELD>
    ```

    🔴 **`CONTRACT_FULFILLMENT` 를 가른다.** 그 경로는 계약값을 쓰므로 마스터가
      `preferred_*` 를 안 보내는 것이 정상이다 — 거기서 `REQUEST_MISSING` 을 내면
      **거짓말이 된다.**

    ⚠️ **`user_request` 를 안 주면 `REQUEST_MISSING` 이다.** 그것도 사실이다 —
      호출에 그 값이 실리지 않았다. 부를 때 들고 있으면 반드시 넘긴다.
    """
    return tuple(
        _origin_of(field, user_request=user_request, business_mode=business_mode)
        for field in missing_commercial_terms(scenario)
    )


def _origin_of(
    field: str,
    *,
    user_request: Mapping[str, Any] | None,
    business_mode: str | None,
) -> str:
    """칸 하나의 원인. **가르는 규칙은 여기 한 줄이다.**"""
    if business_mode != CONTRACT_FULFILLMENT_MODE and not _was_requested(field, user_request):
        return f"{REQUEST_MISSING_PREFIX}{field}"
    return f"{TERMS_UNRESOLVED_PREFIX}{field}"


def _was_requested(field: str, user_request: Mapping[str, Any] | None) -> bool:
    """부르는 쪽이 그 값을 **실었는가.**

    ★ **`missing_commercial_terms` 와 같은 셈법이다.** `0` 은 실린 값이다 —
      `preferred_payment_days=0` 은 *"당일 수금을 원한다"* 는 **정해진 요청**이고,
      `falsy` 로 세면 그 요청이 조용히 *"안 보냈다"* 가 되어 판매의 잘못이 화면
      잘못으로 뒤집힌다.
    """
    if user_request is None:
        return False
    value = user_request.get(preferred_request_field(field))
    return value is not None and value != ""


def missing_terms_reason(origins: tuple[str, ...]) -> str:
    """왜 확정할 수 없나 — **무엇이 없는지 이름을 부르고 어디서 끊겼는지 붙인다.**

    ★ 부서 판정 문장(`capability(runtime/business)`)과 **모양이 다르다.** 탈락 사유가
      *"재무가 반려"* 처럼 보이면 사람이 재무를 본다. *"납품일이 없다"* 로 보여야
      판매를 본다.

    ★ **한 함수가 두 경우를 다 낸다.** 문장을 두 벌로 두면 한쪽만 고치는 날이 온다.

    :param origins: `missing_term_origins` 가 낸 어휘. 접두 없는 칸 이름을 넣어도
        읽히지만 그때는 원인 절이 안 붙는다 — 원인을 아는 자리에서 부른다.
    """
    names = ", ".join(_TERM_NAMES.get(term_of_origin(o), term_of_origin(o)) for o in origins)
    parts = [f"{names} 이(가) 없어 판매를 확정할 수 없다 — 없는 값을 지어내지 않는다."]
    requested = _names_with(origins, REQUEST_MISSING_PREFIX)
    unresolved = _names_with(origins, TERMS_UNRESOLVED_PREFIX)
    if requested:
        parts.append(f"요청에 안 실렸다: {requested} — 화면·호출자가 채운다.")
    if unresolved:
        parts.append(f"조건이 정해지지 않았다: {unresolved} — 판매·계약이 정한다.")
    return " ".join(parts)


def _names_with(origins: tuple[str, ...], prefix: str) -> str:
    """그 원인에 해당하는 칸들의 사람 이름. 없으면 빈 문자열이다."""
    fields = [term_of_origin(o) for o in origins if o.startswith(prefix)]
    return ", ".join(_TERM_NAMES.get(field, field) for field in fields)


# ---------------------------------------------------------------------------
# 🔴 재무가 낸 기여이익 — **되먹임이 없는 안에는 이 길뿐이다** (2026-09-11)
# ---------------------------------------------------------------------------


#: 재검증에서 이 값을 낸 검증. 🔴 **어휘의 주인은 `envelope.Capability` 다** —
#:   `Literal` 이라 오타가 타입 검사에서 걸린다.
_FINANCIAL_VALIDATION: Capability = "FINANCIAL_VALIDATION"

#: 확정에 필요한 재무 요약 칸.
#:
#: ★★ **왜 이 길이 유일한가** (실측 2026-09-11).
#:
#:   ```text
#:   app/sales/domain/sale_ledger.py  _line_profit
#:     ① line.contribution_profit_krw      ← 마스터가 넘긴다   ← 🟢 이 길
#:     ② scenario.contribution_margin_krw  ← **비어 있다**
#:     ③ 없으면 SalesPersistenceConflict("missing contribution profit")
#:   ```
#:
#:   ②가 비는 이유는 사고가 아니라 **설계다.** 판매는 그 값을 되먹임 회신에서 받아
#:   적는데(`proposal.py:216`), **통과한 후보는 되먹임을 안 받는다** (계약 `C-1` ·
#:   `sales_flow:723`). 실측에서 `feedback_attempts` 전건 0 · 후보 `revision` 전건 0
#:   이었다. 그래서 **통과한 안은 영원히 확정될 수 없었다.**
#:
#:   🔴 **`C-1` 은 옳아서 안 건드렸다.** 통과한 안에 되먹임을 걸면 사용자가 볼 수
#:     있던 안이 바뀐다. 고리는 **마스터가 값을 날라서** 푼다.
REQUIRED_FINANCIAL_SUMMARY_FIELDS: tuple[str, ...] = (
    "contribution_margin_krw",
    "contribution_margin_rate",
)

#: 사람이 읽는 이름. `_TERM_NAMES` 와 같은 자리다.
_SUMMARY_NAMES: Mapping[str, str] = {
    "contribution_margin_krw": "기여이익(contribution_margin_krw)",
    "contribution_margin_rate": "기여이익률(contribution_margin_rate)",
}


def financial_summary_of(
    validations: Mapping[str, Mapping[str, Any]] | None,
) -> Mapping[str, Any] | None:
    """재검증 판정에서 **재무가 낸 요약**을 꺼낸다. 없으면 `None`.

    ```text
    validations["FINANCIAL_VALIDATION"]["payload"]["financial_summary"]
    ```

    🔴 **재검증이 낸 것이다 — 첫 검증이 아니다.** 확정은 재검증 **뒤에** 서므로,
      재검증이 그날 사실로 다시 센 값이 정본이다. 첫 검증 값
      (`candidates[].validations…`)을 쓰면 **「제안 시점 사실」로 장부가 서고**, 그
      사이 재고·원가가 움직인 것이 사라진다.

    ★ **여기가 이 매핑의 주인이다.** 부르는 쪽(`decision_service._sale_for`)이 세
      겹을 직접 파고들면 재무가 payload 모양을 바꾸는 날 그 자리가 조용히 `None` 이
      된다 — 그리고 `None` 은 *"재무가 안 냈다"* 와 구별되지 않는다.

    ⚠️ **`payload` 는 2026-09-11 에야 열렸다** (`revalidation._verdict_of`). 그전에는
      재검증이 그 칸을 버려서 여기서 꺼낼 것이 아무것도 없었다.
    """
    if validations is None:
        return None
    verdict = validations.get(_FINANCIAL_VALIDATION)
    if not isinstance(verdict, Mapping):
        return None
    payload = verdict.get("payload")
    if not isinstance(payload, Mapping):
        return None
    summary = payload.get("financial_summary")
    return summary if isinstance(summary, Mapping) else None


def missing_financial_summary_fields(
    financial_summary: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    """확정에 필요한 재무 칸 중 **비어 있는 칸 이름**. 다 있으면 빈 튜플이다.

    ★ **`missing_commercial_terms` 와 같은 모양이다** — `None` 과 빈 문자열만 없는
      것으로 센다. `0` 은 없는 값이 아니다: `contribution_margin_krw=0` 은
      *"기여이익이 0 으로 확인됐다"* 는 **정해진 사실**이고, `falsy` 로 세면 그
      사실이 조용히 *"재무가 안 냈다"* 가 된다.

    🔴 **요약 자체가 없으면 두 칸 다 없는 것이다.** 통째로 `None` 인 것을 빈 튜플로
      내면 *"다 있다"* 가 되어 그 다음 줄이 `None` 을 장부에 싣는다.
    """
    if financial_summary is None:
        return REQUIRED_FINANCIAL_SUMMARY_FIELDS
    return tuple(
        field
        for field in REQUIRED_FINANCIAL_SUMMARY_FIELDS
        if financial_summary.get(field) is None or financial_summary.get(field) == ""
    )


def missing_financial_summary_reason(fields: tuple[str, ...]) -> str:
    """왜 확정할 수 없나 — **무엇이 없는지 이름을 부르고 누가 채우는지 붙인다.**

    ★ **`missing_terms_reason` 과 같은 모양이고 문장만 다르다.** 저쪽은 화면·판매가
      채우는 칸이고 여기는 **재무가 내는 값**이다 — 사람이 다음에 볼 자리가 다르므로
      문장이 그 자리를 가리켜야 한다.

    🔴 **0 으로 채우고 통과시키지 않는다.** 기여이익 0 으로 장부가 서면 그날의
      손익이 거짓이 되고, 그 거짓은 터지지 않는다 — 숫자만 틀린다.
    """
    names = ", ".join(_SUMMARY_NAMES.get(field, field) for field in fields)
    return (
        f"재무 판정에 {names} 이(가) 없어 판매를 확정할 수 없다"
        " — 없는 값을 지어내지 않는다. 재검증의 FINANCIAL_VALIDATION 이 채운다."
    )


def _decimal_of(value: Any) -> Decimal | None:
    """재무가 낸 수를 **그대로** `Decimal` 로 옮긴다. 못 옮기면 `None`.

    🔴 **계산이 아니다.** 수량 × 단가 − 원가 를 여기서 세면 그 순간 마스터가 재무가
      된다 (설계 ④). 이 함수는 **표현만** 바꾼다 — 값도 자릿수도 안 건드린다.

    ⚠️ **경로마다 타입이 다르다.** in-process 에서는 재무가 낸 `Decimal` 이 그대로
      오고, 이력을 한 번 왕복하면 `float` 나 문자열이 온다. `str` 을 거쳐 옮기는
      것이 `Decimal(float)` 의 이진 꼬리를 안 들이는 유일한 길이다.

    ⚠️ **`bool` 을 막는다.** 파이썬에서 `True` 는 `1` 이라 `Decimal(str(True))` 가
      아니라 그 앞에서 거른다 — 판매 스키마도 `_reject_boolean` 으로 같은 자리를
      막는다.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def confirmation_input(
    *,
    request_id: str,
    run_id: str,
    as_of: date,
    policy_version: str | None,
    scenario: Mapping[str, Any],
    financial_summary: Mapping[str, Any],
    sim_run_id: str,
) -> SalesConfirmationInput:
    """`SalesConfirmationInput` 을 짓는다. **판매가 발표한 계약 그대로다.**

    ```text
    sale_date   scenario 의 delivery_date   납품일 정본은 sales.sale_date (판매 확정)
    order_date  그 실행의 as_of              판매·재무 확정 ③ — 벽시계가 아니다
    sim_run_id  원 실행 이력 행의 축          어느 실행의 장부인가는 마스터가 정한다
    line.기여이익  재검증의 재무 판정         🔴 되먹임 없는 안에는 이 길뿐이다
    source_order_id  이 실행의 업무 키        🔴 이 판매를 낳은 판단의 이름이다
    ```

    🔴 **`sale_id` 와 `source_order_id` 는 같은 것을 안 가리킨다** (2026-09-11).

      ```text
      sale_id          SALE-{run_id}-{scenario_id}          ← **실행 축**
                       그날 어느 실행 행이 이 판매를 낳았나 (판매가 짓는다)
      source_order_id  REQ-DAILY-SALES-{실행}-{날짜}-{품목}   ← **업무 축**
                       무엇에 대한 판단이었나 — 어느 실행의 · 어느 날의 · 어느 품목
      ```

      ★★ **두 축이 다르다.** `run_id` 는 그 판단이 **몇 번째로 돈 것**인지이고, 업무
        키는 **무엇을 판단한 것**인지다. 같은 업무 키가 여러 `run_id` 를 가질 수 있다
        (재시도). 그래서 한 칸으로 접히지 않는다 — 접으면 *"같은 판단의 두 번째
        실행"* 과 *"다른 판단"* 을 장부가 더 이상 못 가른다.

      🔴 **둘 다 이 판매를 가리킨다는 이유로 하나를 지우지 마라.** 가리키는 방향이
        다르다: 하나는 위로(실행 이력), 하나는 옆으로(업무 키) 간다.

      ★ **지어낸 값이 아니다.** 이 시뮬레이션에서 판매를 낳은 것은 **승인된 마스터
        판단**이고, 「원본 주문 ID」가 실제로 가리키는 것이 그것이다 — 없는 주문
        번호를 만들어 넣는 자리가 아니다.

      🟡 **판매 계약(`SalesConfirmationInput.source_order_id`)은 여전히 `str | None`
        이다.** 표는 `NOT NULL` 이지만 계약을 필수로 바꿀지는 **판매가 정할 자리**라
        건드리지 않는다. 마스터는 **늘 싣는 것**으로 제 쪽을 잠근다
        (`tests/master/test_sale_carries_business_key.py`).

    🔴 **`sim_run_id` 는 상수가 아니다** (2026-09-11). 전에는 `BURN_IN_SIM_RUN_ID` 를
      박았는데, 이 값이 `app/sales/repository/sale_ledger.py` 의 `sales` INSERT 에 그대로
      실린다 — **일어난 적 없는 판매가 번인 장부에 쌓인다.** 번인은 모든 실행이
      `--baseline-run-id` 로 출발점 삼는 장부라 그 오염이 뒤따르는 실행 전부에 번진다.

      ⚠️ **터지지 않는다. 숫자만 틀린다.** 그래서 상수를 지우고 기본값도 안 둔다 —
        안 넘기면 그 자리에서 터져야 한다 (`revalidate_scenario` 와 같은 규율).

    :param sim_run_id: 원 실행 이력 행이 실은 축. 🔴 **여기서 짓지 않는다.**

    🔴 **옛 주석이 거짓이었다** (2026-09-11). 그 문장은 이랬다.

      ```text
      ★ 기여이익을 마스터가 다시 적지 않는다. line.contribution_profit_krw 와
        contribution_margin_rate 를 비워 두면 판매가 scenario 값을 쓴다
        (_line_profit). 여기에 값을 베껴 넣으면 같은 사실이 두 곳에 남는다.
      ```

      ★★ **통과 경로를 안 본 문장이다.** *"판매가 scenario 값을 쓴다"* 가 참이려면
        `scenario.contribution_margin_krw` 에 값이 있어야 하는데, **통과한 안에는 그
        값이 없다.** 판매는 그 값을 되먹임 회신에서 받아 적고(`proposal.py:216`),
        **통과한 후보는 되먹임을 안 받는다** (계약 `C-1`). 고리가 닫혀 있었고,
        그래서 통과한 안은 영원히 확정될 수 없었다 (실측: `sales` 0행).

      🟢 **지금도 마스터가 값을 「짓지」는 않는다.** 재무가 낸 것을 **읽어서 나른다** —
        수량 × 단가 − 원가 를 여기서 세지 않는다. 그 순간 마스터가 재무가 된다.
        `tests/master/test_finance_margin_carried.py` 가 이 함수 안에 산술 연산이
        없는지를 AST 로 지킨다.

      ⚠️ **같은 사실이 두 곳에 남는 것**은 맞다. 주인은 **재무**이고 여기는 그것을
        옮기는 자리다 — 그래서 값을 고르지도 고치지도 않고 두 칸을 그대로 옮긴다.

    ★ **`grade` 는 `None` 이다.** `SalesScenario` 에 등급 칸이 없다 — 없는 값을
      지어내지 않는다.

    :param financial_summary: 재검증의 재무 판정이 낸 요약. 🔴 **비어 있지 않은
        것은 부르는 쪽이 이미 확인했다** (`missing_financial_summary_fields`).
    """
    selected = SalesScenario.model_validate(dict(scenario))
    if selected.delivery_date is None:
        # ★ `missing_commercial_terms` 가 먼저 막는다. 여기는 최후 방어다 — 그 검사를
        #   지우면 이 줄이 터져서 알려 준다.
        raise SalesPersistenceConflict("selected scenario is missing delivery_date")
    if selected.quantity_kg is None or selected.unit_price_krw is None:
        raise SalesPersistenceConflict("selected scenario is missing quantity or unit price")
    return SalesConfirmationInput(
        execution_identity=SalesExecutionIdentity(
            request_id=request_id,
            run_id=run_id,
            as_of=as_of,
            policy_version=policy_version,
        ),
        selected_scenario=selected,
        selected_scenario_id=selected.scenario_id,
        sim_run_id=sim_run_id,
        sale_date=selected.delivery_date,
        order_date=as_of,
        # 🔴 **업무 키를 그대로 싣는다.** 위 표의 두 축 설명이 이 한 줄의 이유다 —
        #    `sale_id` 와 같은 값을 넣으면 그 순간 한 칸이 거짓말이 된다.
        source_order_id=request_id,
        line=SalesApprovalLine(
            item_name=selected.item,
            quantity_kg=selected.quantity_kg,
            unit_price_krw_per_kg=selected.unit_price_krw,
            grade=None,
            # 🔴 **재무가 낸 것을 그대로 옮긴다.** `_line_profit` 이 이 칸을 **먼저**
            #    보고, 없으면 `scenario.contribution_margin_krw` 를 보는데 통과한
            #    안에는 그 값이 없다 (계약 `C-1`).
            contribution_profit_krw=_decimal_of(
                financial_summary.get("contribution_margin_krw")
            ),
            contribution_margin_rate=_decimal_of(
                financial_summary.get("contribution_margin_rate")
            ),
        ),
    )
