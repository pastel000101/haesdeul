"""사람이 확정한 폐기만 실행한다 (3-D1).

이 파일에는 폐기 확정의 순서가 있다. 판정은 `domain/disposal.py`, SQL 은
`repository/disposal.py` 다.

```text
disposal_candidate = true   폐기 검토가 필요하다 — 아직 아무것도 안 바뀐다
   → 사람이 confirm_disposal(...) 을 명시적으로 부른다
   → DISPOSE Move
   → remaining_qty_kg 감소 · 창고 점유 감소
```

폐기를 실행하는 도메인 경로는 이 함수 하나뿐이다. Scheduler 도, Agent 도, 회전 상태도
이 함수를 직접 부르지 않는다. `domain/turnover.py` 는 후보만 계산하고 이 파일을 임포트하지
않는다 — 그 단방향이 "저절로 재고가 사라지는 경로" 를 막는다.

   규칙 기반 호출은 `service/maintenance` 하나뿐이다. 그 조립 계층은 아래 셋을 모두
   확인한 경우에만 이 함수를 부른다.

   ```text
   disposal_candidate == True   근거는 turnover 것 하나뿐이다
   살아있는 할당이 없다           한 kg 이라도 잡혀 있으면 손대지 않고 건너뛴다
   잔량 전량을 폐기할 수 있다     폐기가능량 == remaining_qty_kg · 수량도 그 전량이다
   ```

   그 밖의 자동 폐기는 없고, 자동 부분 폐기도 없다. 셋 중 하나라도 어긋나면 그 Lot 은
   손대지 않은 채 사람 몫으로 남는다 — 되돌릴 경로가 없기 때문이다.

폐기대기와 폐기는 다르다.

```text
disposal_candidate = true   remaining 그대로 · Move 없음 · Capacity 점유 그대로
                            판매 가용에서만 빠진다 (Legacy 신선도 규칙이 이미 그렇다)
confirm_disposal 이후        remaining 감소 · DISPOSE Move · Capacity 감소
```

회전목표 초과는 폐기 근거가 아니다. `STORAGE_TARGET_EXCEEDED` 만으로는 통과시키지
않는다 — 그것은 "언제 팔고 싶은가" 이지 "팔 수 있는가" 가 아니다. 근거는
`turnover.is_disposal_candidate` 하나뿐이고, 그것은 이미 있는 판매불가 기준
(`remaining_freshness_days <= 0`)을 재사용한다.

예약·할당된 재고를 없애지 않는다.

```text
lot_disposable = Lot remaining − 그 Lot 의 살아있는 할당 (ALLOCATED · PICKED)
```

   품목 축을 따로 재지 않는 이유가 있다. 폐기대기 Lot 은 `outbound._available_lots` 가
   이미 빼서 예약도 할당도 새로 잡을 수 없다 — 즉 이 Lot 을 없애도 판매 가능 재고가 줄지
   않는다. 품목 전체 여유량으로 재면 "다른 Lot 에 여유가 있나" 라는 상관없는 값으로
   폐기를 막게 된다. 판매 가능하던 시절에 붙은 할당은 위 `lot_disposable` 이 그대로
   보호한다.

   주의: 이 단순화는 폐기대기 Lot 을 가용에서 빼는 규칙에 기대고 있다. 그 규칙이 바뀌면
   품목 축 검사를 되살려야 한다.

잠금 순서는 출고와 같다.

```text
① 출고/재고확보 전역 (20260905, 3)   repository/locks.lock_outbound_writes  ← 재사용
② 원장 전역 (20260905, 1)            _record_disposal_move 안에서
③ Lot 행 FOR UPDATE                   〃
```

   새 잠금을 만들지 않는다. 폐기는 예약·할당과 같은 자원(가용재고)을 다투므로 그쪽과
   같은 줄에 서야 한다. 순서도 출고와 같아 역전이 생길 자리가 없다.

수량 감소의 정본은 원장이다. 이 파일에 `UPDATE inventory_lots SET remaining_qty_kg` 이
없다 — `_record_disposal_move` 가 그 일을 한다.

되돌리는 경로가 없다. 폐기 취소·환입·`ADJUST_IN` 은 범위 밖이고, 실사도 제외돼 있다.
그래서 검증을 전부 쓰기 전에 건다.

스키마 실측 (2026-09-05):

```text
inventory_moves   reason_code 에 CHECK 이 없다 — 어휘를 DB 가 강제하지 않는다
                  기존 DISPOSE 2건의 reason_code = 'MVP_DEMO_FIXTURE_CORRECTION'
                  ↑ 씨앗 보정용이지 업무 폐기 사유가 아니다
                  그래서 업무 사유 어휘를 여기서 짓지 않고 호출자가 준다
                  확정자를 적을 칸이 없다 (`confirmed_by` 컬럼 부재) — 보고 대상
inventory_lots    status CHECK = ACTIVE · DEPLETED · DISPOSED · HOLD
```
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from app.logistics.domain.disposal import (
    assert_same_disposal,
    disposal_move_id_for,
    disposal_quantity,
    disposal_text,
)
from app.logistics.readmodel.turnover import load_lot_turnover
from app.logistics.repository.disposal import (
    lot_disposable_qty,
    mark_lot_disposed,
    select_existing_disposal,
    select_lot_remaining_status,
)
from app.logistics.repository.locks import lock_outbound_writes
from app.logistics.repository.rows import cell
from app.logistics.schemas.disposal import (
    DisposalBlocked,
    DisposalIntegrityError,
    DisposalResult,
    InvalidDisposalRequest,
)
from app.logistics.service.ledger import _record_disposal_move


def confirm_disposal(
    conn: Any,
    *,
    disposal_id: str,
    sim_run_id: str,
    lot_id: str,
    quantity_kg: Decimal,
    disposed_at: date,
    reason_code: str,
    as_of: date,
    note: str | None = None,
) -> DisposalResult:
    """사람이 확정한 폐기를 실행한다. 재고를 없애는 유일한 경로다.

    ```text
    ① 입력 검증                        DB 를 안 만진다
    ② 출고/재고확보 전역 잠금            예약·할당과 같은 줄에 선다
    ③ 같은 폐기가 이미 있나              있으면 사실 대조 후 applied=False
    ④ 폐기대기 근거 확인                 disposal_candidate 가 참이어야 한다
    ⑤ Lot 폐기 가능량 검사               살아있는 할당은 건드리지 않는다
    ⑥ _record_disposal_move → 잔량 감소
    ⑦ 잔량 0 이면 Lot status = DISPOSED
    ```

    아무나 자동으로 부르면 안 된다. Scheduler · Agent · 회전 상태가 이 함수를 직접 부르지
    않는다. 되돌릴 경로가 없어서(`ADJUST_IN` 없음 · 실사 제외) 부르는 쪽이 그 판단을
    명시적으로 했다는 것이 안전장치다.

    일반 경로에서는 사람이 판단한 뒤 부른다. 그 판단을 규칙으로 세운 자리는
    `service/maintenance` 하나이고, 거기서도 폐기대기 · 살아있는 할당 없음 · 잔량 전량
    셋을 모두 확인할 때만 부른다 (모듈 docstring 참조).

    멱등: 재실행은 과거 사실을 다시 판정하지 않는다. 같은 `disposal_id` 가 오면 ④⑤ 를
    건너뛰고 기존 Move 와 사실만 대조한다 — 오늘 후보가 아니게 됐다고 어제 적은 폐기가
    틀린 것이 되지는 않는다.

    :param disposal_id: 폐기 건의 정체성. 호출자가 준다 — 부분 폐기를 여러 번 할 수 있어
        `lot_id` 로는 가를 수 없다.
    :param reason_code: 폐기 사유. 호출자가 준다 — DB CHECK 이 없고 기존
        `MVP_DEMO_FIXTURE_CORRECTION` 은 씨앗 보정용이라, 물류가 업무 어휘를 짓지 않는다.
    :param as_of: 폐기대기 판정 기준일. 신선도 잔여를 이 날짜로 센다.
    :raises DisposalBlocked: 폐기대기 근거가 없을 때.
    :raises InvalidDisposalRequest: 수량이 계약이나 한도를 어길 때.
    :raises DisposalIntegrityError: 같은 참조에 다른 사실이 있거나 Lot 이 없을 때.
    """
    # ── ① 검증 ────────────────────────────────────────────────────────
    move_id = disposal_move_id_for(disposal_id=disposal_id)
    disposal_text(sim_run_id, 칸="sim_run_id")
    disposal_text(lot_id, 칸="lot_id")
    disposal_text(reason_code, 칸="reason_code")
    quantity = disposal_quantity(quantity_kg)

    # ── ② 예약·할당과 같은 줄에 선다 ──────────────────────────────────
    lock_outbound_writes(conn)

    # ── ③ 이미 적힌 폐기인가 ──────────────────────────────────────────
    기존 = select_existing_disposal(conn, move_id=move_id)
    if 기존 is not None:
        assert_same_disposal(
            기존,
            disposal_id=disposal_id,
            sim_run_id=sim_run_id,
            lot_id=lot_id,
            quantity=quantity,
            disposed_at=disposed_at,
            reason_code=reason_code,
            note=note,
        )
        # 재실행에서는 후보 판정을 다시 하지 않는다 — 오늘 후보가 아니게 됐다고 어제 적은
        # 폐기가 틀린 것이 되지 않는다. 현재 잔량만 되읽어 돌려준다.
        row = select_lot_remaining_status(conn, sim_run_id=sim_run_id, lot_id=lot_id)
        return DisposalResult(
            applied=False,
            move_id=move_id,
            lot_id=lot_id,
            disposed_qty_kg=quantity,
            remaining_qty_kg=Decimal(cell(row, 0, "remaining_qty_kg")),
            lot_status=cell(row, 1, "status"),
        )

    # ── ④ 폐기대기 근거 ───────────────────────────────────────────────
    후보 = load_lot_turnover(conn, sim_run_id=sim_run_id, as_of=as_of, lot_id=lot_id)
    if not 후보:
        raise DisposalIntegrityError(
            f"폐기할 Lot 이 없다 (sim_run_id={sim_run_id!r}, lot_id={lot_id!r}, as_of={as_of})."
        )
    lot = 후보[0]
    if not lot.disposal_candidate:
        raise DisposalBlocked(
            f"폐기대기 대상이 아니다 (lot_id={lot_id!r}, as_of={as_of}):"
            f" 잔여 신선도 {lot.remaining_freshness_days}"
            f" · 회전상태 {lot.turnover_status}."
            " 🔴 회전목표 초과(STORAGE_TARGET_EXCEEDED)만으로는 폐기하지 않는다 —"
            " 그것은 판매불가가 아니다."
        )

    # ── ⑤ Lot 폐기 가능량 ─────────────────────────────────────────────
    lot_disposable, _ = lot_disposable_qty(conn, sim_run_id=sim_run_id, lot_id=lot_id)
    if quantity > lot_disposable:
        raise InvalidDisposalRequest(
            f"이 Lot 에서 없앨 수 있는 양을 넘는다 (lot_id={lot_id!r}):"
            f" 요청 {quantity} · 가능 {lot_disposable}."
            " 이미 출고에 배정된 몫은 없애지 않는다."
        )
    # 품목 전체 여유량은 여기서 재지 않는다 — 폐기대기 Lot 은 판매 가용 풀에 없어서,
    # 없애도 팔 수 있는 양이 줄지 않는다. 모듈 docstring 참고.

    # ── ⑥ 잔량을 줄이는 것은 원장뿐이다 ───────────────────────────────
    move = _record_disposal_move(
        conn,
        move_id=move_id,
        sim_run_id=sim_run_id,
        lot_id=lot_id,
        quantity_kg=quantity,
        moved_at=disposed_at,
        reason_code=reason_code,
        note=note,
    )

    # ── ⑦ 전량 폐기만 DISPOSED ────────────────────────────────────────
    상태 = "ACTIVE"
    if move.remaining_qty_kg == 0:
        mark_lot_disposed(conn, sim_run_id=sim_run_id, lot_id=lot_id)
        상태 = "DISPOSED"
    return DisposalResult(
        applied=move.applied,
        move_id=move_id,
        lot_id=lot_id,
        disposed_qty_kg=quantity,
        remaining_qty_kg=move.remaining_qty_kg,
        lot_status=상태,
    )
