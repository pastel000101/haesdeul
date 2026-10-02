"""Inventory/Logistics Policy 및 Runtime Fact Repository.

여기의 "Snapshot" 은 폐지된 T0 스냅샷이 아니다. 정의서 v2.5 §3.2 가 폐지한
것은 마스터가 전 부서 데이터를 얼려 배포하던 덩어리이고(v1.2 §1.2-9 · §3.1.1 ·
§3.2.3 — v2.5 부록 B 가 각각 대체·폐지로 적은 조항들이다),
이 모듈이 만드는 것은 물류가 자기 도메인만 진입 시점에 1회 읽어 호출이 끝날
때까지 고정하는 값이다 — 정의서 §1.2-13("한 호출 안에서 같은 값을 두 번 조회하지
않는다")의 구현 수단이다.

  두 개념이 같은 단어를 쓰는 탓에 "폐지된 것을 왜 아직 쓰나" 로 읽히기 쉬워 여기에
  구분을 남긴다. 타입 이름(`InventoryLogisticsSnapshot`)은 물류 문서 세트 v1.4 의
  IO Contract 가 그 이름으로 계약을 적고 있어 문서와 함께 움직여야 한다.

이 파일은 읽는 순서와 연결을 맡는다. SQL 은 `repository/current.py`, 규칙은
`domain/snapshot.py` · `domain/grade.py`, 모델은 `schemas/snapshot.py`. 어댑터 · 점검 · 상태
Tool · 독립 Service 는 `read_current_logistics`(연결 없이), 화면 · 보고서는 받은 연결로
`get_current_logistics_read(conn, …)` 를 부른다.

연결 경계: 연결 없이 부르면 읽기마다 따로 빌리고, 받은 연결이 있으면 한 벌 전체를 그
연결로 읽는다.

```text
읽기                         연결 없이 (read_current_logistics)          받은 연결 (화면 · 보고서)
fixture · 정책 · Lot ·       SELECT 마다 조회 연결 하나(`_read_on`)        받은 연결
보관 정책 · 할당 · 예약
일정 목록 둘                  `connection()` + `transaction` 블록 하나     받은 연결
운송 계약                     `connection()` + `transaction` 블록 하나     받은 연결의 SAVEPOINT
```

  한 벌을 조회 연결 하나로 모으는 안은 설계서 §변경 제안에 개선 제안으로 있다.
"""

import logging
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

import psycopg

from app.core import db as core_db
from app.logistics.domain.inbound_schedules import in_transit_from, pending_inbound_from
from app.logistics.domain.snapshot import (
    build_logistics_policy,
    check_fixture_row,
    inventory_lot_from_row,
    runtime_fixture_from,
    single_fixture_row,
)
from app.logistics.readmodel.inbound_schedules import load_schedule_views
from app.logistics.repository.current import (
    get_item_storage_policies,
    get_outbound_commitments,
    outbound_commitments_from,
    select_current_lot_rows,
    select_holding_allocation_rows,
    select_policy_rows,
    select_runtime_fixture_rows,
    select_unallocated_reservation_rows,
)
from app.logistics.repository.outbound_schedules import confirmed_outbound_at
from app.logistics.repository.transport import resolve_fixed_route
from app.logistics.schemas.current import LogisticsRead
from app.logistics.schemas.inbound_schedules import InboundScheduleView
from app.logistics.schemas.snapshot import (
    InTransitItem,
    InventoryLogisticsSnapshot,
    LogisticsPolicy,
    LogisticsRuntimeFixture,
    OutboundCommitment,
    ScheduledQuantity,
)
from app.logistics.schemas.transport import AmbiguousRoute, RouteNotFound

logger = logging.getLogger(__name__)


def _read_on[Rows](conn: Any | None, select: Callable[..., Rows], /, **params: Any) -> Rows:
    """SELECT 하나 — 받은 연결이 있으면 그 연결로, 없으면 조회 연결 하나를 따로 빌려 읽는다."""
    if conn is not None:
        return select(conn, **params)
    with core_db.read_connection() as own:
        return select(own, **params)


