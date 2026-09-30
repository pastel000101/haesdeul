"""현재 시점 물류 읽기 한 벌 — 스냅샷 · 그것을 만든 정책 · 운송 계약 · fixture · 그날 일정 보기.

★ 2026-09-30 재구성 BL-015: `logistics/repository.py` 의 `LogisticsRead` 를 옮겼다. 스냅샷
  모델(`schemas/snapshot.py`)과
  일정 보기(`schemas/inbound_schedules.py`)를 함께 담아 따로 둔다 — 한 파일에 두면 두 모델 파일이
  서로를 import 한다. 채우는 쪽은 `readmodel/current.py`.
"""

from typing import NamedTuple

from app.logistics.schemas.inbound_schedules import InboundScheduleView
from app.logistics.schemas.snapshot import (
    InventoryLogisticsSnapshot,
    LogisticsPolicy,
    LogisticsRuntimeFixture,
)


class LogisticsRead(NamedTuple):
    """한 호출이 읽은 물류 Fact 한 벌 — Snapshot 과 **그것을 만든 Policy**.

    ★ 정의서 §1.2-13(한 호출 안에서 같은 값을 두 번 조회하지 않는다)의 구현 수단이다
      (#121 ⑤). 종전에는 Snapshot 조립이 Policy 를 읽어 **값만** 담고 버렸고,
      어댑터가 `source_refs`·`policy_version` 때문에 같은 테이블을 다시 읽었다.
      두 읽기가 서로 다른 active 행을 볼 수 있어 *"payload 값은 옛 정책, 표기된
      policy_version 은 새 정책"* 이 조용히 성립하는 구조였다.

    ★ 두 읽기가 여전히 다른 connection 인 것(조회 원자성)은 별개 위험이며 여기서
      해결하지 않는다 — 이 타입이 닫는 것은 **같은 값의 중복 조회**다.

    ★ **화면이 쓰는 두 칸이 뒤에 붙어 있다** (`fixture` · `inbound_schedule_views` ·
      2026-09-15). 화면 한 판은 이 읽기 한 벌에서 판매가능량(Snapshot)도, 운송 중
      Header(fixture)도, 도착 처리 대상(views)도 같이 꺼내 쓴다 — 셋을 따로 읽으면
      같은 `logistics_runtime_fixture` · `inbound_schedules` 질의가 한 요청에 두세 번
      나간다 (실측: 일정 질의 5번 · 421 ms). 어댑터 경로는 이 두 칸을 안 읽어도 된다.
    """

    snapshot: InventoryLogisticsSnapshot
    policy: LogisticsPolicy
    #: 고정 운송 계약 하나 (`logistics_contracts`). `None` 은 **계약 0건**이다 —
    #: 회사 상태이지 오류가 아니다 (`transport.RouteNotFound`).
    delivery_route: str | None = None
    #: 🔴 **계약을 읽다가 실패했다.** `None`(계약 없음)과 가르는 칸이다 —
    #: 앞엣것은 `UNRESOLVED` 로 답할 사실이고 뒤엣것은 다시 부르면 될 수 있는
    #: 실행 오류다 (`transport.AmbiguousRoute` 는 무결성 위반이라 여기 들어온다).
    delivery_route_error: bool = False
    #: 이 읽기가 본 Runtime Snapshot Header 그대로. 화면이 `in_transit_status` 를 읽는다.
    #: `None` 은 손수 만든 `LogisticsRead`(검사 대역)뿐이다 — 이 모듈이 만들면 항상 있다.
    fixture: LogisticsRuntimeFixture | None = None
    #: 그날 살아 있던 입고 일정 + 계보 (`inbound_schedules.load_schedule_views` 결과).
    #: `snapshot.in_transit` · `confirmed_inbound_schedule` 이 여기서 파생됐고, 화면의
    #: 도착 처리 대상(`receivable_from`)도 같은 views 에서 나온다.
    inbound_schedule_views: tuple[InboundScheduleView, ...] | None = None
