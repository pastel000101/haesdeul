"""
outbound_flow.py — **하루의 출고를 조립한다. 순서만 정하고 Lot 을 고르지 않는다.**

🔴 **물류가 순서를 마스터에 넘겼다** (물류 회신 2026-09-08).

  > Allocation 과 Shipment 함수 자체는 분리해서 유지하고, **실제 호출 순서는
  > Master 가 소유**하는 것으로 보겠습니다.
  > Master 는 어느 Reservation 을 실행할지만 정하고, Lot 선택 loop 는 Logistics
  > 내부에서 소유하겠습니다.

  ★ 엔진(`reserve_available_stock` · `allocate_reserved_stock_fefo` ·
    `ship_allocated_stock`) · 어휘(`app/contracts/sales_logistics.py`) · 시각
    (`app/master/sim_time.py`) 이 다 서 있는데 **부르는 곳이 0곳이었다.**
    이 파일이 그 자리다.

---

## 다섯 걸음

```text
① 그날 sale_date 인 판매의 sale_item 을 고른다
② reservation_id_for_sale_item(sale_item_id) 으로 예약 이름을 **계산**한다
③ reserve_confirmed_sale_available 로 **확보되는 만큼** 잡는다
④ allocate_reserved_stock_fefo(decided_at=phase_instant(as_of, "ALLOCATE"))
⑤ ship_allocated_stock(shipped_at=as_of, sale_item_id=…)
   그리고 그 판매의 **모든 품목**이 나갔으면 mark_sale_delivered
```

🔴 **`sales.sale_date` 가 납품일의 정본이다.** DDL 주석이 그렇게 정의한다.

```text
database/schema/sales/sales.sql
  COMMENT ON COLUMN haetdeul.sales.sale_date IS '판매/납품 기준일.';
```

  ⚠️ **봉투에 납품일 칸을 두지 않기로 했다** (2026-09-08 · 물류·판매 합의 ·
    `app/contracts/sales_logistics.py` 가 그 결정을 적어 뒀다). 같은 날짜를
    복사해 두면 두 값이 갈리는 날이 온다. 그래서 여기서도 날짜를 저장하지 않고
    **읽어서 비교만** 한다.

🔴 **예약 이름은 저장하지 않고 계산한다.** `reservation_id_for_sale_item` 이
   결정론이라 적어 둘 이유가 없다 — 적어 두면 그 값이 두 번째 정본이 된다.

🔴 **`reserve_confirmed_sale` 이 아니라 `reserve_confirmed_sale_available` 이다.**
   앞엣것은 전량 아니면 예외를 던지는 사람 경로이고, 여기는 시뮬레이션 경로다.
   **부분 예약은 정상이다** — 물류가 그렇게 설계했고, 확보된 만큼만 나간다.

🔴 **`decided_at` 은 `sim_time.phase_instant(as_of, "ALLOCATE")` 다. 벽시계를
   읽지 않는다.** `clock.seoul_now` 도 부르지 않는다 — 같은 `as_of` 를 다시
   돌리면 장부에 같은 값이 적혀야 한다
   (`tests/core/test_clock_is_the_only_wall_clock.py` 가 AST 로도 지킨다).

---

## 🔴 실패 규율

```text
한 판매가 터져도 **나머지 판매는 계속 돈다** — 터진 것은 `items` 에 FAILED 로 남는다
그날 나갈 것이 없으면 **NOTHING_DUE** — BLOCKED 도 FAILED 도 아니다
예약이 부분만 확보되면 **그만큼만 나간다**
```

⚠️ **`ship` 이 실패해도 `allocate` 를 되돌리지 않는다.**

```text
할당   "어느 Lot 에서 뺄지 정했다"
출고   "나갔다"
```

  두 개는 **다른 사실**이다. 그래서 이 파일은 할당 뒤에 **커밋을 하나 둔다** —
  그래야 뒤이은 출고가 터져도 롤백이 할당까지 걷어 가지 않는다. 되돌리는 함수
  (`cancel_allocation`) 를 여기서 부르지도 않는다.

🔴 **한 판매에 품목이 여럿이면 그 판매의 모든 품목이 나간 뒤에만
   `mark_sale_delivered` 를 부른다.** 일부만 나갔는데 `DELIVERED` 로 적으면
   *"다 갔다"* 가 거짓으로 서고, 판매가 오늘 고친 그 문제가 되살아난다.

---

## 어휘 — 새로 만들지 않았다

```text
RAN           출고 단계를 **탔다**       `ItemRunOutcome.status` · `procurement_status`
FAILED        해 보고 터졌다             같은 곳
NOTHING_DUE   확인했고 나갈 것이 없다     `InboundOut` · `CollectionOut` — **날 단위**
SHORT         확보가 0kg 이라 나간 것이 없다   ← 여기서 새로 둔다 (품목 단위)
```

🔴 **`SHORT` 를 `NOTHING_DUE` 로 접지 않는다** (물류 PR #484 수신요청 §5.1).

```text
NOTHING_DUE   그날 나갈 판매가 **없다**
SHORT         나갈 판매가 **있었는데** 확보가 0이었다
```

  **없는 것과 해 봤는데 0인 것은 다른 사실이다.** 접으면 재고가 모자란 날과 주문이
  없는 날이 장부에서 같아 보인다.

★ **`SHIPPED` 를 상태 어휘로 만들지 않았다.** 그것은 물류가 이미
  `inventory_allocations.status` 에 쓰는 말이라, 단계 결과에 같은 낱말을 쓰면
  *"SHIP 단계"* 와 *"SHIPPED 상태"* 가 로그에서 구별이 안 된다
  (`sim_time.py` 가 단계 이름을 동사형으로 둔 것과 같은 이유다).

⚠️ **아직 예약이 0행이라 실물로는 안 돈다.** 판매가 확정 판매를 물류 경계로
   넘기기 시작하면 그날부터 이 자리가 돈다 — 그때까지 이 함수는 매일
   `NOTHING_DUE` 를 낸다.

★ 2026-09-30 재구성 BL-018: `master/outbound_flow.py` 에서 옮겼다. 역할이 다른 부분은 갈랐다 —
  `domain/outbound_flow.py`; `repository/outbound_flow.py`; `schemas/outbound_flow.py`. 무엇이
  어디로 갔는지는 설계서 대응표 `master/` 절.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from app.contracts.sales_logistics import (
    SalesOutboundReservationRequest,
    reservation_id_for_sale_item,
)
from app.core import db as core_db
from app.logistics.service.fefo_allocation import allocate_reserved_stock_fefo
from app.logistics.service.outbound import (
    recommend_fefo_candidates,
    release_reservation,
    reserve_confirmed_sale_available,
    ship_allocated_stock,
)
from app.master.domain.outbound_flow import due_today, fully_shipped_sales, reserved_qty_of
from app.master.domain.sim_time import SimPhase, phase_instant
from app.master.repository.outbound_flow import due_sale_items
from app.master.schemas.outbound_flow import DueSaleItem, OutboundOut, SaleItemOutcome
from app.sales.service.sale_ledger import mark_sale_delivered

#: 🔴 **할당 시각을 파생하는 단계 이름.** `sim_time.PHASES` 의 것을 그대로 쓴다.
#:
#: ★ 문자열을 상수로 둔 이유는 검사가 이 값을 찾기 때문이다. 손으로 다시 적으면
#:   철자가 갈리고, `phase_instant` 가 `ValueError` 를 내는 날까지 아무도 모른다.
ALLOCATE_PHASE: SimPhase = "ALLOCATE"


# ── ③ 조립 ──────────────────────────────────────────────────────────────


def ship_due_sales(
    as_of: date,
    *,
    sim_run_id: str,
    borrow: core_db.Borrow | None = None,
    due_fn: Callable[..., Sequence[DueSaleItem]] = due_sale_items,
    reserve_fn: Callable[..., Any] = reserve_confirmed_sale_available,
    allocate_fn: Callable[..., Any] = allocate_reserved_stock_fefo,
    ship_fn: Callable[..., Any] = ship_allocated_stock,
    deliver_fn: Callable[..., Any] = mark_sale_delivered,
    release_fn: Callable[..., Any] = release_reservation,
    candidates_fn: Callable[..., Any] = recommend_fefo_candidates,
) -> OutboundOut:
    """`as_of` 에 나갈 판매를 **순서대로 내보낸다. Lot 은 안 고른다.**

    :param release_fn: 할당이 터진 예약을 **그날 놓아주는** 자리 (`_ship_one` 참조).
    :param candidates_fn: 터진 순간의 FEFO 후보를 **읽기만** 하는 자리 (`_ship_one` 참조).

    ★ **`receive_arrivals` · `collect_receipts` 와 같은 모양이다** — `as_of` 하나를
      받고, 예외를 밖으로 안 내고, 상태를 값으로 돌려준다.

    🔴 **Lot 선택 loop 가 여기 없다.** 그것은 `allocate_reserved_stock_fefo` 안에서
       잠금과 함께 돈다 (그 파일이 *"마스터가 밖에서 for 루프를 돌면 ②를 지킬 자리가
       없다"* 고 적어 뒀다). 이 함수가 정하는 것은 **어느 예약을 실행할지**뿐이다.

    🔴 **`sim_run_id` 는 기본값 없는 키워드다.** 기본값을 두면 그 값이 곧 업무
       규칙이 되고, 안 넘긴 자리가 조용히 번인 장부의 판매를 내보낸다. 안 넘기면
       **`TypeError` 로 터져야** 그 자리를 그날 안다
       (`revalidation.revalidate_scenario` 와 같은 모양이다).

    :param due_fn: 그날 나갈 것을 읽는 자리. **검사가 대역을 끼우는 곳**이다.
    """
    open_connection = core_db.connection if borrow is None else borrow
    with ExitStack() as stack:
        try:
            conn = stack.enter_context(open_connection())
        except Exception as exc:  # noqa: BLE001 - 출고 실패가 그날을 통째로 세우면 안 된다.
            return OutboundOut(as_of=as_of, status="FAILED", reason=f"연결 실패: {exc}")

        try:
            rows = due_fn(conn, as_of=as_of, sim_run_id=sim_run_id)
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            return OutboundOut(as_of=as_of, status="FAILED", reason=f"나갈 것을 못 읽었다: {exc}")

        due = due_today(rows, as_of)
        if not due:
            return OutboundOut(
                as_of=as_of,
                status="NOTHING_DUE",
                reason=f"{as_of.isoformat()} 이 납품 기준일인 확정 판매가 없다",
            )

        # 🔴 **시각을 여기서 한 번 파생한다.** 같은 하루의 할당은 같은 시각으로 적힌다.
        decided_at = phase_instant(as_of, ALLOCATE_PHASE)

        results: list[SaleItemOutcome] = []
        for row in due:
            # 🔴 **한 판매가 터져도 여기서 안 멈춘다.** `_ship_one` 이 예외를 값으로
            #    옮기고, 다음 판매가 그대로 이어 돈다.
            results.append(
                _ship_one(
                    conn,
                    row,
                    as_of=as_of,
                    decided_at=decided_at,
                    reserve_fn=reserve_fn,
                    allocate_fn=allocate_fn,
                    ship_fn=ship_fn,
                    release_fn=release_fn,
                    candidates_fn=candidates_fn,
                )
            )

        notes: list[str] = []
        delivered = _mark_delivered(conn, results, deliver_fn=deliver_fn, notes=notes)
        failed = tuple(one.sale_item_id for one in results if one.status == "FAILED")
        short = tuple(one.sale_item_id for one in results if one.status == "SHORT")
        # 🔴 **두 사실을 한 문장에 합치지 않는다.** 터진 것과 확보 0kg 은 다른 일이라
        #    수를 더하면 읽는 사람이 왜 그랬는지 되짚을 수 없다.
        parts = [f"{len(failed)}건이 터졌다"] if failed else []
        if short:
            parts.append(f"{len(short)}건이 확보 0kg 이라 못 나갔다")
        reason = f"{len(results)}건 중 " + " · ".join(parts) if parts else ""
        return OutboundOut(
            as_of=as_of,
            status="RAN",
            reason=reason,
            items=tuple(results),
            delivered_sales=delivered,
            notes=tuple(notes),
        )


def _ship_one(
    conn: Any,
    row: DueSaleItem,
    *,
    as_of: date,
    decided_at: datetime,
    reserve_fn: Callable[..., Any],
    allocate_fn: Callable[..., Any],
    ship_fn: Callable[..., Any],
    release_fn: Callable[..., Any] = release_reservation,
    candidates_fn: Callable[..., Any] = recommend_fefo_candidates,
) -> SaleItemOutcome:
    """판매 품목 하나를 예약 → 할당 → 출고까지 태운다.

    🔴 **할당 뒤에 커밋이 하나 선다.** `ship` 이 터졌을 때 롤백이 할당까지 걷어 가면
       *"어느 Lot 에서 뺄지 정했다"* 는 사실이 사라진다 — 출고가 실패한 것과 할당이
       없던 것은 다른 사실이다.

    ★ **할당을 되돌리는 함수는 안 부른다.** `cancel_allocation` 은 이 파일에 임포트조차
      없다 — 할당을 물리는 것은 물류의 판단이지 출고 실패의 자동 결과가 아니다.

    🔴 **예약 뒤 커밋이 하나 더 있어서, 할당이 터지면 예약만 남는다.** 그 예약은
       처리한 날 뒤로는 다시 안 잡히고(`due_sale_items`), 놓아주는 길도
       없어 **영원히 그 품목의 가용재고를 잡는다** — REH-0914 실측 7건 · 6,436kg 이
       판매가능량을 0 으로 깔았다 (2026-09-15). 그래서 **할당이 안 선 예약은 그날
       놓아준다** (`release_fn` · `released_as_of = as_of`). WP-3 는 `released_as_of`
       로 «그날 있다가 사라진 예약» 을 그대로 되살리므로 과거 장부가 안 어긋난다.

       ⚠️ **출고가 터진 것은 안 놓아준다.** 할당이 서 있으면 재실행이 멱등하게 이어
          나간다 — 놓아주면 그 할당까지 `CANCELLED` 로 내려간다.

    🟡 **터지면 그날 후보를 읽어 값으로 남긴다** (2026-09-15 · 관측). `candidates_fn` 은
       **읽기만** 한다 — 결과의 `status` · `reason` 을 안 바꾸고 칸만 채운다.
    """
    reservation_id = reservation_id_for_sale_item(row.sale_item_id)
    예약_섰다 = False
    할당_섰다 = False
    # ★ 확보량을 기억해 둔다 — 터진 가지에서도 얼마를 잡고 있었는지가 보여야 한다.
    확보량: Decimal | None = None
    try:
        reserved = reserve_fn(
            conn,
            SalesOutboundReservationRequest(
                reservation_id=reservation_id,
                sim_run_id=row.sim_run_id,
                sale_id=row.sale_id,
                sale_item_id=row.sale_item_id,
                item_id=row.item_id,
                # 🔴 **판매 요구량 그대로다.** 모자라면 물류가 확보한 만큼만 잡고,
                #    못 잡은 몫은 `ReservationResult` 에 보이게 남는다.
                quantity_kg=row.quantity_kg,
                as_of=as_of,
            ),
        )
        conn.commit()
        확보량 = reserved_qty_of(reserved)
        # ★ 확보량을 못 읽은 것(None)도 «섰다» 로 본다 — 행이 있을 수 있어서다.
        예약_섰다 = 확보량 != 0

        if 확보량 == 0:
            # 🔴 **없는 예약을 할당하지 않는다** (물류 §5.1). 예전에는 그대로
            #    `allocate` 로 가서 `OutboundIntegrityError` 가 났고, 그것이 `FAILED`
            #    로 적혔다 — **정상 사업 결과가 장애로 기록됐다.**
            return SaleItemOutcome(
                sale_id=row.sale_id,
                sale_item_id=row.sale_item_id,
                reservation_id=reservation_id,
                status="SHORT",
                reason=f"확보 0kg — 요구 {row.quantity_kg}kg",
                required_qty_kg=row.quantity_kg,
                item_id=row.item_id,
                reserved_qty_kg=확보량,
            )

        allocate_fn(
            conn,
            reservation_id=reservation_id,
            as_of=as_of,
            # 🔴 **벽시계가 아니다.** `sim_time` 이 `as_of` 에서 파생한 값이다.
            decided_at=decided_at,
        )
        # 🔴 **여기가 그 커밋이다.** 아래 출고가 터져도 할당은 남는다.
        conn.commit()
        할당_섰다 = True

        shipped = ship_fn(
            conn,
            reservation_id=reservation_id,
            shipped_at=as_of,
            sale_item_id=row.sale_item_id,
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - 한 판매가 하루를 세우면 안 된다.
        conn.rollback()
        reason = f"{type(exc).__name__}: {exc}"
        # 🟡 **후보는 놓아주기 전에 읽는다.** 알고 싶은 것은 «터진 순간의 후보» 다 —
        #    놓아준 뒤에 읽으면 그 예약이 사라진 세상을 본다. Lot 후보(`_available_lots`)는
        #    지금 예약이 아니라 할당만 빼므로 값이 같지만, 순서를 뒤로 두면 그 전제가
        #    바뀌는 날 칸이 조용히 틀린다.
        #
        # ⚠️ **할당이 선 뒤(출고가 터진 것)는 안 읽는다.** 그때는 Lot 이 이미 정해져 후보가
        #    답이 아니고, 출고 실패 뒤 트랜잭션 순서(`reserve · commit · allocate · commit ·
        #    ship · rollback`)를 그대로 둔다.
        후보수: int | None = None
        후보합: Decimal | None = None
        if not 할당_섰다:
            후보수, 후보합 = _candidates_at(conn, row, as_of=as_of, candidates_fn=candidates_fn)
        release_outcome: Literal["RELEASED", "RELEASE_FAILED"] | None = None
        if 예약_섰다 and not 할당_섰다:
            문장, release_outcome = _release_stranded(
                conn, reservation_id=reservation_id, as_of=as_of, release_fn=release_fn
            )
            reason += 문장
        return SaleItemOutcome(
            sale_id=row.sale_id,
            sale_item_id=row.sale_item_id,
            reservation_id=reservation_id,
            status="FAILED",
            reason=reason,
            required_qty_kg=row.quantity_kg,
            item_id=row.item_id,
            reserved_qty_kg=확보량,
            candidate_lot_count=후보수,
            candidate_available_kg=후보합,
            error_type=type(exc).__name__,
            release_outcome=release_outcome,
        )

    return SaleItemOutcome(
        sale_id=row.sale_id,
        sale_item_id=row.sale_item_id,
        reservation_id=reservation_id,
        status="RAN",
        shipped_qty_kg=Decimal(getattr(shipped, "shipped_qty_kg", 0) or 0),
        required_qty_kg=row.quantity_kg,
        item_id=row.item_id,
        reserved_qty_kg=확보량,
    )


def _candidates_at(
    conn: Any, row: DueSaleItem, *, as_of: date, candidates_fn: Callable[..., Any]
) -> tuple[int | None, Decimal | None]:
    """터진 순간 **그날의 FEFO 후보** 수 · 가용합. 🔴 **읽기만 한다.**

    🔴 **여기서 터져도 결과를 안 바꾼다.** 관측이 판정을 흔들면 안 된다 — 칸만 `None`
       이고 `status` · `reason` 은 부르는 쪽이 정한 그대로다.

    ★ **읽은 뒤 롤백한다.** 읽기라도 트랜잭션을 열어 두면 뒤따르는 놓아주기가 그 안에서
      돈다 — 깨끗한 트랜잭션에서 시작하게 둔다.
    """
    try:
        후보 = tuple(
            candidates_fn(conn, sim_run_id=row.sim_run_id, item_id=row.item_id, as_of=as_of)
        )
        수: int | None = len(후보)
        합: Decimal | None = sum(
            (Decimal(str(one.available_qty_kg)) for one in 후보), Decimal(0)
        )
    except Exception:  # noqa: BLE001 - 관측 실패가 출고 결과를 바꾸면 안 된다.
        수, 합 = None, None
    # ★ 롤백 실패도 관측의 일이다 — 결과는 그대로 나간다.
    with contextlib.suppress(Exception):
        conn.rollback()
    return 수, 합


def _release_stranded(
    conn: Any, *, reservation_id: str, as_of: date, release_fn: Callable[..., Any]
) -> tuple[str, Literal["RELEASED", "RELEASE_FAILED"]]:
    """할당이 안 선 예약을 **그날** 놓아준다. 🔴 예약은 이미 커밋됐다 — 롤백이 못 걷는다.

    ★ 여기서 터져도 하루는 계속 간다. 못 놓아준 사실은 사유에 남긴다 — 그래야 다음
      사람이 «왜 아직 잡고 있나» 를 되짚을 수 있다.

    :returns: `(사유에 붙일 문장, 놓아주기 결과)`. 🔴 **결과를 문장에서 뽑지 않게 값으로
        같이 돌려준다** (2026-09-15) — 문장을 고치는 날 칸이 조용히 틀리지 않도록.
    """
    try:
        release_fn(conn, reservation_id=reservation_id, released_as_of=as_of)
        conn.commit()
        return " · 할당이 안 서 예약을 놓아줬다", "RELEASED"
    except Exception as exc:  # noqa: BLE001 - 놓아주기 실패가 하루를 세우면 안 된다.
        conn.rollback()
        return f" · 예약을 못 놓아줬다 ({type(exc).__name__}: {exc})", "RELEASE_FAILED"


def _mark_delivered(
    conn: Any,
    results: Sequence[SaleItemOutcome],
    *,
    deliver_fn: Callable[..., Any],
    notes: list[str],
) -> tuple[str, ...]:
    """모든 품목이 나간 판매만 `DELIVERED` 로 옮긴다.

    ⚠️ **판매 lifecycle 은 판매 것이다.** 이 함수는 판매가 내준 훅
      (`mark_sale_delivered` — *"caller-owned completion hook"*) 을 부르기만 하고,
      `sales.order_status` 를 직접 쓰지 않는다.

    ★ **여기서 터져도 출고를 되돌리지 않는다.** 물건은 이미 나갔다 — 나간 사실과
      판매 상태가 못 따라온 사실은 다르고, 뒤엣것은 `notes` 에 남는다.
    """
    delivered: list[str] = []
    for sale_id in fully_shipped_sales(results):
        try:
            deliver_fn(conn, sale_id=sale_id)
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            notes.append(f"{sale_id} 를 DELIVERED 로 못 옮겼다: {type(exc).__name__}: {exc}")
            continue
        delivered.append(sale_id)
    return tuple(delivered)