def get_active_logistics_policy(conn: Any) -> LogisticsPolicy:
    """현재 Logistics MVP 범위의 active policy를 typed contract로 조회한다."""
    rows = select_policy_rows(conn)
    return build_logistics_policy(rows)


def get_active_logistics_runtime_fixture(
    conn: Any | None, *, as_of: date, sim_run_id: str | None = None
) -> LogisticsRuntimeFixture:
    """요청 기준일과 정확히 일치하는 active MVP runtime fixture 한 건을 조회한다.

    몸통은 `_runtime_fixture_and_views` 다 — 같은 읽기가 일정 views 도 함께 내고,
    `get_current_logistics_read` 는 그것을 `LogisticsRead` 에 실어 화면이 다시
    안 읽게 한다. 이 함수는 Header 만 필요한 호출자를 위한 껍질이다.

    :param conn: 받은 연결. `None` 이면 fixture 는 조회 연결을, 일정 목록은
        `connection()` + `transaction` 블록을 따로 빌린다 (이 모듈 머리의 표).
    """
    fixture, _views = _runtime_fixture_and_views(conn, as_of=as_of, sim_run_id=sim_run_id)
    return fixture


def _runtime_fixture_and_views(
    conn: Any | None, *, as_of: date, sim_run_id: str | None = None
) -> tuple[LogisticsRuntimeFixture, tuple[InboundScheduleView, ...]]:
    """요청 기준일과 정확히 일치하는 active MVP runtime fixture 한 건 + 그날 일정 views.

    조회 축은 `(sim_run_id, as_of, usage_scope)` 다 — DB 의 유일성 축
    (`uq_log_runtime_fixture`)과 같은 축이다. 다르면 유일해야 할 조회가 유일하지
    않다: `sim_run_id` 가 다른 활성 행 둘이 같은 날에 공존할 수 있어 다른 실행의
    상태를 이번 실행의 상태로 읽게 된다.

    `sim_run_id` 는 선택 인자다 — 한 경로 때문이다(#345).

       ```text
       service/agent_read.load_read          봉투(ExecutionContext)로 받아 나른다   (#345)
       service/cycle._get_snapshot_or_none   HTTP 요청에 실행 식별자가 없다         (미해결)
       ```

       독립 Service 경로가 값을 못 나르는 동안 필수로 만들면 물류가 값을 지어내야
       하므로(그것이 곧 fail-open 이다) 축은 열어 둔다. 어댑터 경로는
       `service/agent_read.load_read(*, as_of, sim_run_id)` 로 닫혀 있다 — 거기서는 선택이
       아니다.

       안 받았다고 아무 행이나 고르지 않는다. 그 경우 실행이 둘 보이면
       `ValueError` 로 멈춘다 — "둘 중 하나를 고르지 않는다" 가 이 함수의 규율이다.
       값을 받으면 그 실행으로 좁혀 애초에 둘이 안 보인다.

    :param sim_run_id: 어느 실행의 장부인가. 마스터가 소유한 값이다. `None` 이면
        실행으로 좁히지 않는다 (그리고 둘 이상 보이면 실패한다).
    """
    rows = _read_on(conn, select_runtime_fixture_rows, as_of=as_of, sim_run_id=sim_run_id)
    # 0건과 2건 이상은 다른 종류의 실패다(#121).
    #
    #   0건       그날의 fixture 가 아직 없다 — 부재. 다시 불러도 같다
    #   2건 이상  활성 fixture 가 둘이라 어느 것이 그날의 사실인지 모른다 — 무결성 위반
    #
    # 둘을 같은 LookupError 로 내면 소비자가 가릴 수 없다. 어댑터는 부재를
    # RUNTIME_NOT_READY 로, 실행 오류를 ERROR 로 나누는데(M-1 §5.1) 중복이 부재로
    # 섞이면 깨진 데이터가 "데이터를 주세요" 로 나간다.
    #
    # `sim_run_id` 를 받으면 DB 가 막아 준다 — 그때 2건은 `uq_log_runtime_fixture`
    # 위반이라 실제로 일어날 수 없고, 그래도 검사를 남기는 것은 이 함수가 그 제약을
    # 전제하지 않고도 옳아야 하기 때문이다(WHERE 한 줄이 지워지는 날 여기가 잡는다).
    #
    # 여기서 하나를 고르지 않는다 — 뒤 행이 앞 행을 덮는 것도 고르는 것이다
    # (`find_in_transit_schedule_gap` 의 inbound_id 중복 처리와 같은 규율).
    row = single_fixture_row(rows, as_of=as_of, sim_run_id=sim_run_id)
    return _build_logistics_runtime_fixture(
        row, expected_as_of=as_of, expected_sim_run_id=sim_run_id, conn=conn
    )


