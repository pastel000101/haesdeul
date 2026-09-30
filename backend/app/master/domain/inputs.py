"""마스터 입력의 등급 · 파생 계산 — 행을 payload 로 바꾸고 결측 사유를 짓는다. SQL 은
  `repository/inputs.py`.

★ 2026-09-30 재구성 BL-018: `master/inputs.py` 에서 옮겼다 — `injected_keys`, `stale_batch_why`,
  `forecast_payload`, `forecast_missing`, `plain`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.master.schemas.inputs import REQUEST_GRADE, SourcedInput


def injected_keys(sources: Any) -> tuple[str, ...]:
    """출처표에서 **주입분만** 추린다 — 화면 문구가 읽는 자리.

    ★ `ProcurementRunResponse` 에 칸을 새로 만들지 않는다. 같은 사실의 주인은
      `input_sources` 하나이고, 화면은 그것을 읽어 문장으로 옮기기만 한다.

    ★ **`mocked_inputs` 와 섞지 않는다.** 그것은 `grade == "MOCK"` 만 세고 실행을
      세우는 데 쓴다. 주입은 세울 일이 아니라 적을 일이다.
    """
    prefix = f"{REQUEST_GRADE}:"
    return tuple(key for key, source in (sources or {}).items() if str(source).startswith(prefix))


def stale_batch_why(as_of: date, batch_as_of: date) -> str:
    """당일 배치가 아니라는 사유. **셋을 다 적는다** — 요청일 · 최신 배치일 · 지연일수.

    사람이 읽고 *"언제 것을 집을 뻔했는지"* 를 알아야 한다. 지연일수가 없으면
    두 날짜를 눈으로 빼야 하고, 210일과 1일이 같은 문장으로 보인다.

    ⚠️ **원인을 단정하지 않는다.** 당일 배치가 없는 이유는 공휴일일 수도, ML 이
      안 돈 것일 수도, 적재가 늦은 것일 수도 있는데 **마스터는 그 셋을 구분할
      수단이 없다.** 뷰에는 "배치가 있다/없다" 만 있고 "왜 없다" 가 없다.
      사유가 원인을 단정하면 다음 사람이 엉뚱한 데를 판다 — 사실만 적는다.
    """
    delay = (as_of - batch_as_of).days
    return f"{as_of} 당일 예측 배치가 없다 (가장 최신 배치 {batch_as_of} · {delay}일 전)"


def forecast_payload(row: dict[str, Any]) -> dict[str, Any]:
    """뷰 행을 매입이 받는 형태로. **키를 고르기만 하고 값은 손대지 않는다.**

    🔴 **`use_recommended` 를 더했다** (2026-09-03 · 매입 `#192`).

      ML 이 신뢰도 플래그 셋을 붙여 보내는데 매입이 하나도 안 읽고 있었다.
      매입은 *"payload 에 칸이 없어서 못 읽는다"* 로 진단했는데 **절반만 맞았다.**

      ```text
      is_filled · is_gated   행별   뷰가 daily[] 안에 넣어 이미 간다
      use_recommended        조합별  여기서 버리고 있었다
      ```

    ★ **`daily` 안의 둘은 손대지 않는다.** 뷰가 `jsonb_build_object` 로 넣은
      그대로 나른다 — 마스터가 풀어 다시 조립하면 ML 이 준 모양이 바뀐다.

    🔴 **`as_of` · `target_kind` 를 더했다** (2026-09-11 · 걷기 실측).

      ML 계약(`app/contracts/forecast.py Forecast`)이 **필수**로 두는 칸인데 여기서 버리고
      있었다. 매입은 안 읽어서 안 아팠고, 판매는 그 모델을 그대로 쓰므로 봉투가
      통째로 거부됐다 — **206일에서 537건.** `use_recommended` 때와 같은 모양이다.

    ⚠️ 아직 안 나르는 것이 셋 있다 — `has_filled_rows` · `filled_count` ·
      `quality_note`. 앞 둘은 `daily` 에서 셀 수 있는 파생이고, `quality_note` 는
      사람이 읽는 문장이라 `SourcedInput.note` 로 이미 화면에 간다.
      **읽겠다는 파트가 생기면 그때 더한다.**
    """
    return {
        # 🔴 **`as_of` 와 `target_kind` 는 ML 계약의 필수 칸이다** (2026-09-11).
        #    뷰가 주는데 여기서 버리고 있었다 — `use_recommended` 때와 같은 모양이다.
        #
        #    ★★ 매입은 이 둘을 안 읽어서 안 아팠고, 판매는 ML 모델(`app.contracts
        #      .forecast.Forecast`)을 그대로 쓰므로 **없으면 봉투가 통째로 거부된다.**
        #      걷기 206일에서 판매 537건이 이 자리에서 죽었다.
        "as_of": row["as_of"],
        "target_kind": row["target_kind"],
        "generated_at": row["generated_at"],
        "item": row["item"],
        "unit": row["unit"],
        "current_price": plain(row["current_price"]),
        "horizon_days": plain(row["horizon_days"]),
        "daily": row["daily"],
        "model_version": row["model_version"],
        "use_recommended": row.get("use_recommended"),
    }


def forecast_missing(why: str) -> SourcedInput:
    """🔴 **못 읽으면 비운다. mock 으로 메우지 않는다** (2026-09-03).

    전에는 여기서 `app.purchase_agent.mocks` 를 집어 왔다. 그러면 ML DB 장애가
    **정상 실행처럼** 보인다 — 매입이 안을 만들고 세 부서가 판정하고 `E1_APPROVED`
    까지 간다. 사람이 `input_sources` 를 읽지 않으면 아무도 모른다.

    ★ 비우면 매입이 `missing_data: ["forecast"]` 로 `RUNTIME_NOT_READY` 를 낸다.
      **없는 것과 못 만든 것을 가르는 것**이 이 프로젝트의 §1.2-10 이다.
    """
    return SourcedInput(key="forecast", payload=None, grade="MISSING", source="-", note=why)


# ── 값 정리 ─────────────────────────────────────────────────────────────


def plain(value: Any) -> Any:
    """`Decimal` 을 파이썬 수로. **정수는 정수로 남긴다.**

    매입 계약이 `qty_kg` 를 정수로 받는 자리가 있어, 무조건 `float` 로 바꾸면
    소수/정수 불일치가 거기서 터진다.
    """
    if isinstance(value, Decimal):
        as_float = float(value)
        return int(as_float) if as_float.is_integer() else as_float
    return value


#: 확정 주문을 내다볼 기간. 매입 ③이 `total_kg ÷ order_window_days` 로 일수요를 낸다.
ORDER_WINDOW_DAYS = 14


def booked_orders(
    rows: Sequence[Mapping[str, Any]], *, item: str, as_of: date
) -> dict[str, Any] | None:
    """확정 판매 줄 → `confirmed_orders` payload. 줄이 없으면 `None`."""
    if not rows:
        return None
    orders = [
        {
            "sale_id": r["sale_id"],
            "qty_kg": plain(r["quantity_kg"]),
            "due_date": r["sale_date"].isoformat(),
        }
        for r in rows
    ]
    return {
        "as_of": as_of.isoformat(),
        "item": item,
        "orders": orders,
        "total_kg": sum(o["qty_kg"] for o in orders),
    }


def orders_from_demand(
    item: str,
    as_of: date,
    why: str,
    *,
    demand: Mapping[str, Any],
    cycle_row: Mapping[str, Any] | None,
) -> SourcedInput:
    """파트너 일수요 × 기간. **주문 주기 간격으로 쪼갠다.**

    ⑤ 노드가 `due_date` 별 분포로 등급-신선도를 맞추므로 총량 한 덩어리로 주면
    "전량을 첫날 납품" 으로 읽힌다. 주기(`order_cycle_days`)를 그대로 쓴다.
    """
    cycle = int(cycle_row["order_cycle_days"]) if cycle_row else 1
    cycle = max(1, cycle)

    daily = plain(demand["daily_demand_kg"])
    orders = [
        {
            "sale_id": None,  # 실제 주문이 아니다 — id 를 지어내지 않는다
            "qty_kg": round(daily * cycle, 1),
            "due_date": (as_of + timedelta(days=offset)).isoformat(),
        }
        for offset in range(cycle, ORDER_WINDOW_DAYS + 1, cycle)
    ]
    return SourcedInput(
        key="confirmed_orders",
        payload={
            "as_of": as_of.isoformat(),
            "item": item,
            "orders": orders,
            "total_kg": round(sum(o["qty_kg"] for o in orders), 1),
        },
        grade="DERIVED",
        source="partner_item_demands · v_current_partner_demand",
        note=(
            f"{why} → 일수요 {daily}kg × {ORDER_WINDOW_DAYS}일, 주기 {cycle}일로 분할 "
            f"({demand['demand_basis']}"
            f"{', 잠정값' if demand['provisional'] else ''}) · 확정 주문이 아니다"
        ),
    )


def mix_ratios(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """품목 일수요 ÷ 전체 일수요. 합이 0 이면 빈 표."""
    total = sum(plain(r["daily_demand_kg"]) for r in rows)
    if not total:
        return {}
    return {r["item_name"]: round(plain(r["daily_demand_kg"]) / total, 4) for r in rows}
