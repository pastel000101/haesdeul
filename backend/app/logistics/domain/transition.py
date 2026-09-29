"""승인 약정 → 입고 예정 계산과 승인의 입고 ID 규칙.

★ 2026-09-30 재구성 BL-015: `logistics/transition.py` 의 `build_next_inventory` · 매입 참조 규칙과
  `logistics/cancellation.py` 의 `inbound_ids_of` 를 옮겼다(두 곳이 같은 `INB-{승인}-{회차}` 를
  짓는다). DB 를 만지지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from app.contracts.commitment import ApprovedCommitment
from app.logistics.schemas.snapshot import InTransitItem
from app.logistics.schemas.transition import PurchaseReferenceMissing


def _purchase_reference(
    commitment: ApprovedCommitment,
    leg: Any,
    purchase_ids: Mapping[int, str] | None,
) -> str | None:
    """이 회차가 가리킬 매입 참조. **없으면 `None` 이거나 예외다 — 지어내지 않는다.**

    🔴 **`None` 인 매핑과 값이 빠진 매핑은 다른 사실이다.**

    ```text
    purchase_ids is None            마스터 규약이 안 넘긴다       → None (정상)
    purchase_ids 에 leg.seq 없음     줬는데 이 회차가 빠졌다       → 예외
    purchase_ids[leg.seq] 가 빈 값   있는 척하는 값이다            → 예외
    ```

    ★ **찾는 열쇠는 반드시 `leg.seq` 다.** 매핑에 값이 하나뿐이어도
      `next(iter(purchase_ids.values()))` 로 집지 않는다 — 회차가 늘어난 날 이
      물건이 남의 매입 줄에 조용히 달린다.
    """
    if purchase_ids is None:
        # ★ 마스터 경로다. 규약이 이 값을 빼기로 확정했으므로 *"안 넘어온다"* 는
        #   정상 상태이고, 그 사실을 `None` 이 그대로 적는다 — 대신 만들지 않는다.
        return None
    purchase_id = purchase_ids.get(leg.seq)
    if not purchase_id:
        raise PurchaseReferenceMissing(
            f"승인 {commitment.approval_id!r} 의 회차 seq={leg.seq} 에 매입 참조가"
            f" 없다 (받은 회차: {sorted(purchase_ids)})."
            " 매입 참조 계약을 받고서 이 회차만 빠진 것이라 무결성 문제다 —"
            " purchase_id 는 마스터가 만드는 값이라 물류가 지어내지 않고,"
            " 다른 회차의 값으로 대신하지도 않는다."
        )
    return purchase_id


def build_next_inventory(
    commitment: ApprovedCommitment,
    *,
    purchase_ids: Mapping[int, str] | None = None,
) -> list[InTransitItem]:
    """승인 약정의 회차별 입고를 `InTransitItem` 목록으로 옮긴다. **순수 계산이다.**

    ★ DB 를 부르지 않는다 — 계산이 실패하면 커넥션을 열기도 전에 멈춰야 한다
      (마스터 `transition.py` 가 build 를 커넥션 밖에서 부르는 이유다).

    🔴 **도착일을 다시 계산하지 않는다.** `leg.arrival_date` 를 그대로 쓴다.
       마스터가 물류 `inbound_lead_days`(N4)로 이미 계산해 약정에 실었고, 여기서
       `purchase_date + N` 을 다시 더하면 **같은 사실의 주인이 둘이 된다.**
       두 곳이 각자 계산하면 어느 날 어긋나고, 어긋난 쪽이 틀렸다고 아무도 말해 주지
       않는다. 약정이 실은 값이 그 사실의 유일한 원본이다.

    ★ 빈 `arrival_schedule` 은 예외가 아니다. 회차 일정을 못 만든 약정도 승인은 살아
      있고(마스터 `commitment.py` 의 `notes` 가 왜 못 만들었는지 적는다), 그때 물류가
      반영할 입고 예정이 **없다**는 것은 정상 상태다.

    🟢 **매입 참조를 받을 수 있는 자리다. 마스터는 그것을 넘기지 않는다.**
       운송 중인 물건이 도착하면 물류는 그 매입 줄에서 `purchase_item_id` ·
       `item_id` · `grade` · `unit_price_krw_per_kg` 를 읽는다. 그 참조
       (`purchase_id`)를 만드는 곳은 **마스터**이고, 물류는 받아서 보관만 한다.

       ```text
       purchase_ids=None    마스터 경로. 규약이 안 넘긴다 → purchase_id=None
       purchase_ids={1: …}  값을 주는 호출               → purchase_ids[leg.seq]
       ```

    🔴 **기본값이 `None` 인 것이 이 판의 핵심이다.** 마스터 전이 규약
       (`app/master/transition.py` 의 `LogisticsTransition`)은 이 인자를 **받지
       않기로 확정했고**, **그 파일은 마스터 소유라 물류가 고칠 자리가 아니다.**
       필수로 만들면 마스터의 `apply_approval` 이 `TypeError` 로 터진다 — 물류 혼자
       도메인 경계를 넘어 남의 규약을 강제하는 셈이다.

    🔴 **물류가 이 ID 를 짓지 않는다.** `purchase_id_for()` 를 부르거나 `approval_id`
       를 뜯어 `PUR-…` 를 다시 조립하지 않는다. 같은 규칙이 두 곳에 있으면 같은
       사실의 주인이 둘이 되고, 마스터가 형식을 바꾸는 날 두 곳이 어긋난 채로
       조용히 돈다.

    :param purchase_ids: **회차(`leg.seq`) → `purchase_id` 매핑.** 마스터가 승인 한
        건에 대해 만들어 매입 원장·재무에 넘기는 값과 **같은 것**이다. 물류 규약에는
        올리지 않기로 확정돼 마스터 경로에서는 늘 `None` 이 오고, 그것은 예외가 아니다.
    :raises PurchaseReferenceMissing: 매핑을 **받았는데** 이 회차의 값이 없을 때.
        **다른 항목으로 대신하지 않는다.**
    """
    rows: list[InTransitItem] = []
    for leg in commitment.arrival_schedule:
        rows.append(
            InTransitItem(
                # ★ **승인 id + 회차 seq 로 만든다.** 같은 승인을 두 번 반영해도 같은
                #   id 가 나와야 갱신이 멱등해진다 — 순번 카운터나 난수를 쓰면 두 번째
                #   반영이 같은 물건을 다른 건으로 만들어 `in_transit` 이 부풀고,
                #   `confirmed_inbound_schedule` 과 대조할 열쇠(B-1)도 사라진다.
                inbound_id=f"INB-{commitment.approval_id}-{leg.seq}",
                # ★ **`inbound_id` 를 대신하지 않는다.** 둘은 다른 정체성이다 —
                #   위는 *"물류가 셈하는 입고 건"*, 아래는 *"매입 원장의 어느 행에서
                #   왔나"* 다. B-1 대조의 열쇠는 여전히 `inbound_id` 다.
                purchase_id=_purchase_reference(commitment, leg, purchase_ids),
                item=leg.item,
                # ★ `Decimal(str(x))` 를 쓴다. `Decimal(float)` 은 0.1 이 갖고 있는
                #   이진 오차를 그대로 들여와 수량에 안 보이는 꼬리를 남긴다.
                quantity_kg=Decimal(str(leg.qty_kg)),
                expected_arrival_date=leg.arrival_date,
            )
        )
    return rows


def inbound_ids_of(commitment: ApprovedCommitment) -> tuple[str, ...]:
    """이 승인이 만든 입고 건 ID. **`build_next_inventory` 와 같은 규칙으로 조립한다.**

    ★ **표를 읽지 않는다.** 규칙이 결정론이라 취소 시점에 다시 만들어도 같은 값이
      나온다 — 읽으면 *"fixture 가 말하는 것"* 과 *"약정이 말하는 것"* 이 갈릴 자리가
      하나 더 생긴다 (마스터 `purchase_ids_of` 와 같은 판단).

    ⚠️ **`transition.py:252` 와 같은 문자열이어야 한다.** 한 글자라도 다르면 아무것도
      못 걷고, 그런데도 조용히 성공한다 — 그래서 검사가 두 자리를 대조한다.
    """
    return tuple(f"INB-{commitment.approval_id}-{leg.seq}" for leg in commitment.arrival_schedule)