def _schedule_lists(
    conn: Any | None, *, sim_run_id: str, as_of: date
) -> tuple[
    list[InTransitItem],
    list[ScheduledQuantity],
    list[ScheduledQuantity],
    tuple[InboundScheduleView, ...],
]:
    """세 예정 목록을 각자의 업무 정본에서 읽는다 (W3-2 · WP-3). 넷째는 그 원천 views.

    ```text
    in_transit           inbound_schedules   Receipt 가 생기면 빠진다     운송 중
    confirmed_inbound    inbound_schedules   Lot + 원장 IN 이 서면 빠진다  미래 점유
    confirmed_outbound   sales · sale_items  sale_date > as_of 인 확정 판매 미래 점유
    ```

    출고 축은 fixture JSON 을 읽지 않는다(WP-3). `confirmed_outbound_json` 은
    판매 확정이 채우는 경로가 하나도 없어 실측 254행 전부 `[]` 였다 — 비어 있는
    칸이 «미래 출고가 없다» 는 사실처럼 읽힌다. `outbound_schedules.confirmed_outbound_at`
    이 판매 정본에서 읽는다.

    앞의 두 목록은 같은 목록이 아니다 — 종료조건이 다르다. `in_transit ⊆
    confirmed_inbound` 라 B-1(`tools.find_in_transit_schedule_gap`)은 그대로 통과한다.

    일정 표는 한 번만 읽는다. 앞의 두 목록은 같은 `load_schedule_views` 결과를
    각자의 종료조건(`in_transit_from` · `pending_inbound_from`)으로 거른 것이라,
    따로 읽으면 같은 289행 질의를 두 번 보낸다(실측 2026-09-15 · 81 ms × 2).
    규칙은 `inbound_schedules` 가 그대로 소유한다.

    `conn` 이 없으면 공통 풀에서 자기 커넥션을 빌린다(어댑터 경로) —
       `connection()` + `transaction` 블록 하나로 두 질의를 읽고 끝에서 commit, 예외면 rollback.
       화면 · 보고서는 자기 연결을 넘긴다 (이 모듈 머리의 표).
    """

    def 읽기(c: Any):
        views = load_schedule_views(c, sim_run_id=sim_run_id, as_of=as_of)
        return (
            in_transit_from(views),
            pending_inbound_from(views),
            confirmed_outbound_at(c, sim_run_id=sim_run_id, as_of=as_of),
            views,
        )

    if conn is not None:
        return 읽기(conn)
    with core_db.connection() as own, core_db.transaction(own):
        return 읽기(own)


