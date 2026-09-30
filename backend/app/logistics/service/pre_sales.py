"""PRE_SALES — 판매가 묻는 품목의 판매가능량 · 납기 · 원가 기준.

★ 2026-09-30 재구성 BL-015: `logistics/adapter.py` 의 `_pre_sales` 를 옮겼다(조립 그대로). 요청
  해석은
  `domain/pre_sales.py`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from app.contracts.envelope import AgentReply, AgentRequest, ExecutionMetadata
from app.logistics.domain.agent_evidence import (
    evidence,
    inventory_by_item_evidences,
    lots_ref_of,
    policies_ref_of,
    policy_ref,
    snapshot_ref,
    to_float,
    to_float_or_none,
)
from app.logistics.domain.agent_replies import (
    AGENT,
    T_INVENTORY,
    T_LOTS,
    T_SALES_SIGNALS,
    execution_meta,
    logistics_run_id,
    not_ready_reply,
    snapshot_error_reply,
)
from app.logistics.domain.pre_sales import (
    DELIVERY_MISSING_NAMES,
    confirmed_inventory_cost_basis,
    delivery_input_error,
    query_scope,
    sales_ask,
)
from app.logistics.domain.rules import evaluate_sales_business_signals
from app.logistics.domain.tools import (
    build_inventory_by_item,
    build_lot_constraints,
    evaluate_delivery_feasibility,
    supply_capacity_by_date,
)
from app.logistics.schemas.monitoring import snapshot_observed_as_of
from app.logistics.service.agent_read import SnapshotLoadError, load_read

# ---------------------------------------------------------------------------
# PRE_SALES — 판매 제안 전 "지금 팔 수 있는 것" 컨텍스트 (#346)
# ---------------------------------------------------------------------------

#: ★ **WP-4 가 셋을 실제로 계산하게 되면서 세 «모른다» 이름이 사라졌다.**
#:
#: ```text
#: ~WP-3  SUPPLY_CAPACITY_BY_DATE_UNRESOLVED   권위 계산이 없다
#:        TRANSPORT_LEAD_TIME_UNRESOLVED       standard_minutes 칸이 없다
#:        EARLIEST_DELIVERY_DATE_UNRESOLVED    가장 이른 납기일을 내는 함수가 없다
#: WP-4~  tools.supply_capacity_by_date · TRANSPORT_LEAD_DAYS · earliest_delivery_date_for
#: ```
#:
#: 🔴 **값을 냈는데 «모른다» 로도 적으면 계약이 스스로 모순된다.** 그래서 이름만 지운
#:    것이 아니라 **낼 수 있게 된 다음에** 지웠다.
#:
#: ⚠️ **`DELIVERY_ROUTE_UNRESOLVED` 하나는 살아 있다** —
#:    `tools.evaluate_delivery_feasibility` 가 계약 표를 못 읽었을 때 쓴다. 계약 행이
#:    없는 것은 회사 상태라 그때는 판정을 안 낸다.


def pre_sales_reply(request: AgentRequest) -> tuple[AgentReply, ExecutionMetadata]:
    """판매가 후보를 만들기 **전에** 묻는 것 — *"지금 무엇을 얼마나 팔 수 있나."*

    ★ **새 판매가능량 엔진이 아니다.** 숫자는 전부 `tools` · `rules` 의 결정론 함수가
      만들고 여기는 번역만 한다 (이 파일의 다른 handler 와 같은 규율).

    🔴 **기존 `/logistics/sales` 경로를 부르지 않는다** (#346 분석 결과). 닮은 이름이라
       재사용처럼 보이지만 **목적이 다른 사이클**이다.

    ```text
    /logistics/sales   H1 **승인 매입**을 미래 입고로 Overlay 한 뒤의 창고 판정
    PRE_SALES          판매 제안 **전**의 초기 컨텍스트 — 승인 매입이 아직 없다
    ```

      ★ 셋이 각각 다른 이유로 막힌다.

      ```text
      run_logistics_sales()               _get_snapshot_or_none(as_of) → sim_run_id 축 소실
                                          + save_logistics_agent_run() → DB write
      run_logistics_sales_with_snapshot()  enrich_logistics_response() → LLM 경로
      run_logistics_sales_scenario()       request.approved_purchase 를 반드시 읽는다
      evaluate_sales_rules()               future_occupancy_by_date(=Overlay 산출)를 전제한다
      ```

      🔴 **가짜 승인 매입을 만들어 통과시키지 않는다.**
         `LogisticsApprovedPurchaseCommitment` 은 `total_qty_kg > 0` ·
         `arrival_schedule` 최소 1건이라 **빈 값도 0 도 넣을 수 없다** — 넣으려면
         없는 입고를 지어내야 하고, 그 지어낸 입고가 `LOG-H01`(미래 점유 ≤ 보장 capacity)
         판정을 그대로 바꾼다. 숫자는 나오고 에러도 안 나며 봉투도 통과한다.

    ★ **그래서 재사용 단위는 함수다** — `build_inventory_by_item` ·
      `build_lot_constraints` · `evaluate_sales_business_signals` 셋은 승인 매입 없이
      돌고, 세 함수가 PRE_SALES 가 답할 수 있는 것의 전부다.

    ★ **payload 는 판매 계약(`app.sales.schemas.proposal.SalesLogisticsContext`)의 낱말을 쓴다.**
      🔴 **그 모듈을 import 하지 않는다** — 조정자를 건너뛰고 두 부서를 실행 계층에서
      붙이면 판매가 자기 파일을 고치는 날 물류가 같이 깨진다 (마스터가 판매 어휘를
      베껴 두고 테스트로만 대조하는 것과 같은 판단, `contracts/envelope.Capability`).
      맞추는 것은 **JSON 모양뿐**이다.

      🔴 **숫자를 실은 셋만 payload 최상위에 둔다.**

      ```text
      inventory_by_item                  → sellable_supply.inventory_by_item
      lot_constraints                    → sellable_supply.lot_constraints
      shared_daily_outbound_capacity_kg  → delivery_feasibility.daily_outbound_capacity_kg
      ```

         나머지는 판매 계약 그대로 중첩인데, 이 셋만 올라온 것은 취향이 아니라
         **봉투가 중첩 안의 숫자를 주소지정하지 못하기 때문**이다.

      ```text
      envelope._CLAIM_PATH   ^(?P<key>[^\\[\\].]+)\\[(?P<sel>[^\\]]+)\\]\\.(?P<sub>.+)$
                             key 에 점을 못 쓴다 → 한 겹만 판다
      envelope.required_claims   Mapping 값은 통째로 건너뛴다
      ```

         중첩해 두면 두 가지가 동시에 일어난다 — 판매가능 수량·Lot 수량·신선도·출고
         여력에 **근거가 하나도 요구되지 않고**(검사가 없어서 통과), 그렇다고 근거를
         달면 `sellable_supply.inventory_by_item[배추].available_qty_kg` 가 어디도 못
         가리켜 `E-EVIDENCE-ORPHAN` 이 된다.

         ★ **가장 가까운 조상에 다는 것도 답이 아니다.** 출고 여력을
           `delivery_feasibility` 안에 두고 근거를 그 블록 이름에 달아 봤더니
           *"`delivery_feasibility` 라는 판정의 값이 5,000kg"* 으로 읽혔다 — 그 판정은
           `UNRESOLVED` 라 **근거와 대상의 뜻이 어긋난 채 봉투를 통과했다.**
           그래서 그 숫자도 정책 이름 그대로 최상위로 올렸다.

         **근거를 붙일 수 있는 자리가 최상위뿐**이라 숫자를 실은 셋만 올린다.
         받는 쪽이 제자리로 옮기는 것은 위 표대로 **키 셋을 옮기는 일**이다.

    ★ **판정하지 않는다.** 판매 승인·거절은 판매와 재무가 하고 물류는 사실만 낸다 —
      `judgment_fields` 가 비어 있고 새 verdict 도 만들지 않는다 (`status_query_reply` 와 같다).
    """
    as_of = request.context.as_of
    run_id = logistics_run_id(request)
    tools: list[str] = [T_INVENTORY]

    try:
        read = load_read(as_of=as_of, sim_run_id=request.context.sim_run_id)
    except SnapshotLoadError:
        return snapshot_error_reply(request, run_id, tools)
    snapshot = read.snapshot if read is not None else None
    if snapshot is None:
        return not_ready_reply(
            request,
            run_id,
            tools,
            missing=("logistics_snapshot", "logistics_runtime_fixture"),
            reason="물류 스냅샷을 읽지 못했다",
        )
    # ★ as_of 대조 — 다른 날의 재고는 그날의 사실이 아니다 (§1.2-6)
    #
    # ★ **사유에 날짜를 적지 않는다.** `check_reasoning` 의 `E-REASONING-NUMERIC` 은
    #   `contributes_to_band` 와 무관하게 돌아서 `2025-12-31` 같은 문자열이 걸린다.
    #   어긋난 기준일은 `missing_data` 이름이 이미 나른다.
    if snapshot.as_of != as_of:
        return not_ready_reply(
            request,
            run_id,
            tools,
            missing=(f"logistics_snapshot@{as_of.isoformat()}",),
            reason="물류 스냅샷 기준일이 요청 기준일과 다르다",
        )

    # ── 현재 확정 판매가능량 ─────────────────────────────────────
    #
    # 🔴 **이 한 함수가 confirmed sellable 의 유일한 주인이다.** 새 계산식을 만들지
    #    않는다 — `outbound.item_free_stock_qty`(예약이 실제로 잡을 수 있는 양)와 같은
    #    답을 내도록 차감 규칙이 글자 그대로 맞춰져 있고, 그 둘이 갈리면 매입·판매는
    #    팔 수 있다고 보는데 예약은 못 잡는 상태가 된다.
    #
    # ★ **`None` 은 fail-closed 다.** 예약·할당 축(`outbound_commitments`)이나 확정
    #   출고의 품목 축을 못 읽었다는 뜻인데, 그때 `lot_constraints` 합계로 대신 답하면
    #   **이미 팔린 재고를 다시 팔 수 있다고 답하게 된다.** 판매는 밴드가 없어 이
    #   회신 없이도 시작하지만(`sales_flow._collect_supply_context`), 시작하는 것과
    #   틀린 수량을 주는 것은 다르다.
    inventory_by_item = build_inventory_by_item(snapshot)
    if inventory_by_item is None:
        return not_ready_reply(
            request,
            run_id,
            tools,
            missing=("inventory_by_item",),
            reason=(
                "현재 판매 가능 재고를 확정하지 못했다 — "
                "예약·할당 축이나 확정 출고의 품목 축을 읽지 못했다."
            ),
        )

    # ── 출고 여력 ────────────────────────────────────────────────
    #
    # ★ **없으면 READY 를 내지 않는다.** 판매 사이클의 기존 Rule 이 같은 기준이다 —
    #   `evaluate_sales_rules` 의 `calculation_ready` 가 `N17`
    #   (`shared_daily_outbound_capacity_kg`)을 필수로 세고 있다. 기준을 새로 정하는
    #   것이 아니라 **그 Rule 이 이미 정해 둔 것을 따른다.**
    outbound_capacity = snapshot.shared_daily_outbound_capacity_kg
    if outbound_capacity is None:
        return not_ready_reply(
            request,
            run_id,
            tools,
            missing=("shared_daily_outbound_capacity_kg",),
            reason="공용 일일 출고 여력 정책이 없어 물류 상태를 답할 수 없다",
        )

    # ── Lot 근거 ─────────────────────────────────────────────────
    tools.append(T_LOTS)
    lots = build_lot_constraints(snapshot)

    # 🔴 **잔여 신선도를 낸 그 분모**를 함께 나른다. `remaining_freshness_days` 만 주면
    #    받는 쪽이 *"며칠 중 며칠이 남았나"* 를 알 수 없어 `operational_limit_days`
    #    원값으로 역산하는데, `중` 등급은 유효 한계가 `operational × medium_factor` 라
    #    그 역산이 **갓 입고된 Lot 을 임박으로 만든다** (`InventoryLotSnapshot`
    #    `effective_freshness_limit_days` 주석이 지적한 그 자리).
    #
    # ★ **다시 계산하지 않는다** — Repository 가 remaining 을 만들 때 실제로 쓴 값을
    #   `lot_id` 로 그대로 집어 온다. `build_lot_constraints` 가 이 칸을 안 나르는 것은
    #   `LotConstraint` 계약이라 물류 스키마를 여기서 넓히지 않는다 (#346 범위 밖).
    freshness_limits = {
        lot.lot_id: lot.effective_freshness_limit_days for lot in snapshot.on_hand_by_lot
    }

    # ── 신선도 업무 위험 ─────────────────────────────────────────
    #
    # ★ **승인 매입 없이 도는 유일한 판매 Rule 이다.** `evaluate_sales_rules` 와 달리
    #   스냅샷만 읽는다.
    #
    # 🔴 **`FRESHNESS_QUALITY_RISK` 는 `SELL_PRIORITY` 가 아니다.** 이름이 비슷해 섞기
    #    쉬운데 축이 다르다 — 이쪽은 *"지금 팔 수 있는 재고인가"*(물리 신선도)이고
    #    `SELL_PRIORITY` 는 *"언제 팔고 싶은가"*(회전관리 · `item_turnover_policies`)다.
    #    `turnover.py` 모듈 주석이 **둘은 동시에 다른 답을 낼 수 있어야 한다**고 못박고
    #    있다. 이름을 바꿔 대신 쓰지 않는다.
    tools.append(T_SALES_SIGNALS)
    business = evaluate_sales_business_signals(snapshot=snapshot)

    # ── 사용자가 물은 것 ─────────────────────────────────────────
    #
    # 🔴 **없는 값을 지어내지 않는다.** 수량도 날짜도 마스터가 실어 준 것만 읽는다 —
    #    없으면 그 축의 판정을 안 한다 (`query_scope` 와 같은 규율).
    asked = sales_ask(request)

    # ── 미래 확정 출고 ───────────────────────────────────────────
    #
    # 🔴 **스냅샷이 이미 들고 있는 한 벌을 쓴다. 다시 읽지 않는다 (WP-4B).**
    #
    #    `snapshot.confirmed_outbound_schedule` 은 Repository 가
    #    `outbound_schedules.confirmed_outbound_at` 으로 채운 **WP-3 정본**이고
    #    (`confirmed_outbound_json` 은 죽은 칸이다), Capacity 축이 이미 그 값을 쓴다.
    #
    #    ⚠️ **종전에는 어댑터가 같은 질의를 자기 커넥션으로 한 번 더 돌렸다.** 두 읽기
    #       사이에 판매가 확정·취소되면 **같은 회신 안에서** Capacity 와 납기 판정이
    #       서로 다른 «미래 출고» 를 보고 답한다 — `as_of` 가 같아도 DB 행은 그 사이에
    #       바뀔 수 있다. 한 벌만 읽으면 그 갈림이 구조적으로 없다.
    #
    # ★ **`None` 은 0 이 아니다.** fixture 가 그 축을 `UNRESOLVED` 로 적었다는 뜻이라
    #   (`domain/snapshot.schedule_source`) 확인 못 한 것을 «출고 0kg» 으로 놓으면 하루
    #   여력을 통째로 비어 있다고 답하게 된다. 그때는 납기도 날짜별 공급량도 안 낸다.
    outbound_rows = snapshot.confirmed_outbound_schedule
    outbound_by_date: dict[date, Decimal] = {}
    for row in outbound_rows or ():
        outbound_by_date[row.date] = outbound_by_date.get(row.date, Decimal(0)) + row.quantity_kg

    # ── 운송 계약 ────────────────────────────────────────────────
    #
    # 🔴 **여기서 DB 를 열지 않는다.** 계약은 `load_read` 가 같은 읽기 한 벌에 담아
    #    왔다 (`schemas/current.LogisticsRead.delivery_route`) — 어댑터는 번역만 한다.
    if read.delivery_route_error:
        return delivery_input_error(request, run_id, tools)
    route = read.delivery_route

    delivery = evaluate_delivery_feasibility(
        as_of=as_of,
        daily_outbound_capacity_kg=outbound_capacity,
        outbound_prep_lead_days=read.policy.outbound_prep_lead_days,
        delivery_route=route,
        confirmed_outbound_known=outbound_rows is not None,
        requested_quantity_kg=asked.quantity_kg,
        preferred_delivery_date=asked.delivery_date,
        confirmed_outbound_on_preferred_kg=(
            None if asked.delivery_date is None else outbound_by_date.get(asked.delivery_date)
        ),
    )

    # ── 날짜별 공급량 ────────────────────────────────────────────
    #
    # 🔴 **창을 물류가 만들지 않는다.** 사용자가 물은 납기일 하나가 답할 날짜이고,
    #    안 물었으면 답할 날짜가 없다. 여기서 임의 창(예: 18일)을 만들면 **묻지도 않은
    #    날짜의 공급량**이 판매 근거로 나간다.
    #
    # ★ **빈 목록은 «못 냈다» 가 아니다.** 그래서 `SUPPLY_CAPACITY_BY_DATE_UNRESOLVED`
    #   를 안 단다 — 계산에 실패한 것과 물어본 날짜가 없는 것은 다른 사실이다 (§1.2-10).
    #
    # 🔴 **미래 확정 출고를 못 읽었으면 날짜별 공급량도 안 낸다.** 그 값의 상한 절반이
    #    그 축이라(§확정 판매가능량 = min(재고, 남은 출고 여력)) 모르는 채로 내면
    #    **재고 축만 본 수치**가 확정 공급량 행세를 한다.
    supply_dates = (
        [asked.delivery_date]
        if asked.delivery_date is not None and outbound_rows is not None
        else []
    )
    supply_rows = supply_capacity_by_date(
        snapshot,
        dates=supply_dates,
        inventory_by_item=inventory_by_item,
        confirmed_outbound_by_date=outbound_by_date,
        daily_outbound_capacity_kg=outbound_capacity,
        item=asked.item,
    )

    # ── 확정 물량의 재고 취득원가 ────────────────────────────────
    #
    # 🔴 **원가의 주인은 창고다.** 어느 Lot 이 얼마에 들어왔는지는 물류 장부의 사실이고,
    #    판매도 재무도 그것을 다시 셈할 근거가 없다. 종전에는 아무도 내지 않아 재무가
    #    매번 `authoritative_inventory_cost_basis` 없음으로 판정을 닫았다.
    #
    # ★ **못 내면 안 낸다 — READY 는 그대로다.** 재고원가는 판매 제안 자체의 전제가
    #   아니라 재무 판정의 재료다. 없으면 재무가 `RUNTIME_NOT_READY` 로 멈추고,
    #   그 이름(`authoritative_inventory_cost_basis`)은 재무가 이미 부른다 — 여기서
    #   같은 사실에 두 번째 이름을 붙이지 않는다.
    #
    # 🔴 **Lot 선택 순서는 실제 자동 출고와 같은 FEFO 다** (`turnover.fefo_sort_key`).
    #    이 시점에는 이 판매의 할당이 아직 없어서 «출고된 Lot» 을 못 적는다 —
    #    적는 것은 *"지금 출고한다면 FEFO 가 집을 Lot"* 이고, 그래서 **순서만이라도**
    #    실제와 같아야 한다. 규칙이 다르면 예상이 빗나가는 것이 아니라 처음부터
    #    다른 것을 재는 것이 된다.
    cost_basis = confirmed_inventory_cost_basis(
        snapshot,
        asked=asked,
        inventory_by_item=inventory_by_item,
        supply_rows=supply_rows,
    )

    # ── payload ──────────────────────────────────────────────────
    ref = snapshot_ref(snapshot)
    lots_ref = lots_ref_of(snapshot)
    policies_ref = policies_ref_of(snapshot)

    # 🔴 **구조적으로 못 내는 것의 이름.** READY 를 막지는 않지만 조용히 빠지지도
    #    않는다 (§1.2-10).
    #
    # ★ **낸 것은 여기서 뺀다.** WP-4 가 `supply_capacity_by_date` · `delivery_route` ·
    #   `transport_lead_time` · `earliest_delivery_date` 를 실제로 계산하게 됐다 —
    #   값을 냈는데 *"모른다"* 로도 적으면 계약이 스스로 모순된다.
    #
    # 🔴 **`delivery_feasibility` 를 여기 적지 않는다.** 그 블록은 **있다** — 판정이
    #    무엇이든 있는 것을 없다고 적으면 마스터가 *"물류가 납기 블록을 안 보냈다"* 로
    #    읽고 사용자에게 엉뚱한 것을 달라고 한다 (M-1 §5.1).
    missing: list[str] = []
    if delivery.status == "UNRESOLVED":
        # ★ 물류 내부 어휘(`*_UNRESOLVED`)를 마스터가 읽는 이름으로 옮긴다 —
        #   `interpretation._MISSING_DATA_NAMES` 와 같은 층 구분이다.
        missing.extend(DELIVERY_MISSING_NAMES[code] for code in delivery.uncertainties)

    payload: dict[str, Any] = {
        "query_scope": query_scope(request, as_of),
        # ★ **중첩 한 벌이 정본이다 (WP-4).** 종전에는 숫자를 실은 셋을 payload 최상위로
        #   끌어올려 중복시켰다 — 봉투가 중첩 안의 숫자를 주소지정하지 못해서였다
        #   (`envelope._CLAIM_PATH` 가 한 겹만 팠다). WP-4 가 그 봉투를 고쳤으므로
        #   **판매 계약 모양 그대로** 낸다. 한 사실이 두 자리에 있으면 받는 쪽이
        #   어느 것을 볼지 갈린다.
        "sellable_supply": {
            "status": "READY",
            "inventory_by_item": [
                {"item": entry.item, "available_qty_kg": to_float(entry.available_qty_kg)}
                for entry in inventory_by_item
            ],
            "lot_constraints": [
                {
                    "lot_id": lot.lot_id,
                    "item": lot.item,
                    # 🔴 **예약·할당 차감 전 raw 다.** 위 `inventory_by_item` 과 **다른 뜻**
                    #    이라 합산해서 판매가능량을 다시 만들면 안 된다 — 근거 컨텍스트다.
                    "available_qty_kg": to_float(lot.available_qty_kg),
                    # 신선도는 **없을 수 있고 음수일 수 있다** — 둘 다 그대로 둔다.
                    # 음수는 *"신선도 기준을 지난 실제 일수"* 라는 사실이고, 0 으로
                    # 접으면 **기준일 당일**과 **닷새 지난 Lot** 이 같은 값이 된다.
                    "remaining_freshness_days": lot.remaining_freshness_days,
                    "effective_freshness_limit_days": freshness_limits.get(lot.lot_id),
                    "grade": lot.grade,
                    "status": lot.status,
                }
                for lot in lots
            ],
            # ★ **묻지 않은 날짜는 안 만든다.** 빈 목록은 *"물어본 날짜가 없다"* 다.
            "supply_capacity_by_date": [
                {
                    "date": row.date.isoformat(),
                    # 🔴 **`None` 이 정상값인 자리다.** 그날 이미 확정된 출고가 하루
                    #    여력을 넘으면(`OUTBOUND_CAPACITY_OVERCOMMITTED`) 확정 공급량을
                    #    낼 수 없다 — 0 으로 접으면 정책·데이터 이상이 «오늘은 더 못
                    #    판다» 는 정상 사실로 보인다.
                    "confirmed_sellable_quantity_kg": to_float_or_none(
                        row.confirmed_sellable_quantity_kg
                    ),
                    "freshness_unresolved_inbound_quantity_kg": to_float(
                        row.freshness_unresolved_inbound_quantity_kg
                    ),
                    "uncertainties": list(row.uncertainties),
                }
                for row in supply_rows
            ],
            # 🔴 **`None` 이 정상값인 자리다.** 물은 품목·수량이 없거나, 그 물량을 FEFO 로
            #    다 덮지 못하거나, 헐어야 할 Lot 의 입고일·단가를 못 읽었다는 사실이다.
            #    0원으로 메우면 «원가 0원짜리 판매» 가 마진 판정을 통과한다.
            "inventory_cost_basis": (
                None
                if cost_basis is None
                else {
                    "item": cost_basis.item,
                    "quantity_kg": to_float(cost_basis.quantity_kg),
                    "amount_krw": to_float(cost_basis.amount_krw),
                    "allocation_method": cost_basis.allocation_method,
                    "cost_method": cost_basis.cost_method,
                    "included_components": list(cost_basis.included_components),
                    # ★ 하위 호환용 대표 하나. 계보는 아래 `source_refs` 다.
                    "source_ref": cost_basis.source_ref,
                    "source_refs": list(cost_basis.source_refs),
                    "evidence_grade": cost_basis.evidence_grade,
                }
            ),
            "uncertainties": [],
        },
        "delivery_feasibility": {
            "status": delivery.status,
            # ★ **정책 원값을 옮긴 것이다** (`snapshot.shared_daily_outbound_capacity_kg`).
            #   여기서 계산한 무엇이 아니라 3PL 공용 정책값이고, 근거가 그렇게 말한다.
            "daily_outbound_capacity_kg": to_float(delivery.daily_outbound_capacity_kg),
            # ★ **계약 표에서 읽은 값이다** — 문자열을 코드에 박지 않았다
            #   (`transport.resolve_fixed_route`).
            "delivery_route": delivery.delivery_route,
            # 🔴 **준비일과 다른 값이다.** 합치지 않는다 (`tools.TRANSPORT_LEAD_DAYS`).
            #
            # ★ **이름에 단위를 안 붙인다 (WP-4B).** 판매 계약의 정본 이름이
            #   `transport_lead_time` 이다. 단위를 이름에 붙인 종전 표기는 물류가
            #   지은 것이었고, 호환 alias 를 같이 내면 **같은 사실이 두 주소**로
            #   다니게 된다 — 받는 쪽이 어느 것을 볼지 갈린다. 단위는 Evidence 의
            #   `unit="days"` 가 나른다.
            "transport_lead_time": delivery.transport_lead_time,
            "earliest_delivery_date": (
                None
                if delivery.earliest_delivery_date is None
                else delivery.earliest_delivery_date.isoformat()
            ),
            # 업무 사유다 — 축을 못 읽은 것은 아래 uncertainties 다.
            "reason_codes": list(delivery.reason_codes),
            "uncertainties": list(delivery.uncertainties),
        },
        # ★ **없는 판정을 지어내지 않는다.** 판매 사이클의 하드 제약은
        #   `evaluate_sales_rules` 소유인데 그것은 승인 매입 Overlay 를 전제한다.
        "hard_constraints": [],
        # ★ **기존 코드명을 그대로 보존한다.** severity 도 점수도 새로 만들지 않는다.
        "soft_warnings": [
            {"code": code} for code in dict.fromkeys([*business["signals"], *business["warnings"]])
        ],
        "missing_data": list(missing),
        # ★ **ref 를 발명하지 않는다** — Repository 가 스냅샷에 실어 둔 것 그대로다.
        "evidence_refs": list(snapshot.evidence_refs),
    }

    # ── 근거 ─────────────────────────────────────────────────────
    #
    # 🔴 **값도 출처도 그대로다. 바뀐 것은 주소뿐이다 (WP-4).**
    #
    # ```text
    # inventory_by_item[배추].available_qty_kg
    #   → sellable_supply.inventory_by_item[배추].available_qty_kg
    # lot_constraints[LOT-X].*    → sellable_supply.lot_constraints[LOT-X].*
    # shared_daily_outbound_capacity_kg
    #   → delivery_feasibility.daily_outbound_capacity_kg
    # ```
    supply = payload["sellable_supply"]
    evidences = inventory_by_item_evidences(
        supply["inventory_by_item"], snapshot, prefix="sellable_supply."
    )
    for row in supply["lot_constraints"]:
        base = f"sellable_supply.lot_constraints[{row['lot_id']}]"
        evidences.append(
            evidence(
                f"{base}.available_qty_kg",
                row["available_qty_kg"],
                "kg",
                lots_ref,
                f"{row['item']} · 상태 {row['status']} — Lot 물리 잔량이다. "
                "예약·할당 차감 전이라 판매가능량이 아니다",
            )
        )
        if row["remaining_freshness_days"] is not None:
            evidences.append(
                evidence(
                    f"{base}.remaining_freshness_days",
                    row["remaining_freshness_days"],
                    "days",
                    lots_ref,
                    "유효 보관한계 − 입고 후 경과일. 음수는 한계를 지난 실제 일수다",
                    extra_ref_ids=(policies_ref,) if policies_ref != lots_ref else (),
                )
            )
        if row["effective_freshness_limit_days"] is not None:
            evidences.append(
                evidence(
                    f"{base}.effective_freshness_limit_days",
                    row["effective_freshness_limit_days"],
                    "days",
                    policies_ref,
                    "잔여 신선도를 낸 분모. `중` 등급은 운영 보관한계에 계수가 곱해진 값이라 "
                    "품목 정책 원값과 다를 수 있다",
                )
            )
    for row in supply["supply_capacity_by_date"]:
        base = f"sellable_supply.supply_capacity_by_date[{row['date']}]"
        if row["confirmed_sellable_quantity_kg"] is not None:
            evidences.append(
                evidence(
                    f"{base}.confirmed_sellable_quantity_kg",
                    row["confirmed_sellable_quantity_kg"],
                    "kg",
                    lots_ref,
                    "그날까지 신선한 판매가능 재고와 그날 남은 출고 여력 중 작은 값. "
                    "입고 예정은 신선도가 안 정해져 더하지 않았다",
                    source="tool_calc",
                    extra_ref_ids=(ref,) if ref != lots_ref else (),
                )
            )
        evidences.append(
            evidence(
                f"{base}.freshness_unresolved_inbound_quantity_kg",
                row["freshness_unresolved_inbound_quantity_kg"],
                "kg",
                ref,
                "그날까지 들어올 확정 입고량 — Lot 이 아직 없어 신선도가 정해지지 않았다. "
                "판매 근거로 쓰는 양이 아니다",
                source="tool_calc",
            )
        )
    evidences.append(
        evidence(
            "delivery_feasibility.daily_outbound_capacity_kg",
            outbound_capacity,
            "kg",
            policy_ref(read.policy, "shared_daily_outbound_capacity_kg", ref),
            f"3PL 공용 일일 출고 여력 정책값 ({read.policy.policy_version}) — "
            "하루에 내보낼 수 있는 총량이지 납기 가능성 판정이 아니다",
            grade="SIM_FIXED",
        )
    )
    if delivery.transport_lead_time is not None:
        evidences.append(
            evidence(
                "delivery_feasibility.transport_lead_time",
                delivery.transport_lead_time,
                "days",
                ref,
                "MVP 확정 가정 — 운송 소요시간의 정본 칸이 스키마에 없어 측정값이 아니다",
                source="tool_calc",
                grade="SIM_FIXED",
            )
        )
    if payload["missing_data"]:
        evidences.append(
            evidence(
                "missing_data",
                len(payload["missing_data"]),
                "name_count",
                ref,
                "이번 회신이 내지 못한 것의 이름 수 — 값이 아니라 세어 본 것이다",
                source="tool_calc",
            )
        )
    if payload["evidence_refs"]:
        evidences.append(
            evidence(
                "evidence_refs",
                len(payload["evidence_refs"]),
                "ref_count",
                ref,
                "이 회신이 읽은 출처의 건수 — 값이 아니라 세어 본 것이다",
                source="tool_calc",
            )
        )

    reply = AgentReply(
        request_id=request.context.request_id,
        as_of=as_of,
        agent=AGENT,
        mode=request.mode,
        run_id=run_id,
        runtime_status="READY",
        business_status="ok",
        payload=payload,
        evidences=tuple(evidences),
        # 🔴 **재 봤더니 못 쟀다** — 기본값을 그대로 둔 것이 아니다 (#628 Commit 2).
        #    네 Mode 의 회신은 전부 정책값(용량 · 리드타임 · 임계 비율 · 보관한계)을
        #    계산에 넣는데 그 표들에 유효일 칸이 없어, 규칙(§18)대로 결과가 `None` 이다.
        #    ⚠️ **`as_of` 로 메우지 않는다** — 메우면 «안 쟀다» 가 «쟀다» 로 세어진다.
        observed_at=snapshot_observed_as_of(snapshot),
        # 판매 승인·거절을 내지 않는다 — 낸 것이 없으니 근거를 요구할 판정도 없다
        judgment_fields=(),
        missing_data=tuple(dict.fromkeys(missing)),
        # 🔴 **숫자·날짜를 적지 않는다** — `check_reasoning` 의 `E-REASONING-NUMERIC` 이
        #    `contributes_to_band` 와 무관하게 돌아 `5,000` 이나 `2026-09-10` 을 잡는다.
        reasoning=_PRE_SALES_REASONING[delivery.status],
    )
    return reply, execution_meta(request, run_id, tools, reply)


#: 납기 판정별 사유 문장. 🔴 **숫자도 날짜도 안 적는다** (`E-REASONING-NUMERIC`).
_PRE_SALES_REASONING: dict[str, str] = {
    "READY": (
        "현재 판매 가능 재고와 출고 여력을 조회했다. "
        "가장 이른 납기일과 요청 납기일의 출고 여력까지 판정했다."
    ),
    "FAIL": (
        "현재 판매 가능 재고와 출고 여력을 조회했다. "
        "요청한 납기 조건은 준비 리드 또는 하루 출고 여력을 넘어 낼 수 없다."
    ),
    "UNRESOLVED": (
        "현재 판매 가능 재고와 출고 여력을 조회했다. "
        "납기 판정에 필요한 정책 또는 운송 계약을 읽지 못해 납기는 내지 않았다."
    ),
}
