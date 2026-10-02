"""검수 끝난 입고를 가용재고로 만든다 (3-B4-I).

이 파일에는 도착 대상 읽기 · 재고화의 순서가 있다. 대조 규칙은 `domain/inbound_stock.py`,
SQL 은 `repository/inbound_stock.py` 다.

```text
INSPECTED Receipt
   → accepted 수량만큼 Lot 하나 (remaining 0 으로 세우고)
   → record_inventory_move(IN)      ← remaining 이 여기서 accepted 가 된다
   → Receipt PUTAWAY_DONE
```

입고 일정은 걷지 않는다 (W3-3). 완료는 `inbound_schedules` 의 칸이 아니라 Lot + 원장 IN
으로 유도한다 — Reader 가 그 둘을 보고 도착 대상과 Capacity 에서 뺀다.

다른 모듈의 로직을 복제하지 않는다. Receipt · 검수 · 원장은 각자의 모듈이 이미 하고
있고, 이 파일은 순서와 트랜잭션만 맡는다 (`master/service/transition.apply_approval` 이
재무·물류를 감싸는 것과 같은 결).

`accepted_qty_kg` 만 재고가 된다.

  ```text
  accepted > 0   Lot 1 · IN 1                  ACTIVE · inspection_status=PASS
  accepted = 0   Lot 없음 · IN 없음             REJECT 여도 정상 완료다
  hold · reject  Lot 없음 · IN 없음 · 폐기 없음  검수·Receipt 에만 남는다
  ```

  보류·거부 물량을 자동으로 `DISPOSE` 로 바꾸지 않는다. 그것은 사람이 판단할 일이고
  범위 밖이다.

보관 Zone 의 권위 출처는 `item_storage_policies` 다.

  ```text
  item_storage_policies.storage_zone   ITEM-BAECHU → COLD_HUMID_0_3   …5품목
  inventory_lots.storage_zone          기존 80행이 품목마다 정확히 그 값이다
                                       (16/16 × 5품목, 실측 2026-09-05)
  ```

  ⇒ `inventory_lots.storage_zone` 의 주인은 `item_storage_policies` 다. 품목으로 찾아 그
    값을 그대로 쓴다.

  `item_zone_assignments.zone_id`(`HIGH_HUMIDITY_COLD` 계열)는 다른 축이다. 그쪽은 새
  WMS 의 물리 Zone(`warehouse_zones`)이고 5품목 중 3품목만 덮는다 (`ITEM-GEONGOCHU` ·
  `ITEM-PIMANUL` 없음). 두 축을 잇는 번역표도 없다 (`warehouse_zones.zone_code ==
  zone_id` 라 옮길 값이 없다). 그래서 여기서 그 둘을 잇는 매핑을 지어내지 않는다 —
  물리 Zone 배정은 적치(Putaway) 단계의 사실이다.

  품목명으로 Zone 을 하드코딩하지 않는다. `배추 → HIGH_HUMIDITY_COLD` 같은 줄을 코드에
  적으면 그 순간 정책표가 둘이 된다.

잠금 순서가 계약이다. 기존 두 잠금을 그대로 쓰고 새 잠금을 만들지 않는다.

  ```text
  ① 도착 전역 advisory  (20260905, 2)   repository/locks.lock_arrival_writes
  ② fixture 행 FOR UPDATE               status 를 읽고 도착 경로를 직렬화한다 · ③④ 보다 먼저
  ③ Lot 조회 / INSERT
  ④ record_inventory_move  → 원장 전역 advisory (20260905, 1) → Lot 행 FOR UPDATE
  ⑤ Receipt UPDATE
  ⑥ 커밋은 호출자가 한 번
  ```

  ② 는 쓰기 대상이 아니다. 같은 행의 `in_transit_status` 를 승인 전이가 건드리는 것과
  도착 경로 자체의 직렬화 때문에 잡는다 (`load_in_transit_for_receiving`).

  원장 전역을 fixture 행보다 먼저 잡는 경로를 만들면 안 된다 — ② 를 ④ 앞에 둔 이유가
  그것이고, 그 규칙이 이 전순서를 성립시킨다.

Pallet · Location 을 만들지 않는다. 원장이 `lines=()` 를 허용하고, 실제로 기존 80 Lot 의
Pallet 배분도 원장에 없다 (`pallets` 주석: "Lot 잔량이 수량 정본이다").
`receiving_location_id` · 팔레트 수도 그대로 NULL 이다.

`CLOSED` 까지 가지 않는다. `PUTAWAY_DONE` 의 뜻은 "이 Receipt 에서 가용재고로 반영할
accepted 수량의 재고화가 끝났다" 이지 보류·거부까지 정리됐다는 뜻이 아니다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.logistics.domain.inbound_stock import (
    assert_existing_move,
    assert_receipt_matches,
    assert_same_lot,
    assert_same_purchase_reference,
    check_receiving_axis,
    lot_id_for,
    move_id_for,
)
from app.logistics.readmodel.inbound_schedules import receivable_at
from app.logistics.repository.inbound_stock import (
    existing_lot,
    insert_lot,
    lock_fixture_row_for_receiving,
    mark_putaway_done,
    select_in_move,
    select_receipt_for_stock,
    select_storage_zone,
)
from app.logistics.repository.inspections import find_inspection
from app.logistics.repository.locks import lock_arrival_writes
from app.logistics.schemas.inbound_stock import (
    IN_REASON_CODE,
    InboundStockResult,
    LotIntegrityError,
    ScheduleIntegrityError,
)
from app.logistics.schemas.purchase_detail import PurchaseDetail
from app.logistics.schemas.snapshot import UNRESOLVED_SOURCE, InTransitItem
from app.logistics.schemas.vocabulary import USAGE_SCOPE
from app.logistics.service.ledger import record_inventory_move

#: 이 단계가 손댈 수 있는 Receipt 상태. `ARRIVED` · `INSPECTING` 은 아직 대상이 아니다.
_READY_TO_MATERIALIZE: frozenset[str] = frozenset({"INSPECTED"})

#: 이미 재고화가 끝난 상태. 재실행이면 여기로 온다.
_ALREADY_MATERIALIZED: frozenset[str] = frozenset({"PUTAWAY_DONE", "CLOSED"})


def load_in_transit_for_receiving(
    conn: Any,
    *,
    sim_run_id: str,
    as_of: date,
    usage_scope: str = USAGE_SCOPE,
) -> list[InTransitItem] | None:
    """도착 처리를 위해 그날 내 실행의 운송 중 목록을 읽는다.

    ```text
    ① 도착 쓰기 전역 advisory lock       repository/locks.lock_arrival_writes
    ② 그날 fixture 행 SELECT … FOR UPDATE   (status 를 읽고, 도착 경로를 직렬화한다)
    ③ inbound_schedules → InTransitItem 목록   ← 목록의 정본 (W3-2)
    ```

    목록의 정본은 `inbound_schedules` 다 (W3-2). 신규 표는 날짜에 묶여 있지 않아, 미래
    날짜 fixture 행이 먼저 열려 있어도 나중에 난 승인을 놓치지 않는다 (fixture 행의
    `in_transit_json` 을 읽으면 그 행이 나중 승인을 몰라 도착일에 볼 것이 없다 — 실측
    `INB-H1-REQ-FIRSTINB-20260113-1-1`).

    `readmodel/current.get_active_logistics_runtime_fixture` 로 대신하지 않는다. 그쪽은
    잠그지 않는 조회이고 연결을 받지 않으면 자기 연결을 빌린다 — 마스터가 쥔 트랜잭션 밖이나
    잠금 없이 읽으면, 이번 도착 처리가 쓸 목록과 실제로 고칠 행이 갈린다.

    잠금 순서가 계약이다 (`materialize_inspected_inbound` 과 같은 순서).

    ```text
    ① 도착 전역 (20260905, 2)   ← 여기서 먼저 잡는다
    ② fixture 행 FOR UPDATE      ← 그다음 (status 읽기 · 직렬화)
    ③ Receipt · 검수
    ④ Lot · 원장 IN (원장 전역 → Lot 행)
    ```

       끝에 «일정 정리» 단계가 없다. 완료는 `inbound_schedules` 의 칸이 아니라 Lot +
       원장 IN 으로 유도한다 — 일정 행은 과거 재현을 위해 그대로 남는다.

       ② 를 ① 앞에 두면 안 된다. 그러면 두 트랜잭션이 요청하는 잠금 집합에 전순서가
       없어져 교착이 생긴다 (`repository/locks.py` 의 잠금 순서 표).

    읽기인데 `FOR UPDATE` 를 쓴다. 여기서 읽은 목록이 곧 이번 실행이 처리할 대상이고,
    같은 행의 status 를 `persist_inventory`(승인 전이)가 건드린다. 그 사이가 열려 있으면
    이번에 못 본 승인분이 생긴다 — 시작부터 끝까지 한 행 잠금 아래 둔다.

    `None` 과 `[]` 를 가른다. 판정 근거는 `in_transit_status` 다 (W3-3).

    ```text
    in_transit_status = UNRESOLVED   None   확인한 적 없다
    그 외                            [...]  inbound_schedules 가 답한다
    ```

       `in_transit_json IS NULL` 로 판정하지 않는다. 승인 Writer 가 그 칸을 쓰지 않으므로
       `status=CONFIRMED · json=NULL · 일정 있음` 이 성립하고, JSON 으로 판정하면 이
       경로만 `None` 을 내 화면과 갈린다.

       둘을 뭉치면 "오늘 도착할 게 없다" 와 "오늘 뭐가 도착할지 모른다" 가 같은 값으로
       나간다 (`domain/arrival.ArrivalSelection.source_status` 가 그 둘을 가른다).

    `sim_run_id=None` 을 허용하지 않는다. 도착 처리는 쓰기 경로라 남의 실행 장부를
    건드리면 안 된다 — 조회 축은 `uq_log_runtime_fixture` 와 같아야 한다.

    커밋도 롤백도 하지 않고 커넥션을 새로 열지 않는다. advisory lock 도 행 잠금도 트랜잭션
    수명이라 호출자의 커밋/롤백과 함께 풀린다.

    :param conn: 호출자가 소유한 커넥션. 이 함수는 수명을 관리하지 않는다.
    :param sim_run_id: 어느 실행의 장부인가. 마스터가 소유한 값이다.
    :param as_of: 읽을 fixture 행의 날짜. 마스터가 정하는 달력값이다.
    :raises InvalidReceivingAxis: `sim_run_id` 나 `usage_scope` 가 비었거나 공백뿐일 때.
    :raises ScheduleIntegrityError: 그날 그 실행의 fixture 행이 없을 때.
    """
    # 조회 축 세 칸 중 문자열 둘을 함께 막는다. 하나만 막으면 나머지 한 칸으로 같은 구멍이
    # 그대로 남는다 — 0건이 돌아오고 그 0건이 '그날 행이 없다' 로 읽힌다.
    #
    # `usage_scope` 는 기본값이 있어 정상 경로에서는 비지 않는다. 그래도 보는 이유는 이
    # 함수가 public export 이고, 기본값을 안 쓰는 호출자가 언제든 생기기 때문이다
    # (`check_receipt_state` 가 상류의 검증을 믿지 않는 것과 같은 이유).
    check_receiving_axis(sim_run_id=sim_run_id, as_of=as_of, usage_scope=usage_scope)

    # ── ① 도착 전역 잠금이 먼저다 ─────────────────────────────────────
    lock_arrival_writes(conn)

    # ── ② 그날 행을 잠그고 status 를 읽는다 ──────────────────────────
    #    행 잠금은 도착 처리 전체가 쓰는 자원이다. 이 행을 잡은 채 Receipt · 검수 · Lot ·
    #    원장 IN 까지 가고, 승인 전이가 같은 행의 status 를 건드린다. 잠금 순서(도착 전역
    #    → 이 행 → 원장 전역)를 바꾸지 않는다.
    status = lock_fixture_row_for_receiving(
        conn, sim_run_id=sim_run_id, as_of=as_of, usage_scope=usage_scope
    )
    if status == UNRESOLVED_SOURCE:
        # `[]` 로 바꾸지 않는다. 모르는 것을 0 건으로 적으면 그 순간 아는 척이 된다.
        # 판정 근거는 `status` 하나다 — JSON 의 NULL 여부가 아니다 (W3-3).
        # Console · Capacity(`domain/snapshot.schedule_source`)와 같은 눈이어야 같은 날
        # 같은 입고를 두 경로가 다르게 읽지 않는다.
        return None

    # ── ③ 목록은 신규 표에서 온다 (W3-2) ─────────────────────────────
    #    `Receipt 존재` 로 빼지 않는다. 검수에서 막힌 건(Receipt=ARRIVED · Lot 없음)은 다음
    #    실행이 이어받아야 하고, `_receive_one` 이 `check_receipt_state` 로 마지막 성공
    #    단계 다음부터 잇는 구조라 여기서 빼면 그 입고가 영구 고착된다. 종료조건은
    #    `Lot + 원장 IN` 이다.
    return receivable_at(conn, sim_run_id=sim_run_id, as_of=as_of)


def materialize_inspected_inbound(
    conn: Any,
    *,
    as_of: date,
    receipt_id: str,
    purchase_detail: PurchaseDetail,
    usage_scope: str = USAGE_SCOPE,
) -> InboundStockResult:
    """검수 끝난 입고를 재고로 만든다. 멱등하다.

    ```text
    ① 잠금 · 상태 확인
    ② fixture 행 FOR UPDATE       ← 원장 잠금보다 먼저 (도착 경로 직렬화)
    ③ accepted > 0 이면 Lot (remaining 0) → record_inventory_move(IN)
    ④ Receipt PUTAWAY_DONE
    ```

    일정을 걷는 단계가 없다 (W3-3). 완료는 `inbound_schedules` 의 칸이 아니라 Lot + 원장
    IN 으로 유도한다 — Reader 가 그 둘을 보고 도착 대상과 Capacity 에서 뺀다. 일정 행은
    과거 재현을 위해 그대로 남는다.

    상태가 생성 권한을 가른다.

    ```text
    INSPECTED               아직 재고화 전이다 — Lot · Move 를 만들 수 있다
                            이미 같은 사실로 있으면 멱등 재실행이다
    PUTAWAY_DONE · CLOSED   이미 재고화를 주장하는 상태다
                            accepted > 0 이면 Lot 과 Move 가 반드시 있어야 한다
                            없으면 무결성 오류 — 새로 만들어 조용히 복구하지 않는다
                            accepted = 0 이면 둘 다 없는 것이 정상이다
    ARRIVED · INSPECTING    아직 대상이 아니다 — 검수 사실 없이 재고를 만들면
                            수용량을 우리가 정하는 셈이 된다
    ```

    `accepted = 0` 인데 Lot 이 있으면 모순이다. 검수가 하나도 안 받았다는데 재고가 선
    것이라, 수량 분기와 무관하게 기존 Lot 을 먼저 본다.

    커밋도 롤백도 하지 않고 커넥션을 새로 열지 않는다.

    :param as_of: 정리할 fixture 행의 날짜. 마스터가 정하는 달력값이다.
    :param purchase_detail: 원가·등급의 권위 출처 (`fetch_purchase_detail` 결과).
    """
    # ── ① 도착 전역 잠금이 먼저다 ─────────────────────────────────────
    lock_arrival_writes(conn)

    receipt = select_receipt_for_stock(conn, receipt_id=receipt_id)
    상태 = receipt["receipt_status"]
    if 상태 not in _READY_TO_MATERIALIZE | _ALREADY_MATERIALIZED:
        raise LotIntegrityError(
            f"재고화할 수 없는 Receipt 상태다: {상태!r} (receipt_id={receipt_id!r})."
            " 검수 사실 없이 재고를 만들면 수용량을 우리가 정하는 셈이 된다."
        )

    검수 = find_inspection(conn, receipt_id=receipt_id)
    if 검수 is None:
        raise LotIntegrityError(
            f"검수 사실이 없다 (receipt_id={receipt_id!r}, receipt_status={상태!r})."
            " 수용 수량의 주인은 검수다 — 여기서 지어내지 않는다."
        )
    assert_receipt_matches(receipt, 검수.outcome, receipt_id=receipt_id)
    # DML 전에 Receipt 가 든 매입 참조와 받은 상세가 같은 줄인지 본다.
    assert_same_purchase_reference(receipt, purchase_detail, receipt_id=receipt_id)
    accepted = 검수.outcome.accepted_qty_kg
    완료상태 = 상태 in _ALREADY_MATERIALIZED

    # ── ② 일정 행을 원장 잠금보다 먼저 잡는다 ─────────────────────────
    sim_run_id = receipt["sim_run_id"]
    inbound_id = receipt["inbound_id"]
    if not inbound_id:
        raise ScheduleIntegrityError(
            f"Receipt 에 inbound_id 가 없어 입고 일정으로 되짚을 수 없다:"
            f" receipt_id={receipt_id!r}."
            " 그 값이 inbound_schedules 와 잇는 유일한 열쇠라, 없으면 그 일정이"
            " 영원히 «아직 안 들어온 것» 으로 남는다."
        )
    lock_fixture_row_for_receiving(
        conn, sim_run_id=sim_run_id, as_of=as_of, usage_scope=usage_scope
    )

    # ── ③ 기존 Lot 은 수량·상태와 무관하게 먼저 본다 ──────────────────
    #    `accepted = 0` 인데 Lot 이 있는 것도 모순이라, 그 분기 안에서만 보면 못 잡는다.
    기존 = existing_lot(conn, receipt_id=receipt_id)
    lot_id: str | None = None
    move_id: str | None = None
    applied = False

    if accepted <= 0:
        if 기존 is not None:
            raise LotIntegrityError(
                f"수용 수량이 0 인데 재고 Lot 이 있다"
                f" (receipt_id={receipt_id!r}, lot_id={기존['lot_id']!r})."
                " 검수가 하나도 안 받았다는데 재고가 선 것이라 모순이다."
            )
    else:
        기대 = {
            "sim_run_id": sim_run_id,
            # Receipt 와 상세가 이미 같다는 것을 위에서 확인했다 — 섞지 않는다.
            "purchase_item_id": purchase_detail.purchase_item_id,
            "item_id": purchase_detail.item_id,
            # 매입이 적은 등급 그대로다. NULL 이면 NULL 이다.
            "grade": purchase_detail.grade,
            "received_at": receipt["arrived_at"],
            "original_qty_kg": accepted,
            "unit_cost_krw_per_kg": purchase_detail.unit_price_krw_per_kg,
        }
        if 기존 is not None:
            # 보관 Zone 을 다시 조회하지 않는다. 이미 선 Lot 의 Zone 은 확정된 역사적
            # 사실이라, 정책이 나중에 바뀌거나 지워졌다고 과거 입고 재실행이 실패하면 안 된다.
            assert_same_lot(기존, 기대, receipt_id=receipt_id)
            lot_id = 기존["lot_id"]
        elif 완료상태:
            # 완료라고 적힌 상태에서 없는 재고를 새로 만들어 복구하지 않는다.
            raise LotIntegrityError(
                f"재고화가 끝났다는 Receipt 인데 Lot 이 없다"
                f" (receipt_id={receipt_id!r}, receipt_status={상태!r})."
                " 여기서 새로 만들면 사라진 재고가 있었다는 사실조차 안 남는다."
            )
        else:
            lot_id = lot_id_for(receipt_id=receipt_id)
            insert_lot(
                conn,
                {
                    **기대,
                    "lot_id": lot_id,
                    # 신규 Lot 일 때만 정책표를 본다.
                    "storage_zone": select_storage_zone(conn, item_id=purchase_detail.item_id),
                    "inbound_receipt_id": receipt_id,
                },
            )
            applied = True

        # ── ④ 잔량을 바꾸는 것은 원장뿐이다 ───────────────────────────
        move_id = move_id_for(lot_id=lot_id)
        if 완료상태:
            # 읽어서 확인만 한다. `record_inventory_move` 를 부르면 Move 가 없을 때 새로
            # 만들어 버린다 — 완료 상태에서는 그것이 조용한 복구다.
            assert_existing_move(
                select_in_move(conn, move_id=move_id),
                move_id=move_id,
                sim_run_id=sim_run_id,
                lot_id=lot_id,
                quantity_kg=accepted,
                moved_at=receipt["arrived_at"],
            )
        else:
            # `record_inventory_move` 가 `move_id` 로 이미 멱등하다 — 재실행이면 사실을
            # 대조하고 `applied=False` 를 돌려준다. 여기서 다시 세지 않는다.
            move = record_inventory_move(
                conn,
                move_id=move_id,
                sim_run_id=sim_run_id,
                lot_id=lot_id,
                move_type="IN",
                quantity_kg=accepted,
                moved_at=receipt["arrived_at"],
                reason_code=IN_REASON_CODE,
            )
            applied = applied or move.applied

    # ── ⑤ Receipt 마감 ───────────────────────────────────────────────
    if 상태 in _READY_TO_MATERIALIZE:
        mark_putaway_done(conn, receipt_id=receipt_id)
        상태 = "PUTAWAY_DONE"

    # 일정을 걷지 않는다 (W3-3). 완료는 `inbound_schedules` 의 칸이 아니라 downstream
    # 사실로 유도한다 — Lot 과 원장 IN 이 둘 다 서면 Reader 가 도착 대상에서도 Capacity
    # 에서도 뺀다 (`readmodel/inbound_schedules.receivable_at` · `pending_inbound_at`).
    #
    # 일정 행을 지우거나 취소로 바꾸지 않는다. 지우면 "그날 무엇이 떠 있었나" 를 되짚을
    # 자리가 없어지고, 취소로 적으면 들어온 물건이 취소된 것으로 둔갑한다. 둘은 다른
    # 사실이다.
    return InboundStockResult(
        applied=applied,
        receipt_status=상태,
        lot_id=lot_id,
        move_id=move_id,
        accepted_qty_kg=accepted,
    )
