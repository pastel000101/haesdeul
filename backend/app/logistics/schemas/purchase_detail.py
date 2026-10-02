"""매입 줄 한 건(등급 · 단가)과 읽기 실패 종류.

읽기는 `repository/purchase_detail.py` 에 있다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


class PurchaseDetailError(RuntimeError):
    """이 모듈이 내는 실패의 조상 (`receipts.ReceiptLookupError` 와 같은 결)."""


class InvalidPurchaseIdentity(PurchaseDetailError, ValueError):
    """조회 열쇠로 쓸 수 없는 `purchase_id` 다.

    없는 열쇠로 묻지 않는다. 물으면 0행이 나오고, 그 0행은 "매입 줄이
    없다" 로 읽힌다 — 없는 것과 물어보지 못한 것이 같은 답으로 뭉개진다.

    `arrival.select_due_inbound` 이 이미 같은 눈으로 걸러 준다
    (`ARRIVAL_PURCHASE_REFERENCE_MISSING`). 여기서 다시 보는 것은 이 함수가 그
    경로 밖에서도 안전해야 하기 때문이지, 저쪽을 못 믿어서가 아니다.
    """


class PurchaseDetailMissing(PurchaseDetailError, LookupError):
    """입고가 가리키는 `purchase_id` 에 매입 줄이 하나도 없다.

    대체 데이터를 만들지 않는다. 시세를 조회하지도, 품목명으로 `item_id` 를
    유추하지도, 평균원가를 쓰지도 않는다. 승인이 만든 매입 줄이 없는데 물건이
    도착했다는 것은 무결성 문제이지 값이 모자란 상태가 아니다.
    """


class PurchaseDetailAmbiguous(PurchaseDetailError, ValueError):
    """같은 `purchase_id` 에 매입 줄이 둘 이상이다.

    어느 것도 고르지 않는다. 첫 행도, 최신도, 한글 품목명이 맞는 것도,
    싼 것도, 등급 높은 것도 전부 고르는 것이다. 고른 뒤에는 버려진 줄이
    있었다는 사실조차 남지 않고, 그 줄의 단가가 로트 원가로 굳는다.

    `readmodel/current.get_active_logistics_runtime_fixture`(활성 fixture 2건) ·
    `receipts.check_receipt_state`(Receipt 2건) 와 같은 규율이다.

    지금 MVP 계약은 매입당 상세 1행이다. 다품목 매입 지원은 나중의 계약
    확장이고(그때 열쇠가 `purchase_id + item_id` 로 넓어진다), 여기서 미리
    고르는 규칙을 만들지 않는다.
    """


@dataclass(frozen=True)
class PurchaseDetail:
    """매입 원장이 확정해 둔 사실. 입고 처리에 필요한 것만 담는다.

    `market_name` · `line_amount_krw` · `source_quote_id` 를 안 싣는다 —
    입고가 쓰지 않는 값이고, 실어 두면 뒤 단계가 여기서 읽은 낡은 값을 쓴다.
    """

    purchase_item_id: str
    item_id: str
    #: 정규화하지 않는다. DB 가 NULL 이면 `None` 그대로다.
    #:   `상품 → 상` 같은 임의 치환은 물류 정규화표에서도 의도적으로 비어 있다
    #:   (`domain/grade._RAW_GRADE_NORMALIZATION`).
    grade: str | None
    quantity_kg: Decimal
    unit_price_krw_per_kg: Decimal
