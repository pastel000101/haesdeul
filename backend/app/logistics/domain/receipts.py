"""도착 Receipt 의 판정 — Receipt ID · 조회 열쇠 검사 · 읽은 행 해석 · 적을 매입 사실 검사.

★ 2026-09-30 재구성 BL-015: `logistics/receipts.py` 에서 옮겼다. DB 를 만지지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.logistics.domain.arrival import DueInbound
from app.logistics.schemas.purchase_detail import PurchaseDetail
from app.logistics.schemas.receipts import (
    RECEIPT_STATUSES,
    InvalidInboundIdentity,
    ReceiptExistence,
    ReceiptFactsMissing,
    ReceiptIntegrityError,
    ReceiptRowUnreadable,
)


def receipt_id_for(*, sim_run_id: str, inbound_id: str) -> str:
    """Receipt 행의 PK. **순수 계산이고 결정론이다.**

    ```text
    RCPT-{sim_run_id}-{inbound_id}
    → RCPT-SIM-BURNIN-202512-INB-H1-THRU-20260105-BAECHU-1-1
    ```

    🔴 **난수 · 시계 · DB 시퀀스를 쓰지 않는다.** 같은 `(sim_run_id, inbound_id)` 는
       몇 번을 불러도 같은 값이어야 재시도가 멱등해진다 — 마스터 `purchase_id_for` ·
       물류 `inbound_id` 가 같은 이유로 결정론이다.

    🔴 **`sim_run_id` 를 반드시 담는다.** PK 는 `receipt_id` **단독**인데 유일성 축은
       `(sim_run_id, inbound_id)` 다. 즉 스키마가 *"같은 `inbound_id` 가 다른 실행에
       있는 것은 합법"* 이라고 선언하고 있어서, `inbound_id` 만으로 지으면 두 실행의
       같은 입고가 **PK 에서 충돌한다.**

    ★ **`receipt_id` 는 정체성이 아니라 그 정체성의 행 표현이다.** 정체성은 여전히
      `(sim_run_id, inbound_id)` 이고 조회도 그 축으로 한다.

    🔴 **매입 ID 에서 유도하지 않는다.** `purchase_id` 를 뜯거나 접두사를 떼어 붙이지
       않는다 — 그 값의 주인은 마스터이고, 물류가 그 모양에 기대면 마스터가 형식을
       바꾸는 날 조용히 어긋난다.

    :raises InvalidInboundIdentity: 둘 중 하나라도 비었거나 공백뿐일 때.
    """
    if not sim_run_id or not sim_run_id.strip():
        raise InvalidInboundIdentity(
            f"receipt_id 를 지을 수 없다 — sim_run_id 가 비었다: {sim_run_id!r}"
            f" (inbound_id={inbound_id!r})."
        )
    if not inbound_id or not inbound_id.strip():
        raise InvalidInboundIdentity(
            f"receipt_id 를 지을 수 없다 — inbound_id 가 비었다: {inbound_id!r}"
            f" (sim_run_id={sim_run_id!r})."
        )
    return f"RCPT-{sim_run_id}-{inbound_id}"


def check_receipt_keys(*, sim_run_id: str, inbound_id: str) -> None:
    """Receipt 를 묻는 열쇠 `(sim_run_id, inbound_id)` 가 둘 다 있어야 한다. DB 를 만지지 않는다.

    ★ `check_receipt_state` 의 첫 단계를 떼어 낸 것이다 — 순서·문구 그대로.
    """
    if not inbound_id or not inbound_id.strip():
        raise InvalidInboundIdentity(
            f"Receipt 조회에 쓸 수 없는 inbound_id 다: {inbound_id!r}"
            f" (sim_run_id={sim_run_id!r})."
            " 없는 열쇠로 물으면 0건이 돌아오고 그것은 '아직 Receipt 가 없다' 로"
            " 읽힌다 — 없는 것과 물어보지 못한 것은 다른 사실이다."
        )
    if not sim_run_id or not sim_run_id.strip():
        # ★ 같은 이유다. 열쇠는 두 값이 함께여야 유일성 축이 된다.
        raise InvalidInboundIdentity(
            f"Receipt 조회에 쓸 수 없는 sim_run_id 다: {sim_run_id!r}"
            f" (inbound_id={inbound_id!r}). 어느 실행의 장부인지 없이 물을 수 없다."
        )


def receipt_existence(
    rows: Sequence[tuple[Any, Any]], *, sim_run_id: str, inbound_id: str
) -> ReceiptExistence:
    """읽은 Receipt 행(`(receipt_id, receipt_status)`)을 존재 사실로 옮긴다. DB 를 만지지 않는다.

    ```text
    0건      NEW
    1건      ALREADY_EXISTS — DB 값 그대로
    2건 이상  ReceiptIntegrityError
    읽을 수 없는 id · 어휘 밖 상태   ReceiptRowUnreadable
    ```
    """
    if not rows:
        # 🔴 세 칸을 **함께** 비운다. `NEW` 에 `"ARRIVED"` 를 얹으면 *"아직 없다"* 와
        #    *"막 도착했다"* 가 같은 값이 된다.
        return ReceiptExistence(status="NEW", receipt_id=None, receipt_status=None)

    receipt_ids = [row[0] for row in rows]
    if len(receipt_ids) > 1:
        raise ReceiptIntegrityError(
            f"같은 입고 건에 Receipt 가 둘 이상이다"
            f" (sim_run_id={sim_run_id!r}, inbound_id={inbound_id!r}):"
            f" {receipt_ids!r} …."
            " 어느 것이 진짜인지 여기서 고르지 않는다 — 최신도 첫 행도 고르지 않고,"
            " 조용히 합치지도 않는다."
        )

    receipt_id = receipt_ids[0]
    if not isinstance(receipt_id, str) or not receipt_id.strip():
        # ★ `receipt_id` 는 PK · NOT NULL 이다. 비어 오면 읽은 것이 그 행이 아니다.
        raise ReceiptRowUnreadable(
            f"Receipt 행의 receipt_id 를 읽을 수 없다: {receipt_id!r}"
            f" (sim_run_id={sim_run_id!r}, inbound_id={inbound_id!r})."
        )

    receipt_status = rows[0][1]
    if receipt_status not in RECEIPT_STATUSES:
        # 🔴 **`ARRIVED` 로 대신 읽지 않는다.** 모르는 상태를 아는 값으로 바꾸면
        #    검수·Lot 이 이미 있는 행을 처음부터 다시 돌게 된다.
        raise ReceiptRowUnreadable(
            f"Receipt 행의 receipt_status 가 계약 어휘 밖이다: {receipt_status!r}"
            f" (receipt_id={receipt_id!r}, sim_run_id={sim_run_id!r},"
            f" inbound_id={inbound_id!r}). 허용: {sorted(RECEIPT_STATUSES)}."
            " 아는 값으로 바꿔 읽지 않는다 — 모르는 상태를 아는 척하게 된다."
        )

    return ReceiptExistence(
        status="ALREADY_EXISTS",
        receipt_id=receipt_id,
        # ★ **DB 에 적힌 값 그대로다.** 여기서 진행 여부를 판단하지 않는다.
        receipt_status=receipt_status,
    )


def check_arrival_facts(*, inbound: DueInbound, purchase_detail: PurchaseDetail) -> None:
    """도착 Receipt 에 적을 매입 사실이 다 있는가. 없으면 쓰지 않는다. DB 를 만지지 않는다."""
    if not purchase_detail.purchase_item_id or not purchase_detail.purchase_item_id.strip():
        raise ReceiptFactsMissing(
            f"매입 상세에 purchase_item_id 가 없다 (inbound_id={inbound.inbound_id!r})."
            " NULL 로 Receipt 를 만들면 다음 실행이 ALREADY_EXISTS 를 보고 이 입고를"
            " 영영 건너뛴다 — 보강할 경로가 없다."
        )
    if not purchase_detail.item_id or not purchase_detail.item_id.strip():
        raise ReceiptFactsMissing(
            f"매입 상세에 item_id 가 없다 (inbound_id={inbound.inbound_id!r})."
            " inbound_receipts.item_id 는 NOT NULL 이고, 품목명으로 따로 번역해"
            " 채우지 않는다 — 그러면 purchase_item_id 와 다른 품목을 가리킬 수 있다."
        )
