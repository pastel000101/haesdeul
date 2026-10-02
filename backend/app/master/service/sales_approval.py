"""
sales_approval.py — 판매 승인 → 판매 확정 (`confirm_sale`)

사람이 판매안을 승인하면 그 안이 `sales` · `sale_items` 에 `CONFIRMED` 로 선다.
그 자리가 여기다.

```text
사용자 승인
 → 선택 scenario 를 그 실행의 as_of 로 재검증 (revalidation.revalidate_scenario)
 → PASSED 면 confirm_sale() + 확정분 예약
 → sales · sale_items · CONFIRMED
 → 그 뒤는 하루 순서 (출고 날 할당 → 출고 → DELIVERED → 채권)
```

`service/transition.py` 의 `apply_approval` 을 재사용하지 않는다. 저쪽은 매입 약정
(`ApprovedCommitment`)을 받아 재무·물류 장부를 바꾼다 — 판매 확정은 받는 것도 바꾸는
것도 다르다. 한 함수로 묶으면 그 함수가 두 사이클의 장부를 다 알게 된다.

승인 즉시 = 판매 확정. 승인 즉시 ≠ 출고 즉시(2026-09-08 계약). 여기서 서는 것은
`sales.order_status = 'CONFIRMED'` 와 확정분 예약이고, 할당·출고·`DELIVERED`·채권은 그
뒤 하루 순서가 자기 날에 한다. 이 모듈이 출고를 부르면 승인이 곧 출고가 되고, 그러면
"승인했지만 아직 안 나갔다" 라는 상태가 사라진다.

없는 값을 지어내지 않는다. `delivery_date` · `payment_days` 가 비어 있으면 `BLOCKED`
이고 무엇이 없는지 이름을 부른다. `as_of + N` 같은 값을 만들면 그 `N` 이 곧 업무
규칙이 되고, 그 날짜가 출고일·수금일·곡선의 시점이 된다 — 아무도 정한 적이 없는데.

판매가 재무·물류를 직접 부르지 않는다. 잇는 것은 마스터다(§3.2.2). 이 모듈이 아는
판매 쪽 이름은 발표된 입력 계약(`SalesConfirmationInput`)과 그것을 받는 함수
(`confirm_sale`), 예약 요청을 만드는 projection(`outbound_reservation_for_sale`)이고,
무슨 값을 어느 칸에 어떤 SQL 로 쓸지는 판매가 안다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from app.contracts.sales_logistics import SalesOutboundReservationRequest
from app.core import db as core_db
from app.logistics.service.outbound import reserve_confirmed_sale_available
from app.master.domain.sales_approval import (
    confirmation_input,
    missing_financial_summary_fields,
    missing_financial_summary_reason,
    missing_term_origins,
    missing_terms_reason,
)
from app.master.schemas.sales_approval import SaleConfirmationOut
from app.sales.domain.logistics_request import outbound_reservation_for_sale
from app.sales.schemas.sale_ledger import SalesConfirmationInput, SalesPersistenceConflict
from app.sales.service.sale_ledger import confirm_sale


def confirm_approved_sale(
    *,
    request_id: str,
    run_id: str,
    as_of: date | None,
    policy_version: str | None,
    scenario: Mapping[str, Any],
    revalidation_outcome: str | None,
    financial_summary: Mapping[str, Any] | None,
    sim_run_id: str,
    confirm: Callable[[Any, SalesConfirmationInput], Any] | None = None,
    reserve: Callable[[Any, SalesOutboundReservationRequest], Any] | None = None,
    borrow: core_db.Borrow | None = None,
) -> SaleConfirmationOut:
    """승인된 판매안을 판매 원장에 확정한다.

    순서가 이 함수의 전부다.

    ```text
    1. 재검증 통과 확인   PASSED 가 아니면 여기서 끝난다 — confirm_sale 을 안 부른다
    2. 상업조건 확인      없는 값을 지어내지 않는다 → BLOCKED (이름을 부른다)
    3. 기여이익 확인      재무가 안 냈으면 지어내지 않는다 → BLOCKED (이름을 부른다)
    4. 기준일 확인        order_date 는 그 실행의 as_of 다 — 벽시계가 아니다
    5. 업무 키 확인       source_order_id 가 될 값이다 — 없으면 지어내지 않는다
    6. 입력 계약 조립     커넥션 밖에서 (실패해도 DB 를 안 건드린다)
    7. confirm_sale       한 커넥션 · commit 한 번 · 실패하면 rollback
    8. 확정분 예약        7 이 성공한 뒤에만 — 같은 커넥션 · 같은 커밋
    ```

    확정이 서면 그 자리에서 재고를 잡는다. 예약을 출고 날(D+1)에야 걸면 그 사이 하루
    동안 `held_qty_kg` 가 오르지 않아 같은 재고가 다음 날 또 팔린다(`SIM-CHAIN-V4` 실측:
    확정 34건 중 14건 15,474kg 미출고).

    멱등: 출고(`ship_due_sales`)가 같은 예약을 또 불러도 이중 예약이 나지 않는다. 예약
    이름을 `reservation_id_for_sale_item` 이 `sale_item_id` 에서 계산하고, 출고가 같은
    함수로 같은 값을 계산한다 — 새로 지으면 번호가 갈려 정확히 이중 예약이 난다.
    그래서 이름을 여기서 짓지 않고 판매의 projection(`outbound_reservation_for_sale`)이
    만든 요청을 그대로 넘긴다.

    모자라도 `BLOCKED` 로 되돌리지 않는다. 확정은 이미 일어난 사실이고 장부에 섰다 —
    못 잡은 몫은 `reservation_outcome` 과 `reserved_qty_kg` 로 보이게 남는다(물류의
    `reserve_confirmed_sale_available` 과 같은 태도).

    재검증이 막히면 부르지 않는다. `CONDITIONAL` 도 통과가 아니다 — 사용자가 승인한
    대상은 그때 화면에 있던 그 안이고, 새 조건이 붙으면 다른 안이다
    (`schemas/decision.py` 의 `RevalidationOutcome`).

    실패 처리: 예외를 밖으로 던지지 않는다. 이 함수가 불릴 때 결정은 이미 적재됐다.
    확정 실패가 예외로 올라가면 라우터가 500 을 내고, 사람이 보기에는 승인이 실패한
    것이 된다 — 실제로는 승인은 남았고 원장만 안 선 것이다(`apply_approval` 과 같은
    규율).

    :param as_of: 그 실행의 기준일. `order_date` 가 되고, 벽시계가 아니다(판매·재무
        확정 ③). 못 읽으면 `None` 이고 그때는 `BLOCKED` 다.
    :param financial_summary: 재검증의 재무 판정이 낸 요약(`financial_summary_of` 가
        꺼낸다). 기여이익과 기여이익률이 여기서 온다 — 되먹임을 받지 않는 안에는 이
        길뿐이다. 기본값이 없다: 안 넘기면 실패해야 한다. 없으면(`None`) 지어내지 않고
        `BLOCKED` 다.
    :param sim_run_id: 그 실행의 축. `sales` 행이 어느 장부에 앉는지를 정한다
        (`app/sales/repository/sale_ledger.py` 의 INSERT). 기본값이 없다 — 안 넘기면
        실패해야 한다. 부르는 쪽이 재검증에 넘긴 것과 같은 한 값이어야 한다:
        `service/decision.py` 의 `record_decision` 이 행에서 한 번 읽어 둘에 흘린다.
    :param confirm: 확정 함수. 안 주면 `app.sales.service.sale_ledger.confirm_sale` 이다.
    :param reserve: 확정분 예약 함수. 안 주면
        `app.logistics.service.outbound.reserve_confirmed_sale_available` 이다.
        전량형 `reserve_stock` 이 아니다 — 저쪽은 전량 아니면 멈추고, 여기서 멈추면
        이미 선 확정이 예외로 되돌아간다.
    :param borrow: 연결을 빌려 주는 함수. 안 주면 `app.core.db.connection`(공통 풀)이다.
        판매 원장과 물류 예약이 같은 서비스 DB 에 있어 연결 하나로 한 번 commit 한다.
    """
    if revalidation_outcome != "PASSED":
        # 여기서 돌아선다 — `confirm_sale` 을 부르지 않고 커넥션도 열지 않는다.
        shown = revalidation_outcome or "안 돌았다"
        return SaleConfirmationOut(
            status="BLOCKED",
            reason=f"재검증이 통과하지 않아 판매를 확정하지 않았다 (재검증 결과: {shown}).",
        )

    missing = missing_term_origins(scenario)
    if missing:
        return SaleConfirmationOut(
            status="BLOCKED",
            reason=missing_terms_reason(missing),
            missing_terms=list(missing),
        )

    # 기여이익을 지어내지 않는다. 0 으로 채우면 그날 손익이 거짓이 되고,
    # 그 거짓은 오류를 내지 않는다 — 숫자만 틀린다.
    #
    # `missing_terms` 에 담지 않는다. 저 칸의 어휘는 `missing_term_origins` 가
    # 내는 `REQUEST_MISSING_*` · `TERMS_UNRESOLVED_*` 이고 "화면·판매·계약이
    # 채운다" 는 뜻이다. 재무가 안 낸 값을 그 어휘에 섞으면 사람이 엉뚱한 파트를
    # 보러 간다 — 사유 문장이 재무를 가리킨다.
    없는재무칸 = missing_financial_summary_fields(financial_summary)
    if 없는재무칸:
        return SaleConfirmationOut(
            status="BLOCKED",
            reason=missing_financial_summary_reason(없는재무칸),
        )

    if as_of is None:
        # 오늘로 대신 채우지 않는다. `order_date` 는 그 실행이 선 날이고,
        # 못 읽었으면 모르는 것이다 — 모르는 날짜를 지어내면 수금 곡선의 시점이 틀린다.
        return SaleConfirmationOut(
            status="BLOCKED",
            reason="원 실행의 기준일(as_of)을 못 읽어 주문일을 정할 수 없다.",
        )

    if not request_id:
        # 업무 키를 지어내지 않는다. `source_order_id` 가 될 값이고, 그 칸이
        # 가리키는 것은 이 판매를 낳은 마스터 판단이다. 못 읽었으면 "무엇에 대한
        # 판단이었나" 를 모르는 것이라, `or "UNKNOWN"` 으로 메우면 원장에 아무도
        # 안 내린 판단이 원본 주문으로 선다.
        #
        # 계약의 `min_length=1` 에 기대지 않는다. 거기까지 흘려 보내면 사유가
        # "판매가 입력을 거부했다" 가 되는데, 실제로는 마스터가 값을 못 만든
        # 것이다. 둘은 다른 사실이라 여기서 먼저 돌아서고 이름을 부른다.
        return SaleConfirmationOut(
            status="BLOCKED",
            reason=(
                "이 판매를 낳은 마스터 업무 키(request_id)를 못 읽어 "
                "원본 주문(source_order_id)을 정할 수 없다."
            ),
        )

    try:
        # 커넥션 밖에서 조립한다(`apply_approval` 과 같은 규율). 계약 위반은
        # 흔한 일인데, 커넥션을 연 뒤에 실패하면 열린 트랜잭션이 남는다.
        confirmation = confirmation_input(
            request_id=request_id,
            run_id=run_id,
            as_of=as_of,
            policy_version=policy_version,
            scenario=scenario,
            financial_summary=financial_summary,
            sim_run_id=sim_run_id,
        )
    except (ValidationError, SalesPersistenceConflict, ValueError) as exc:
        # `FAILED` 가 아니다. 계약이 안 맞아 쓸 수 없는 것은 우리가 아는
        # 사실이지 실패가 아니다.
        return SaleConfirmationOut(status="BLOCKED", reason=f"판매 확정 입력을 만들 수 없다: {exc}")

    do_confirm = confirm_sale if confirm is None else confirm
    do_reserve = reserve_confirmed_sale_available if reserve is None else reserve
    open_connection = core_db.connection if borrow is None else borrow
    with open_connection() as conn:
        # 어느 단계에서 실패했나. 사유가 "확정이 안 섰다" 와 "확정은 섰는데
        # 재고를 못 잡았다" 를 가르지 못하면 다음 사람이 또 손으로 재현해야 한다.
        예약단계 = False
        try:
            result = do_confirm(conn, confirmation)
            # 여기부터가 8 이다. `confirm_sale` 이 성공한 뒤에만 온다 —
            # 순서를 바꾸면 안 선 확정의 재고를 잡는다.
            예약단계 = True
            # 요청을 손으로 조립하지 않는다. 예약 이름을 포함해 판매의 projection
            # 이 만든다 — 여기서 문자열을 지으면 출고가 계산하는 이름과 갈린다.
            #
            # as_of = 가용량 판정 기준일 = 납품일. 확정일이 아니다
            # (D/D+1 신선도 절벽 · 물류 문서 24).
            #
            #    ```text
            #    예약이 서는 시점     확정일 D     그대로 — 하루 사이 이중판매를 막는 자리
            #    재고를 보는 기준일   납품일 D+1   할당(`_ship_one`)이 보는 날과 같아야 한다
            #    ```
            #
            #    확정일로 보면 D 에 잔여 1일인 Lot 을 예약이 세고, D+1 에 0일이 되어 할당이
            #    못 쓴다 — `OutboundIntegrityError` 로 실패하고 그 판매는 못 나간다
            #    (REH-0914 배추 02-10: 확보 3,586kg · 납품일 가용 2,870kg).
            #    값은 판매 확정 입력의 `sale_date` 다 — 날짜를 여기서 다시 짓지 않는다.
            예약요청 = outbound_reservation_for_sale(
                result, sim_run_id=sim_run_id, as_of=confirmation.sale_date
            )
            예약 = do_reserve(conn, 예약요청)
            요구량 = Decimal(str(예약.required_qty_kg))
            확보량 = Decimal(str(예약.reserved_qty_kg))
            conn.commit()
        except SalesPersistenceConflict as exc:
            # 판매가 "이 사실로는 확정할 수 없다" 고 말한 것이다. 문장은 판매가 쓴
            # 것을 그대로 옮긴다 — 마스터가 다시 쓰면 사유의 주인이 둘이 된다.
            conn.rollback()
            return SaleConfirmationOut(status="BLOCKED", reason=f"판매가 확정을 막았다: {exc}")
        except Exception as exc:  # noqa: BLE001 - 확정 실패가 적재된 결정을 지우면 안 된다.
            conn.rollback()
            if 예약단계:
                # 확정까지 같이 물러난다. 확정만 서고 예약이 없는 상태가 바로
                # 같은 재고를 두 번 파는 자리라, 그 상태로 커밋하느니 안 선 것이 낫다.
                return SaleConfirmationOut(
                    status="FAILED",
                    reason=f"확정분 예약 적재 실패 — 확정까지 롤백했다: {exc}",
                )
            return SaleConfirmationOut(status="FAILED", reason=f"판매 확정 적재 실패: {exc}")

    모자람 = 확보량 < 요구량
    return SaleConfirmationOut(
        status="CONFIRMED",
        reason=(
            "판매를 확정했다 (CONFIRMED). 출고는 이 승인이 하지 않는다."
            if not 모자람
            else (
                "판매를 확정했다 (CONFIRMED). 출고는 이 승인이 하지 않는다. "
                f"확정분 예약이 모자란다 — 요구 {요구량}kg 중 {확보량}kg 만 잡혔다."
            )
        ),
        sale_id=getattr(result, "sale_id", None),
        sale_item_id=getattr(result, "sale_item_id", None),
        reservation_id=예약요청.reservation_id,
        required_qty_kg=요구량,
        reserved_qty_kg=확보량,
        reservation_outcome="SHORT" if 모자람 else "RESERVED",
    )