def _build_logistics_runtime_fixture(
    row: dict[str, object],
    *,
    expected_as_of: date,
    expected_sim_run_id: str | None = None,
    conn: Any | None,
) -> tuple[LogisticsRuntimeFixture, tuple[InboundScheduleView, ...]]:
    """fixture 행 하나를 계약 타입으로 (+ 그 목록을 만든 일정 views).
    목록은 업무 정본에서 온다(W3-2 · WP-3).

    ```text
    업무 정본에서  in_transit · confirmed_inbound · confirmed_outbound
    fixture 에서   세 status · 나머지 칸                          ← Header 뿐이다
    ```

    status 어휘를 안 바꾼다(`08 §8`). fixture 가 `UNRESOLVED` 라고 적은 축은
    그대로 `UNRESOLVED`(목록 `None`)이고, 그 외에는 정본 결과가 0건이면
    `CONFIRMED_ZERO`, 있으면 `CONFIRMED` 다.

    ```text
    fixture status == UNRESOLVED   →  UNRESOLVED · None    아는 척으로 안 바꾼다
    그 외 · 정본 0건                →  CONFIRMED_ZERO · []
    그 외 · 정본 1건 이상           →  CONFIRMED · [...]
    ```

       주의: status 가 업무 사실의 두 번째 정본이 되면 안 된다. 업무 사실은
       `inbound_schedules` 이고, status 는 Header 의 가용성·Legacy 호환 표시다.
       그래서 `CONFIRMED`/`CONFIRMED_ZERO` 를 저장된 값이 아니라 목록에서 낸다.
    """
    check_fixture_row(row, expected_as_of=expected_as_of, expected_sim_run_id=expected_sim_run_id)

    # ── W3-2 · WP-3: 세 목록의 정본은 전부 fixture JSON 밖에 있다 ────────
    run_id = str(row.get("sim_run_id"))
    in_transit, confirmed_inbound, confirmed_outbound, views = _schedule_lists(
        conn, sim_run_id=run_id, as_of=expected_as_of
    )
    fixture = runtime_fixture_from(
        row, in_transit=in_transit, confirmed_inbound=confirmed_inbound,
        confirmed_outbound=confirmed_outbound,
    )
    return fixture, views


def get_current_inventory_logistics_snapshot(
    *, as_of: date, sim_run_id: str | None = None
) -> InventoryLogisticsSnapshot:
    """Snapshot 만 필요한 소비자용 (독립 Service 경로)."""
    return read_current_logistics(as_of=as_of, sim_run_id=sim_run_id).snapshot


