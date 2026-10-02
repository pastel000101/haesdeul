"""마스터가 실어 주는 입력 3종 — 어디서 왔는지를 값과 함께 들고 다닌다.

정의서 §3.2.5 의 명시적 예외다. 이 셋은 "해당 에이전트에게 요청" 이 성립하지 않아
마스터가 직접 싣는다.

```text
forecast          ML 은 호출 구조 밖 독립 실행이라 부를 대상이 없다
confirmed_orders  1차 판매는 에이전트가 아니라 마스터 관할 Rule 이다
policy_values     정책 테이블 — 운반 주체 미결 (M-19)
```

값만 싣지 않고 `SourcedInput` 으로 싣는다. 같은 `forecast` 라도 오늘 실제 DB 에서 읽은
것과 mock 파일에서 온 것은 의사결정의 무게가 다르다. 값만 넘기면 그 차이가 사라지고,
리포트를 읽는 사람은 전부 실측으로 읽는다. §3.7.6("못 한 것을 한 척하지 않는다")이 검증
커버리지에 대해 말하는 것과 같은 이야기다.

비어 있으면 지어내지 않는다. 못 읽으면 `MISSING` 으로 두고 매입이 `missing_data` 로
답하게 한다 — 0 이나 평균값으로 메우면 그럴듯하게 틀린 계획이 나온다.

`forecast` 에는 mock 대체 경로가 없다. ML DB 가 죽었는데 mock 으로 돌면 장애가 정상으로
보인다 (그 갈래가 2026-08-31 · 09-03 피마늘 실측을 오염시켰다). 못 읽으면 `MISSING` 이고,
매입이 `missing_data: ["forecast"]` 로 `RUNTIME_NOT_READY` 를 낸다. 못 한 것이 한 것으로
안 보인다.

`MOCK` 은 어휘에 남긴다. 만드는 곳이 지금은 없지만, 새 다리가 생기면 그것이 스스로
`MOCK` 이라고 말할 자리가 있어야 하고 그때 `ProcurementFlow` 가 세운다.

SQL 은 `repository/inputs.py`, 행 → payload · 파생 계산은 `domain/inputs.py`, 모델과 어휘는
`schemas/inputs.py` 에 있다. 여기는 SELECT 하나에 조회 연결 하나를 빌리고 등급을 붙인다.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.contracts.core import ITEMS
from app.core import db as core_db
from app.core.settings import get_db_schema
from app.master.domain.inputs import (
    ORDER_WINDOW_DAYS,
    booked_orders,
    forecast_missing,
    forecast_payload,
    mix_ratios,
    orders_from_demand,
    stale_batch_why,
)
from app.master.repository.inputs import (
    select_booked_order_rows,
    select_item_daily_demands,
    select_latest_forecast,
    select_order_cycle,
    select_partner_demand,
)
from app.master.schemas.inputs import PROCUREMENT_TARGET_KIND, MasterInputs, SourcedInput


def collect_inputs(item: str, as_of: date, *, sim_run_id: str) -> MasterInputs:
    """세 입력을 모은다. 하나가 실패해도 나머지는 싣는다.

    `sim_run_id` 에 기본값을 두지 않는다. 확정 주문은 실행마다 다른 사실이다. 안 넘긴
    자리는 조용히 번인이나 전 실행을 읽지 말고 여기서 터진다.

    매입 전용이다. 부르는 자리는 `service/procurement.py` 의 `_inputs_for` 하나이고,
    그래서 시세 계열도 매입 것(`PROCUREMENT_TARGET_KIND`)으로 정해서 넘긴다. 판매는 셋을
    안 모으고 예측 하나만 따로 읽는다 (`service/sales.py` 의 `_sales_forecast`).
    """
    return MasterInputs(
        forecast=load_forecast(item, as_of, target_kind=PROCUREMENT_TARGET_KIND),
        confirmed_orders=load_confirmed_orders(item, as_of, sim_run_id=sim_run_id),
        policy_values=load_policy_values(item, as_of),
    )


# ── forecast ────────────────────────────────────────────────────────────


def load_forecast(item: str, as_of: date, *, target_kind: str) -> SourcedInput:
    """ML 예측. `as_of` 당일 배치만 쓴다.

    `target_kind` 에 기본값을 두지 않는다.

      기본값은 곧 업무 규칙이고, 안 넘긴 자리가 조용히 경매가로 답한다 — 조회에
      `'AUC'` 가 박혀 있으면 판매도 경매가를 읽고, 경매가로 사서 경매가로 팔게 된다.

      `service/revalidation.py` 의 `revalidate_scenario` 가 `as_of` 에 대해 같은 결론을
      냈다 — 안 넘기면 터져야 한다.

    부르는 자리가 자기 계열을 골라서 넘긴다.

      .. code-block:: text

          매입   PROCUREMENT_TARGET_KIND   경매에서 산다
          판매   SALES_TARGET_KIND         중도매로 판다

    미래 배치를 집으면 백테스트 성적이 통째로 무효가 된다 (look-ahead). 뷰가 `as_of`
    컬럼을 갖고 있으므로 그 이하만 고른다.

    그 조건만으로는 지연 상한이 없다. 그날 배치가 없으면 조회는 옛 배치를 집는다.
    그것을 `MEASURED` 로 실으면 오래된 예측으로 오늘 매입안을 만든다. 실측(배추,
    2026-09-04):

      .. code-block:: text

          as_of        집은 배치      지연
          2026-09-04   2026-09-04     0일      ← 정상
          2026-08-25   2026-01-27     210일    ← 당일 배치 아님
          2026-08-01   2026-01-27     186일    ← 당일 배치 아님
          2026-06-01   2026-01-27     125일    ← 당일 배치 아님

      `#227` 의 "ML DB 가 죽으면 선다" 에는 배치가 없는 날도 들어야 한다.

    그래서 집은 행의 `as_of` 가 요청 `as_of` 와 같을 때만 `MEASURED` 다. 하루만 밀려도
    안 쓴다. 다르면 `MISSING` 이고, `#227` 의 경로(MISSING → 매입 `RUNTIME_NOT_READY` →
    `E4_NOT_STARTED`)를 그대로 탄다.

    공휴일 달력을 심지 않는다. ML 배치 유무가 곧 개장 여부다 — 배치일 실측에서 평일은
    `base_dt == 그날` 로 배치가 있고, 2026-01-01(신정) 에만 배치가 없어 간격이 2일로
    벌어졌다. 달력을 따로 두면 그 달력이 틀리는 날이 온다.
    """
    try:
        row = _forecast_from_db(item, as_of, target_kind)
    except Exception as error:  # noqa: BLE001 — 적재 실패가 Flow 를 죽이면 안 된다
        return forecast_missing(f"DB 조회 실패 ({error})")
    if row is None:
        return forecast_missing(f"{as_of} 이전 예측 배치가 없다")
    if row["as_of"] != as_of:
        return forecast_missing(stale_batch_why(as_of, row["as_of"]))
    return SourcedInput(
        key="forecast",
        payload=forecast_payload(row),
        grade="MEASURED",
        # 이 한 줄이 계열을 나른다. 나중에 "이 단가가 경매였나 중도매였나" 를 되짚을 수
        # 있는 자리는 여기뿐이다. 매입은 경매, 판매는 중도매라 이 값이 실제로 갈린다.
        source=f"v_ml_price_forecast(as_of={row['as_of']}, {row['target_kind']})",
        note=str(row.get("quality_note") or ""),
    )


def _forecast_from_db(item: str, as_of: date, target_kind: str) -> dict[str, Any] | None:
    """`as_of` 이하 최신 배치 한 행. 당일인지는 여기서 안 본다.

    계열도 여기서 안 정한다. 받아서 넘기기만 한다 — 조회에 계열을 박으면 부르는
    자리가 무엇이든 그 계열 값이 올라온다.

    `as_of <= %s` 를 걷으면 미래 배치를 집는다 (look-ahead). 그러면 백테스트 성적이
    통째로 무효가 되므로 이 조건은 남는다.

    주의: 이 조회에는 지연 상한이 없다. 그날 배치가 없으면 210일 전 배치가 그대로
    올라온다 (2026-08-25 → 2026-01-27, 실측 2026-09-04). 당일인지를 거르는 것은
    `load_forecast` 의 몫이다 — 조회는 후보를 집어 오고, 쓸지 말지는 부르는 쪽이
    정한다.
    """
    schema = get_db_schema()
    with core_db.read_connection() as conn:
        return select_latest_forecast(
            conn, item=item, as_of=as_of, target_kind=target_kind, schema=schema
        )


# ── confirmed_orders ────────────────────────────────────────────────────


def load_confirmed_orders(item: str, as_of: date, *, sim_run_id: str) -> SourcedInput:
    """향후 납품 예정. 실제 주문이 있으면 그것을, 없으면 파트너 수요에서 파생한다.

    실제 주문은 이 실행의 것만 읽는다. 축 없이 읽으면 앞 걷기가 구간 끝에 남긴
    `CONFIRMED` 주문이 다음 걷기의 확정 수요로 샌다 — 같은 코드로 다시 걸 때마다 무
    매입이 판마다 11~14kg 늘었다(실측).

    파생 경로(`_orders_from_demand`)에는 축을 붙이지 않는다. `partner_item_demands` 는
    실행 축 없는 기준표이고 실행마다 같아야 맞다.

    파생분을 "확정 주문" 이라 부르지 않는다. 앞으로 납품할 `sales` 건이 없으면 파트너
    일수요로 메우는데, 그건 예상 수요이지 확정이 아니다. 등급을 `DERIVED` 로 두고
    파생식을 `note` 에 적어 리포트에 내보낸다 — 값만 넘기면 매입도 사람도 확정으로
    읽는다.

    실패 처리: 조회가 터지면 파생으로 넘어가지 않는다 (`#651`).

      예외를 "확정 건이 없다" 와 같은 길로 보내면 DB 장애가 파트너 명목 수요로 사는
      정상 걷기처럼 보인다. `load_forecast` 가 조회 실패에 대해 낸 결론과 같다 —
      비운다.

      .. code-block:: text

          조회 성공 · 0건   DERIVED    파트너 일수요 × 기간 (그대로)
          조회 성공 · N건   MEASURED   이 실행의 확정 주문 (그대로)
          조회 실패         MISSING    메우지 않는다 → 매입 missing_data

    사유는 원인을 단정하지 않는다. 예외 클래스와 메시지만 적는다.
    """
    try:
        booked = _orders_from_db(item, as_of, sim_run_id=sim_run_id)
    except Exception as error:  # noqa: BLE001 — 적재 실패가 Flow 를 죽이면 안 된다
        return SourcedInput(
            key="confirmed_orders",
            payload=None,
            grade="MISSING",
            source="-",
            note=f"확정 주문 조회 실패 ({type(error).__name__}: {error})",
        )
    why = "앞으로 납품할 확정 건이 없다"

    if booked:
        return SourcedInput(
            key="confirmed_orders",
            payload=booked,
            grade="MEASURED",
            source="sales + sale_items",
            note=f"{as_of} 이후 {ORDER_WINDOW_DAYS}일 납품 예정",
        )

    try:
        return _orders_from_demand(item, as_of, why)
    except Exception as error:  # noqa: BLE001
        return SourcedInput(
            key="confirmed_orders",
            payload=None,
            grade="MISSING",
            source="-",
            note=f"{why} · 파생도 실패 ({error})",
        )


def _orders_from_db(item: str, as_of: date, *, sim_run_id: str) -> dict[str, Any] | None:
    schema = get_db_schema()
    with core_db.read_connection() as conn:
        rows = select_booked_order_rows(
            conn,
            schema=schema,
            item=item,
            after=as_of,
            until=as_of + timedelta(days=ORDER_WINDOW_DAYS),
            sim_run_id=sim_run_id,
        )
    return booked_orders(rows, item=item, as_of=as_of)


def _orders_from_demand(item: str, as_of: date, why: str) -> SourcedInput:
    """파트너 일수요 × 기간을 주문 주기로 쪼갠 파생 주문(`domain/inputs.orders_from_demand`).

    일수요 · 주문 주기를 조회 연결 하나씩 따로 읽는다. 일수요가 없으면 주기는 읽지 않고
    `LookupError` 다.
    """
    schema = get_db_schema()
    with core_db.read_connection() as conn:
        demand = select_partner_demand(conn, item=item, schema=schema)
    if demand is None:
        raise LookupError(f"{item} 파트너 일수요가 없다")

    schema = get_db_schema()
    with core_db.read_connection() as conn:
        cycle_row = select_order_cycle(conn, schema=schema)
    return orders_from_demand(item, as_of, why, demand=demand, cycle_row=cycle_row)


# ── policy_values ───────────────────────────────────────────────────────


def load_policy_values(item: str, as_of: date) -> SourcedInput:
    """매입이 쓰는 정책값.

    `item_mix_ratio` 는 파트너 일수요에서 파생한다. 정책 테이블에 그 키가 없고, 쓰임이
    "한 품목이 임계 이상을 차지하면 mix 축을 닫는다" 라 품목별 수요 비중이 바로 그
    뜻이다. 파생식을 `note` 에 남긴다.

    `contract_price_krw` 는 비운다. 계약 단가가 DB 에 없고, 매입 계약상 필수가 아니다 —
    없으면 `margin_warning` 이 `null` 로 나가는 것이 정상 경로다. 평균값으로 메우면 마진
    경고가 조용히 틀린다.
    """
    del as_of  # 정책은 현재 유효분 하나뿐이다 (버전 축은 policy_version 이 갖는다)
    try:
        ratios = _mix_ratio_from_demand()
    except Exception as error:  # noqa: BLE001
        return SourcedInput(
            key="policy_values", payload=None, grade="MISSING", source="-", note=str(error)
        )
    if not ratios:
        return SourcedInput(
            key="policy_values",
            payload=None,
            grade="MISSING",
            source="partner_item_demands",
            note="품목별 일수요가 없어 비중을 낼 수 없다",
        )

    payload: dict[str, Any] = {"item_mix_ratio": ratios}
    return SourcedInput(
        key="policy_values",
        payload=payload,
        grade="DERIVED",
        source="partner_item_demands",
        note=(
            f"item_mix_ratio = 품목 일수요 ÷ 전체 일수요 "
            f"({item} {ratios.get(item, 0):.3f}) · contract_price 는 DB 에 없어 비움"
        ),
    )


def _mix_ratio_from_demand() -> dict[str, float]:
    """품목 비중 — 분모는 계약 품목만이다 (`#286`).

    계약 밖 품목이 분모에 들면 비중이 눌린다 (매입 실측 2026-09-10 · 배추 0.7643 vs 0.8096).

    「보일 때 거르기」로는 안 된다 — 비중은 이미 눌린 값이라 받는 쪽에서 되돌릴 수 없다.
    되돌릴 수 없는 것은 원천에서 막는다.

    DB 행은 안 고친다. 지난 기록을 고쳐 쓰면 기록이 거짓이 된다 — 읽을 때만 거른다.

    품목 이름을 여기 다시 적지 않는다. 정본은 `app.contracts.core.ITEMS` 하나다 — 두
    벌을 두면 계약이 늘거나 줄 때 한쪽만 바뀐다.
    """
    schema = get_db_schema()
    with core_db.read_connection() as conn:
        rows = select_item_daily_demands(conn, items=ITEMS, schema=schema)
    return mix_ratios(rows)
