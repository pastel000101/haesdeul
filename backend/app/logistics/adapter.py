"""물류가 마스터와 주고받는 번역 — 봉투 분기와 마스터 등록소 표면.

```text
logistics_port                봉투 → mode 분기 → service/<mode>.py 의 *_reply
LogisticsTransitionAdapter    승인 전이 등록소 표면 → service/transition.persist_inventory
LogisticsCancellationAdapter  승인 취소 등록소 표면 → service/cancellation.withdraw_inventory
LogisticsDayOpening           하루 넘김 등록소 표면 → service/day_open
LogisticsInboundExecution     도착 처리 등록소 표면 → service/inbound_execution.receive_inbound
```

이 파일에는 업무가 없다. 모드별 업무 조립(스냅샷 읽기 · 규칙 · Tool · 해석 · 회신)은
`service/`(`agent_status` · `pre_purchase` · `pre_sales` · `scenario_validation`), 근거 ·
회신 · 실행 흔적 조립은 `domain/agent_evidence.py` · `domain/agent_replies.py`, 판매 요청
해석은 `domain/pre_sales.py` 가 맡는다. 도착 처리의 순서 · 분기 · 실패 처리 계약은
`service/inbound_execution.py` 모듈 설명에 있다.

등록소 표면은 커밋도 롤백도 하지 않고 커넥션을 새로 열지 않는다. 커넥션은 마스터가
주고 커밋은 마스터가 한 번 한다. 등록소 표면 객체는 마스터 등록소 조립
(`master/registry/bootstrap.py`)이 만들어 건다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from app.contracts.commitment import ApprovedCommitment
from app.contracts.envelope import AgentReply, AgentRequest, ExecutionMetadata
from app.contracts.parts import InboundPartOut
from app.logistics.domain.agent_replies import no_run_axis_reply, not_implemented_reply
from app.logistics.domain.inbound_execution import InspectionProvider
from app.logistics.domain.transition import build_next_inventory, inbound_ids_of
from app.logistics.schemas.transition import InventoryTransition
from app.logistics.schemas.vocabulary import USAGE_SCOPE
from app.logistics.service.agent_status import status_query_reply
from app.logistics.service.cancellation import withdraw_inventory
from app.logistics.service.day_open import is_logistics_day_open, open_logistics_day
from app.logistics.service.inbound_execution import receive_inbound
from app.logistics.service.pre_purchase import pre_purchase_reply
from app.logistics.service.pre_sales import pre_sales_reply
from app.logistics.service.scenario_validation import scenario_validation_reply
from app.logistics.service.transition import persist_inventory

#: 실행 축을 실제로 읽는 mode — 이 넷만 `sim_run_id` 문을 지난다 (#345 · #346).
#
# 미구현 mode 를 이 문 앞에 세우지 않는다. 실행 축이 비었다고 "번역이 없다" 를
# `sim_run_id` 누락으로 바꾸면 없는 구현을 값 탓으로 돌리는 거짓이 되고, 마스터는 그 말을
# 듣고 사용자에게 줄 수 없는 것을 달라고 한다 (M-1 §5.1).
#
# 불변식: 이 집합 ⊆ `logistics_port` 가 실제 handler 로 보내는 mode. 구현보다 문이 먼저
# 서면 그 순간 위 거짓이 되살아난다. `tests/logistics/test_logistics_adapter.py` 가 구조로
# 검사한다.
#
# 아래 네 handler 가 전부 같은 `load_read` 를 지나므로 문은 하나면 된다.
_RUNTIME_AXIS_MODES: frozenset[str] = frozenset(
    {"PRE_PURCHASE", "PRE_SALES", "SCENARIO_VALIDATION", "STATUS_QUERY"}
)


def logistics_port(request: AgentRequest) -> tuple[AgentReply, ExecutionMetadata]:
    """마스터가 부르는 유일한 접점."""
    # 어느 실행의 장부인지 모르면 읽지 않는다 (#345). 물류는 이 값을 지어내지 않는다 —
    # 마스터가 소유한 값이고, 없으면 없다고 답하는 것이 답이다.
    if request.mode in _RUNTIME_AXIS_MODES and not request.context.sim_run_id.strip():
        return no_run_axis_reply(request)
    if request.mode == "PRE_PURCHASE":
        return pre_purchase_reply(request)
    if request.mode == "PRE_SALES":
        return pre_sales_reply(request)
    if request.mode == "SCENARIO_VALIDATION":
        return scenario_validation_reply(request)
    if request.mode == "STATUS_QUERY":
        return status_query_reply(request)
    return not_implemented_reply(request)


class LogisticsTransitionAdapter:
    """마스터 전이 Protocol(`app.master.registry.transition.LogisticsTransition`)의 물류 입구.

    여기에는 업무가 없다. 재무 `FinanceTransitionAdapter` 와 같은 결이다 — 얇게 두어야
    계약이 바뀔 때 고칠 자리가 한 곳으로 남는다.

    commit 도 rollback 도 하지 않고 커넥션을 새로 열지도 않는다. `persist_inventory` 가
    이미 그 규율을 지키고, 어댑터는 인자만 넘긴다.

    :param sim_run_id: 이 반영이 앉을 시뮬레이션 실행. 마스터가 소유한 값이고
        `app/master/domain/sim_run.py` 의 `BURN_IN_SIM_RUN_ID` 가 그 주인이다.
    """

    def __init__(self, *, sim_run_id: str) -> None:
        self._sim_run_id = sim_run_id

    def build(
        self,
        commitment: ApprovedCommitment,
        *,
        target_state_date: date,
        purchase_ids: Mapping[int, str] | None = None,
    ) -> Sequence[InventoryTransition]:
        """묶음 하나를 담은 시퀀스를 낸다.

        Protocol 이 `Sequence[object]` 라 하나만 담아도 어기지 않는다. 승인 하나가 바꾸는
        fixture 행이 하나뿐이라 묶음도 하나다.

        `purchase_ids` 는 회차(`leg.seq`) → 매입 참조다. 마스터 전이 규약
        (`app/master/registry/transition.py` 의 `LogisticsTransition`)은 이 인자를 필수로
        받고, 마스터 `service/transition.py` 의 `apply_approval` 이 `purchase_id_for` 로 만든
        매핑을 넘긴다. 이 메서드의 기본값 `None` 은 매핑 없이 부르는 호출(검사 등)을 위한
        것이고, 그때 행의 `purchase_id` 는 비어 도착일에 `blocked` 로 드러난다.

        인자는 그대로 넘긴다. 회차마다 어느 값을 집을지도, 없을 때 어떻게 할지도
        `build_next_inventory` 가 정한다.
        """
        return (
            InventoryTransition(
                target_state_date=target_state_date,
                # 마스터 승인에서 왔다는 것을 행에 남긴다. 접두사를 붙이는 이유는 같은
                # 칸에 다른 출처(번인 적재 등)가 앉을 수 있어서다.
                source_ref=f"MASTER-APPROVAL:{commitment.approval_id}",
                items=tuple(build_next_inventory(commitment, purchase_ids=purchase_ids)),
            ),
        )

    def persist(self, conn: Any, rows: Sequence[InventoryTransition]) -> None:
        """묶음마다 `persist_inventory` 를 부른다. 인자를 넘기는 것이 전부다.

        `as_of` 에 `target_state_date` 를 넘긴다. 승인일(`commitment.as_of`)이 아니다 —
        승인이 바꾸는 것은 다음 날 상태이고, 재무가 같은 날짜로 `finance_states` 를 세운다.
        여기서 하루 앞 행에 쓰면 두 장부가 다른 날에 앉아 그날의 사실이 갈린다.
        """
        for bundle in rows:
            persist_inventory(
                conn,
                sim_run_id=self._sim_run_id,
                as_of=bundle.target_state_date,
                rows=bundle.items,
                source_ref=bundle.source_ref,
            )


class LogisticsCancellationAdapter:
    """마스터 `ApprovalCancellation` 을 물류 함수에 잇는 얇은 연결.

    `withdraw_inventory` 를 감싸기만 한다. 걷는 규칙은 그 함수가 그대로 한다.

    `sim_run_id` 는 생성 인자다. "어느 실행의 장부인가" 는 실행 정체성이라 물류가 아니라
    마스터가 정한다 — `LogisticsTransitionAdapter` 와 같은 판단이고, 등록소 조립
    (`app/master/registry/bootstrap.py`)에서 눈에 보이게 주입한다.
    """

    def __init__(self, *, sim_run_id: str) -> None:
        self._sim_run_id = sim_run_id

    def cancel(
        self,
        conn: Any,
        *,
        commitment: ApprovedCommitment,
        cancelled_on: date,
        target_state_date: date,
        purchase_ids: Mapping[int, str],
        financing_mode: str,
    ) -> None:
        """`target_state_date` 행에서 걷는다. 승인이 쓴 행이 아니다.

        승인은 `commitment.as_of + 1` 행에 적었고 취소는 `cancelled_on + 1` 행에서
        걷는다. 둘이 다르면 그 사이 날들은 그대로 둔다 — 그때는 실제로 오는 중이었다.

        `purchase_ids` 와 `financing_mode` 는 쓰지 않는다. 물류가 걷는 열쇠는
        `inbound_id` 이고 물류 축은 `(sim_run_id, as_of, usage_scope)` 라 재무 축이 걸리지
        않는다. 그래도 받는 이유는 두 파트가 같은 인자를 받아야 호출부가 하나로 서기
        때문이다 — `purchase_ids` 를 한 파트에만 주면 다른 파트의 처리가 막힌다(`#313`).
        """
        withdraw_inventory(
            conn,
            sim_run_id=self._sim_run_id,
            as_of=target_state_date,
            inbound_ids=inbound_ids_of(commitment),
            source_ref=f"MASTER-CANCEL:{commitment.approval_id}@{cancelled_on.isoformat()}",
        )


class LogisticsDayOpening:
    """`app.master.registry.day_open.DayOpening` 의 물류 구현.

    commit 도 rollback 도 하지 않고 커넥션을 새로 열지도 않는다. 커넥션은 마스터가 주고
    커밋은 두 파트가 모두 끝난 뒤 마스터가 한 번 한다. 여기서 커밋하면 물류만 먼저
    확정되고 뒤이어 다른 파트가 실패했을 때 한쪽 날만 열린 장부가 남는다
    (`persist_inventory` 와 같은 규율이다).

    생성 인자 `sim_run_id` 는 `LogisticsTransitionAdapter` · `LogisticsCancellationAdapter`
    와 같은 자리다. "어느 실행의 장부인가" 는 물류 사실이 아니라 실행 정체성이고, 그 값의
    주인은 마스터다. 모듈 상수로 박으면 실행이 둘이 되는 날 물류 코드를 고쳐야 하므로 등록소
    조립(`app/master/registry/bootstrap.py`)에서 눈에 보이게 받는다.

    INSERT 의 `sim_run_id` 칸은 전날 행에서 물려받는다(`base.sim_run_id`). 그러나 어느
    전날 행을 물려받을지는 물려받는 값이 아니라 알고 있어야 하는 값이라 생성 인자로 받는다.

    `None` 을 받을 수 있다 — 다만 조용히 넘어가지 않는다.

       ```text
       받았다     세 조회 모두 (sim_run_id, as_of, usage_scope) 로 좁힌다
       못 받았다  실행이 하나뿐이면 그 실행으로 답한다 · 둘 이상 보이면 LogisticsRunAmbiguous
       ```

    `None` 은 "모든 실행 허용" 이 아니다. 실행이 둘 보이는 순간 답하지 않고 멈춘다.
    기본값을 둔 이유는 등록소 조립 파일이 물류 소유가 아니라서다 — 필수 인자로 만들면 값을
    주입하지 않는 호출이 생성 시점에 실패한다.

    :param sim_run_id: 이 하루 넘김이 앉을 시뮬레이션 실행. 마스터가 소유한 값이다.
    """

    def __init__(self, *, sim_run_id: str | None = None) -> None:
        # 빈 문자열은 `None` 과 다른 실수다 — 주입은 했는데 값이 안 실린 것이라 조용히
        # 미주입으로 접으면 그 주입 실수가 안 보인다.
        if sim_run_id is not None and not sim_run_id.strip():
            raise ValueError(
                f"하루 넘김에 쓸 수 없는 sim_run_id 다: {sim_run_id!r}."
                " 주입하지 않을 것이면 None 이어야 한다 — 빈 문자열은 조회를 0건으로"
                " 만들고 그 0건은 '그날 행이 없다' 로 읽힌다."
            )
        self._sim_run_id = sim_run_id

    def is_open(self, conn: Any, *, as_of: date) -> bool:
        """그날 내 실행의 물류 fixture 행이 이미 있는가 — `is_logistics_day_open`."""
        return is_logistics_day_open(conn, as_of=as_of, sim_run_id=self._sim_run_id)

    def open_day(self, conn: Any, *, as_of: date, carry_from: date) -> None:
        """`carry_from` 날 행을 물려받아 `as_of` 날 행을 만든다 — `open_logistics_day`."""
        open_logistics_day(conn, as_of=as_of, carry_from=carry_from, sim_run_id=self._sim_run_id)


class LogisticsInboundExecution:
    """`app.master.registry.inbound.InboundExecution` 의 물류 구현.

    `sim_run_id` 는 생성 인자다. "어느 실행의 장부인가" 는 물류 사실이 아니라 실행
    정체성이고, Protocol 의 `receive(conn, *, as_of)` 는 그 값을 나르지 않는다 —
    `LogisticsTransitionAdapter` · `LogisticsCancellationAdapter` · `LogisticsDayOpening`
    과 같은 자리다. 모듈 상수로 박으면 실행이 둘이 되는 날 물류 코드를 고쳐야 한다.

    `None` 을 받지 않는다. `LogisticsDayOpening` 은 조회 경로라 `None` 을 받고 둘 이상
    보이면 멈추지만, 이쪽은 쓰기 경로다. 실행을 모르는 채로 Receipt · Lot · 원장을 만들면
    남의 장부에 적을 수 있다.

    `inspection_provider` 도 필수다. 기본값을 두는 순간 그 기본값이 검수 정책이 된다
    (`InspectionProvider` 참조).

    :param sim_run_id: 이 도착 처리가 앉을 시뮬레이션 실행. 마스터가 소유한 값이고
        `app/master/domain/sim_run.py` 의 `BURN_IN_SIM_RUN_ID` 가 그 주인이다.
    :param inspection_provider: 검수 사실의 권위 출처.
    :param usage_scope: 조회·갱신 대상 범위. 기본값이 곧 현재 계약이다.
    """

    def __init__(
        self,
        *,
        sim_run_id: str,
        inspection_provider: InspectionProvider,
        usage_scope: str = USAGE_SCOPE,
    ) -> None:
        # 빈 문자열은 주입은 했는데 값이 안 실린 것이다. 조용히 넘기면 조회가 0건이 되고
        # 그 0건은 "그날 행이 없다" 로 읽힌다 (`LogisticsDayOpening` 과 같은 규율).
        if not sim_run_id or not sim_run_id.strip():
            raise ValueError(
                f"도착 처리에 쓸 수 없는 sim_run_id 다: {sim_run_id!r}."
                " 어느 실행의 장부인지 없이 Receipt · Lot · 원장을 만들 수 없다."
            )
        if not usage_scope or not usage_scope.strip():
            raise ValueError(f"도착 처리에 쓸 수 없는 usage_scope 다: {usage_scope!r}.")
        # 주입 오류는 생성 시점에 드러나야 한다. `None` 을 그대로 들고 있으면 객체는 멀쩡히
        # 서고, 실제로 도착할 물건이 생긴 날 `provide` 에서 AttributeError 로 늦게
        # 실패한다 — 그때는 마스터가 그 예외를 `FAILED` 로 바꿔 그날 입고를 통째로
        # 롤백하므로, 주입 실수가 운영 장애의 모습으로 나타난다.
        #
        # Protocol 준수 여부는 검사하지 않는다. `isinstance` 로 `provide` 서명까지 보려 들면
        # 대역·부분구현이 정당한 자리에서 막힌다 — 여기서 막는 것은 "주입을 안 했다"
        # 하나뿐이다.
        if inspection_provider is None:
            raise ValueError(
                "inspection_provider 가 없다. 검수 사실의 주인 없이 도착 처리를 세울 수 없다 —"
                " 물류가 판정을 지어내지 않기 때문이다."
                " 검수 원천이 아직 없으면 항상 None 을 내는 provider 를 배선하면 된다:"
                " 그러면 그날 도착분이 INSPECTION_FACT_UNAVAILABLE 로 보인다."
            )
        self._sim_run_id = sim_run_id
        self._provider = inspection_provider
        self._usage_scope = usage_scope

    def receive(self, conn: Any, *, as_of: date) -> InboundPartOut:
        """그날 도착 처리 — `service/inbound_execution.receive_inbound` 가 순서를 갖는다."""
        return receive_inbound(
            conn,
            as_of=as_of,
            sim_run_id=self._sim_run_id,
            inspection_provider=self._provider,
            usage_scope=self._usage_scope,
        )