def get_current_logistics_read(
    conn: Any | None, *, as_of: date, sim_run_id: str | None = None
) -> LogisticsRead:
    """Fixture, direct physical lots, Policy를 한 번 읽어 호출 중 고정될 값을 만든다.

    :param conn: 빌려 쓸 커넥션. 화면(`readmodel/console.load_console_runtime`)과 마스터 보고서가
        넘긴다. `None` 이면 읽기마다 따로 빌린다(`read_current_logistics` — 어댑터 ·
        점검 · 상태 Tool · 독립 Service 경로. 대여 표는 이 모듈 머리).

    "한 번"이 계약이다 (정의서 §1.2-13) — 같은 호출이 같은 값을 다시 읽으면 그 사이
    원장이 바뀌어 같은 `as_of` 인데 값이 다른 상태가 성립한다.

    실행 축은 fixture 한 곳에서만 정해진다. 아래 `inventory_lots` · 출고가 잡은 몫
    (`_outbound_commitments`)은 `fixture.sim_run_id` 로 묻는다 — 실행을 가르는 자리는
    fixture 조회 하나이고, 그래서 스냅샷 전체가 같은 실행 위에 선다.

    이것은 Current 읽기다. 과거 조회에 쓰지 않는다.

    ```text
    Current     이 함수                        Agent Runtime · 그 순간의 잔량
    Historical  readmodel/historical          화면 조회 · as_of 시점 원장/사건
    ```

       아래 `inventory_lots` 조회는 `remaining_qty_kg`(Derived Current Cache)로
       Lot 을 고른다. `as_of` 를 받지만 그것은 `received_at` 상한일 뿐이고 잔량은
       언제나 지금 값이다 — 과거 화면이 이 함수를 쓰면 오늘 소진된 재고가
       그날에도 없었던 것으로 보인다(실측: 네 기준일 전부 0 kg).

       이 함수에 `historical=True` 같은 분기를 넣지 않는다. 두 축이 한 함수
          안에 섞이는 순간 어느 호출이 어느 시점을 읽는지 아무도 말할 수 없다.
    """
    fixture, views = _runtime_fixture_and_views(conn, as_of=as_of, sim_run_id=sim_run_id)
    policy = _read_on(conn, get_active_logistics_policy)

    # 물리 점유 대상: 잔량이 남아 실제 창고 안에 존재하는 모든 Lot.
    # status로 거르지 않는다 — 검수·격리·사용불가·신선도 만료 재고도 반출/폐기 전이면
    # 공간을 점유한다. 소진/반출 완료 Lot은 remaining_qty_kg = 0으로 자연히 빠진다
    # (현행 DB의 DEPLETED가 그 예). 가용 여부 판정은 tools.build_inventory_by_item 몫이다.
    #
    # `remaining_qty_kg` 는 DERIVED CURRENT CACHE 다. 정본은 `inventory_moves`
    # 이고 이 컬럼은 그 누계를 들고 있는 지금 값이다(실측 불일치 0건 — 캐시가
    # 틀린 것이 아니라 과거에 쓰면 안 되는 값이다). 과거 잔량은
    # `readmodel/historical.onhand_by_lot_at` 이 원장에서 되살린다.
    inventory_rows = _read_on(
        conn, select_current_lot_rows, sim_run_id=fixture.sim_run_id, as_of=fixture.as_of
    )

    lots = [inventory_lot_from_row(row, as_of=fixture.as_of) for row in inventory_rows]
    used_capacity = sum((lot.available_qty_kg for lot in lots), start=Decimal(0))
    snapshot = InventoryLogisticsSnapshot(
        snapshot_id=None,
        as_of=fixture.as_of,
        on_hand_by_lot=lots,
        # Lot 조회와 별도로 읽는다 — 재고가 0kg인 품목의 보관 정책도 필요하다.
        item_storage_policies=_read_on(conn, get_item_storage_policies),
        in_transit=fixture.in_transit,
        confirmed_inbound_schedule=fixture.confirmed_inbound_schedule,
        confirmed_outbound_schedule=fixture.confirmed_outbound_schedule,
        # 예약·할당 축을 여기서 한 번 읽는다. 안 읽으면 매입에 나가는
        #    `inventory_by_item` 이 이미 팔린 재고를 다시 팔 수 있다고 답한다.
        outbound_commitments=_outbound_commitments(conn, sim_run_id=fixture.sim_run_id),
        used_capacity_kg=used_capacity,
        guaranteed_capacity_kg=policy.guaranteed_capacity_kg,
        burst_capacity_kg=policy.burst_capacity_kg,
        guaranteed_capacity_by_zone_kg=None,
        inbound_lead_days=policy.inbound_lead_days,
        daily_inbound_capacity_kg=policy.daily_inbound_capacity_kg,
        inbound_transport_capacity_kg=policy.inbound_transport_capacity_kg,
        shared_daily_outbound_capacity_kg=policy.shared_daily_outbound_capacity_kg,
        capacity_tight_ratio=policy.capacity_tight_ratio,
        freshness_pressure_ratio=policy.freshness_pressure_ratio,
        evidence_refs=[
            f"DB:logistics_runtime_fixture/{fixture.fixture_id}",
            fixture.source_ref,
            f"DB:inventory_lots/sim_run_id={fixture.sim_run_id}",
            "DB:item_storage_policies",
            *policy.source_refs.values(),
        ],
    )
    노선, 노선오류 = _delivery_route(conn)
    return LogisticsRead(
        snapshot=snapshot,
        policy=policy,
        delivery_route=노선,
        delivery_route_error=노선오류,
        fixture=fixture,
        inbound_schedule_views=views,
    )


def _outbound_commitments(conn: Any | None, *, sim_run_id: str) -> list[OutboundCommitment]:
    """출고가 잡은 몫 — 할당 SELECT · 예약 SELECT 를 차례로 읽어 한 목록으로.

    받은 연결이 있으면 그 연결로 읽고(`get_outbound_commitments`), 없으면 두 SELECT 가 조회
    연결을 하나씩 따로 빌린다.
    """
    if conn is not None:
        return get_outbound_commitments(conn, sim_run_id=sim_run_id)
    allocation_rows = _read_on(None, select_holding_allocation_rows, sim_run_id=sim_run_id)
    reservation_rows = _read_on(None, select_unallocated_reservation_rows, sim_run_id=sim_run_id)
    return outbound_commitments_from(allocation_rows, reservation_rows)


def _delivery_route(conn: Any | None) -> tuple[str | None, bool]:
    """운송 계약 하나를 읽는다. 문자열을 코드에 안 박는다.

    정본은 `logistics_contracts` 표이고 Reader 는 `repository/transport.resolve_fixed_route`
    하나다. 상수로 복제하면 계약 행이 바뀌는 날 코드만 옛 값을 들고 남는다 —
    저쪽이 0 / 1 / 2+ 를 이미 셋 다 다르게 다룬다.

    ```text
    계약 0건    RouteNotFound   → (None, False)    회사 상태다. 납기는 UNRESOLVED 로 간다
    계약 1건    그 계약          → (contract_id, False)
    계약 2건+   AmbiguousRoute  → (None, True)     무결성 위반이라 실행 오류로 올린다
    ```

    어댑터가 아니라 여기서 읽는다. 어댑터가 자기 커넥션을 열면 한 회신 안에서
    읽기가 두 시점으로 갈리고(`LogisticsRead` 가 닫으려는 바로 그 구멍), 어댑터의
    «DB 를 직접 안 만진다» 경계도 함께 깨진다.

    빌린 커넥션에서는 SAVEPOINT 안에서 읽는다. 이 함수는 `psycopg.Error` 를
    삼켜 `(None, True)` 로 답하는데, 공유 커넥션에서 SQL 이 실패하면 그 트랜잭션이
    aborted 상태로 남아 뒤따르는 모든 SELECT 가 `InFailedSqlTransaction` 으로
    죽는다 — 운송 계약 하나를 못 읽은 것이 화면 한 판 전체의 500 이 된다.
    `conn.transaction()` 은 이미 트랜잭션 안이면 SAVEPOINT 를 잡고 예외 때 거기로
    되돌려, 삼킨 오류가 커넥션을 오염시키지 않게 한다.

    `conn` 이 없으면 자기 커넥션을 빌린다(어댑터 경로) — `connection()` +
       `transaction` 블록 하나. 계약 1건이면 commit, 0건 · 2건 이상 · 실패면 그 블록이 rollback 한
       뒤 위 표대로 답한다.
    """
    try:
        if conn is not None:
            with conn.transaction():
                return resolve_fixed_route(conn).logistics_contract_id, False
        with core_db.connection() as own, core_db.transaction(own):
            return resolve_fixed_route(own).logistics_contract_id, False
    except RouteNotFound:
        return None, False
    except (AmbiguousRoute, psycopg.Error, RuntimeError, TypeError, ValueError):
        logger.exception("운송 계약 조회 실패")
        return None, True


def read_current_logistics(*, as_of: date, sim_run_id: str | None = None) -> LogisticsRead:
    """현재 시점 물류 Fact 한 벌을 연결 없이 읽는다 — 어댑터 · 점검 · 상태 Tool · 독립 Service.

    `get_current_logistics_read(None, …)` 와 같다 — 읽기마다 따로 빌린다(조회 연결 여섯 ·
    일정 목록 블록 하나 · 운송 계약 블록 하나, 이 모듈 머리의 표). 조회 연결 하나로 모으는
    안은 설계서 §변경 제안에 개선 제안으로 있다.
    """
    return get_current_logistics_read(None, as_of=as_of, sim_run_id=sim_run_id)


def read_active_logistics_policy() -> LogisticsPolicy:
    """물류 활성 정책을 조회 연결 하나로 읽는다 — 스냅샷 없이 정책만 필요한 조회용.

    부르는 곳: 재고 콘솔의 Runtime 없는 날(`readmodel/console.py`) · STATUS_QUERY
    `get_policy`(`readmodel/status_tools.py`).
    """
    with core_db.read_connection() as conn:
        return get_active_logistics_policy(conn)
