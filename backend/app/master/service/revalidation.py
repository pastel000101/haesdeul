"""
revalidation.py — 최종 승인 시점 재검증 (설계 2026-09-07 · M-4)

승인 클릭 하나가 부서를 다시 부른다.

```text
읽고 → 검사하고 → 재검증하고 → 적재한다
```

왜 자기 모듈인가: 결정 적재(`service/decision.py`)는 "사람이 무엇을 눌렀나" 를 적는
자리이고 여기는 "그 사이 바뀌었는가" 를 묻는 자리다. 한 파일에 두면 결정 적재가 부서
호출·개장 관문·호출 예산까지 들고 있게 된다.

S-1(기여 호출 재사용)을 쓰지 않는다. 판매 Flow 의 `_judge` 는 라우팅이 ②와 같으면 그
회신을 다시 쓰는데(같은 `as_of`·같은 요청 안이므로), 여기서는 그것이 틀린 규칙이다.
목적이 "그 사이 바뀌었는가" 라 재사용하면 바뀐 것을 못 보고 통과시킨다 — 재검증을 하는
이유 자체가 없어진다(설계 §1 · `sales_flow._judge` 의 경고 그대로). 그래서 이 모듈은 원
실행의 회신을 결과로 쓰지 않는다. 원 실행에서 읽는 것은 "무엇을 검증받아야 하는가" 와
"그때 조건이 무엇이었나" 둘뿐이고, 판정은 전부 이번 호출에서 나온다.

`as_of` 는 이 파일이 만들지 않는다(`#452`). 부르는 쪽이 정해 넘기고 여기는 받은 것을
흘린다. 지금은 `service/decision.py` 의 `_revalidate_scenario_of` 가 원 실행의 날
(`as_of_of`)을 넘긴다. 그날로 개장 Gate 를 또 지난다: 그날이 안 열렸으면 재검증을 못
한다.

재검증 결과를 장부 Write 로 잇는 것은 부르는 쪽이다(`service/decision.py`). 예를 들어
판매 승인은 `PASSED` 일 때만 판매 확정(`service/sales_approval.py`)으로 흐른다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from app.contracts.envelope import (
    AgentName,
    ExecutionContext,
    Mode,
    route_capability,
    wire_adjustment,
)
from app.master.domain.decision import PROCUREMENT_CYCLE, SALES_CYCLE
from app.master.domain.flow import ADVISORS
from app.master.domain.revalidation import (
    PROCUREMENT_REVALIDATION_MODE,
    REQUIRED_CAPABILITIES,
    capabilities_for,
    make_revalidation_request_id,
    procurement_validation_payload,
    revalidation_verdict,
    verdict_of,
)
from app.master.registry import wiring
from app.master.registry.ports import AgentNotRegistered
from app.master.schemas.revalidation import Revalidation
from app.master.service.budget import BudgetExhausted, CallBudget
from app.master.service.day_gate import check_day_gate
from app.master.service.persistence import record_revalidation
from app.master.service.runner import MasterRunner

#: 라우팅은 있지만 재검증에서는 부르지 않는 capability.
#:
#: `ADDITIONAL_SUPPLY_CONTEXT` 가 그 자리다(라우팅: `contracts/envelope.py` 의
#: `CAPABILITY_ROUTING` → 매입 `SUPPLY_CAPACITY_QUERY`). 판매 Flow 는 부족량과 매입용
#: 경계를 골라 담아 보내는데(`sales_flow._supply_capacity_input`), 여기는 후보를 그대로
#: 보낸다(`revalidate_scenario` 의 `runner.call`). 그대로 보내면 매입이 부족량도 경계도
#: 못 읽어 `basis=unknown` 으로 답한다.
#:
#: 그 답은 화면에서 "못 물어봤다" 로 보이는데 실제로는 "잘못 물어봤다" 다. 호출 예산을
#: 한 번 쓰고 어휘가 거짓말을 한다 — 둘 다 손해다. 그래서 부르지 않고 `unroutable` 로
#: 남긴다. "이 검증은 안 왔다" 는 사실 그대로다.
#:
#: 제약: 이것을 지우려면 부족량과 경계를 골라 담는 자리를 여기에도 먼저 만들어야 한다.
#: 그 전에 지우면 위 문단의 거짓말이 그대로 돌아온다.
_NOT_REVALIDATED: frozenset[str] = frozenset({"ADDITIONAL_SUPPLY_CONTEXT"})


REVALIDATION_BUDGET = 4
"""재검증 한 번의 호출 예산.

```text
필수  SELLABLE_SUPPLY_CONTEXT · FINANCIAL_VALIDATION      2
조건부 후보가 요구한 나머지 (어휘가 넷이라 최대 2 가 더 붙는다)  2
──────────────────────────────────────────────────────────
                                                          4
```

`SALES_BUDGET`(25) 을 그대로 쓰지 않는다. 저쪽은 "후보 3 · 되먹임 2회" 를 전제로 센
값이고, 재검증은 후보 하나에 되먹임이 없다. 남의 예산을 빌려 쓰면 여기서 몇 번을
부르는지가 아무 데도 안 적히고, 그 사이 판매 예산이 바뀌면 재검증의 상한이 이유 없이
따라 움직인다.

소진은 `ERROR` 다. "다 봤는데 안 된다" 가 아니라 "다 못 봤다" 이므로 `FAILED` 와 갈라
둔다(판매가 `SL5` 를 `SL3` 으로 접지 않는 것과 같은 판단).
"""


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------


def revalidate_scenario(
    *,
    scenario: Mapping[str, Any],
    original_conditions: frozenset[str],
    decision_seq: int,
    policy_version: str,
    as_of: date,
    sim_run_id: str,
    item: str | None = None,
) -> Revalidation:
    """선택된 1안만 받은 그날로 다시 검증한다.

    ```text
    ① 개장 Gate      오늘이 안 열렸으면 못 돈다        → ERROR
    ② 어댑터 점검     필수 capability 를 부를 수 없다  → ERROR
    ③ 호출           필수 + 후보가 요구했던 나머지
    ④ 매핑           PASSED · CONDITIONAL · FAILED
    ⑤ 이력 적재       master_agent_runs (cycle=SALES)
    ```

    판매 안 전용이다. 매입 안은 `revalidate_procurement_scenario` 로 간다. 매입 안을
    여기 넣으면 재무 `SALES_VALIDATION` 이 판매 사실을 못 찾아
    `INPUT_INCOMPLETE`(READY/skipped) 로 답하고, 그 skipped 가 늘 `FAILED` 로 접힌다.

    전체 후보를 다시 돌리지 않는다. 사용자는 하나를 골랐고, 나머지는 이미 그 시점의
    판단으로 화면에 나갔다(설계 §1).

    개장 Gate 를 지난다. `ExecutionContext` 를 만드는 자리는 전부 그렇다 — 안 열린 날
    판단이 서면 막힌 것이 아니라 안 막힌 것이라 아무 오류도 나지 않는다.
    `tests/master/test_entrypoint_day_gate.py` 의 스캐너가 이 모듈까지 훑는다.

    `as_of` 를 필수 인자로 받고 시계를 읽지 않는다(`#452`). 날짜는 부르는 쪽이 정해
    넘기고(지금은 `service/decision.py` 가 원 실행의 날을 넘긴다), 이 깊은 자리에서는
    아무도 날짜를 지어내지 못하고 받은 것을 그대로 쓴다. 여기서 시계를 읽으면
    백테스트가 무효가 된다 — `2026-03-10` 을 걷는 실행이 승인 경로를 타는 순간
    재검증만 오늘로 답하고, 곡선에 벽시계가 섞인다.
    `tests/core/test_clock_is_the_only_wall_clock.py` 가 이 파일이 `clock` 을 임포트하지
    않는지 지킨다. 기본값을 두지 않는다. 기본값은 곧 업무 규칙이 되고, 안 넘긴 자리가
    조용히 오늘로 답한다. 안 넘기면 실패해야 한다.

    재검증할 날이 제안한 날과 같아도 재검증은 돈다 — 그날 안에 입고 · 수금 · 출고가
    지나갔을 수 있고, 재검증이 재는 것은 "그 사이" 이지 "며칠 지났는가" 가 아니다.

    `sim_run_id` 도 필수 인자로 받는다. 번인 상수를 박으면 어느 실행을 재검증하든 늘
    번인 장부를 읽는다. 실측(실행 `SIM-SALESCHAIN-20260911`): 재무가 채권·현금을 번인
    장부에서 읽어, 판매를 한 번도 안 한 실행의 재검증이 `SALES_CREDIT_LIMIT_EXCEEDED` ·
    `BASE_MINIMUM_CASH_VIOLATED` 로 떨어졌다. 승인 일곱 건이 전부 `FAILED` 이고 `sales`
    는 0행인데, 번인 채권 합과 재검증이 본 AR 이 소수점까지 같았다
    (15,752,100.13535). `as_of` 와 같은 규율로 기본값을 두지 않는다.

    :param as_of: 이 재검증이 서는 날. 부르는 쪽이 정한다(지금은 원 실행의 날).
    :param sim_run_id: 어느 실행의 장부를 읽는가. 원 실행 행이 정본이고 여기서 짓지
        않는다(`domain/decision.py` 의 `run_sim_run_id_of`).
    :param original_conditions: 원 실행에서 그 후보에 붙어 있던 조건 표지 집합
        (`conditions_of` 가 만든다). 이번 결과가 이보다 늘면 `CONDITIONAL` 이다.
    """
    request_id = make_revalidation_request_id(sim_run_id, as_of, decision_seq)
    context = ExecutionContext(
        request_id=request_id,
        as_of=as_of,
        trigger="USER_REQUEST",
        policy_version=policy_version,
        # 어느 실행의 장부인가는 마스터가 정한다(물류 `#325`). 다만 상수가
        # 아니라 원 실행 행에서 온다 — 부르는 쪽이 읽어 넘긴 값을 그대로 흘린다.
        sim_run_id=sim_run_id,
    )

    # ① 첫 관문은 개장이다. 막히면 재검증이 실패한 것이 아니라 돌리지 못한
    #    것이라 `FAILED` 와 갈라 `ERROR` 로 적는다.
    #
    #    관문에도 같은 축을 넘긴다. 봉투와 관문이 다른 실행을 보면 "안 열린 날에
    #    판단이 서는" 자리가 한 함수 안에서 생긴다.
    day_gate = check_day_gate(as_of, sim_run_id=sim_run_id)
    if day_gate.gate == "BLOCKED":
        return Revalidation(
            outcome="ERROR",
            reason=f"재검증할 날({as_of.isoformat()})이 안 열려 재검증을 못 돌렸다: "
            f"{day_gate.reason or day_gate.result}",
        )

    capabilities = capabilities_for(scenario)
    routes = {capability: route_capability(capability) for capability in capabilities}

    # ② 필수 capability 를 부를 대상이 등록조차 안 돼 있으면 못 돈 것이다.
    missing = _missing_for(routes)
    if missing:
        return Revalidation(
            outcome="ERROR",
            reason=f"필수 검증을 부를 어댑터가 없어 재검증을 못 돌렸다: {', '.join(missing)}",
        )

    runner = MasterRunner(context, wiring.registry(), CallBudget(limit=REVALIDATION_BUDGET))
    validations: dict[str, Mapping[str, Any]] = {}
    adjustments: list[Mapping[str, Any]] = []
    unroutable: list[str] = []

    try:
        for capability, route in routes.items():
            if route is None or capability in _NOT_REVALIDATED:
                # 조용히 건너뛰지 않는다. 건너뛰면 "검증됐다" 로 읽힌다.
                unroutable.append(capability)
                continue
            agent, mode = route
            # 후보를 그대로 보낸다 — `sales_flow._judge` 와 같은 규칙이다.
            # 마스터가 골라 담으면 판매가 필드를 늘린 날 조용히 빠진다.
            reply = runner.call(agent, mode, dict(scenario))
            validations[capability] = verdict_of(reply)
            adjustments.extend(wire_adjustment(a) for a in reply.suggested_adjustments)
    except BudgetExhausted as exc:
        return _recorded(
            context,
            Revalidation(
                outcome="ERROR",
                request_id=request_id,
                reason=f"호출 예산 소진으로 재검증이 끝나지 않았다: {exc}",
                validations=validations,
                unroutable=tuple(unroutable),
            ),
            runner=runner,
            item=item,
            cycle=SALES_CYCLE,
        )
    except AgentNotRegistered as exc:
        # ②에서 필수는 걸렀지만 조건부 대상이 빠질 수 있다. 그때도 못 돈 것이다.
        return _recorded(
            context,
            Revalidation(
                outcome="ERROR",
                request_id=request_id,
                reason=f"에이전트 미등록으로 재검증을 끝내지 못했다: {exc}",
                validations=validations,
                unroutable=tuple(unroutable),
            ),
            runner=runner,
            item=item,
            cycle=SALES_CYCLE,
        )

    outcome, reason, conditions = revalidation_verdict(
        validations, tuple(unroutable), adjustments, original_conditions
    )
    return _recorded(
        context,
        Revalidation(
            outcome=outcome,
            request_id=request_id,
            reason=reason,
            conditions=conditions,
            validations=validations,
            unroutable=tuple(unroutable),
        ),
        runner=runner,
        item=item,
        cycle=SALES_CYCLE,
    )


def revalidate_procurement_scenario(
    *,
    scenario: Mapping[str, Any],
    proposal: Mapping[str, Any],
    original_conditions: frozenset[str],
    decision_seq: int,
    policy_version: str,
    as_of: date,
    sim_run_id: str,
    item: str | None = None,
) -> Revalidation:
    """매입 안 1안만 그 실행의 날로 다시 검증한다(매입 승인 · 실매입 기록 공용).

    ```text
    ① 개장 Gate      안 열렸으면 못 돈다                  → ERROR
    ② 조언자 점검     재무 · 물류 중 등록 안 된 쪽이 있다    → ERROR
    ③ 호출           조언자마다 SCENARIO_VALIDATION 한 번
    ④ 매핑           PASSED · CONDITIONAL · FAILED (`revalidation_verdict` 공용)
    ⑤ 이력 적재       master_agent_runs (cycle=PROCUREMENT)
    ```

    판매 capability 로 묻지 않는다(2026-09-16 실측). 판매 경로로 물으면
    `FINANCIAL_VALIDATION` 이 재무 `SALES_VALIDATION` 으로 가는데, 매입 안에는 판매 사실이
    없어 재무가 `INPUT_INCOMPLETE` → `READY/skipped` 로 답한다. 그 skipped 가 허용목록
    밖이라 매입 재검증이 늘 `FAILED` 가 되고 이력 행도 `cycle=SALES` 로 남는다.

    묻는 모양은 매입 Flow 가 정한 그대로다(`service/flow.py` 의 `_validate`). 제안
    최상위(응답 `judgment` · `meta.as_of` · `meta.item` 이 여기 있다)에 `scenarios` 를
    고른 안 하나로 얹는다. 안의 칸을 골라 담지 않는다 — 매입이 칸을 늘린 날 조용히
    빠진다.

    부를 조언자의 주인은 `domain/flow.py` 의 `ADVISORS` 다. 여기서 이름을 다시 적지
    않는다.

    skipped 는 통과가 아니다(`revalidation_verdict`). 규칙 판정은 LLM 이 꺼져도 돈다 —
    원 실행이 `E1_APPROVED` 로 올라온 것 자체가 두 조언자가 LLM 없이 판정을 냈다는
    뜻이다. 그러니 재검증에서 skipped 가 오면 "못 봤다" 이지 "원래 그렇다" 가 아니다.

    :param proposal: 원 실행 응답의 `judgment` (매입 제안에서 `scenarios` 를 뺀 최상위).
    """
    request_id = make_revalidation_request_id(sim_run_id, as_of, decision_seq)
    context = ExecutionContext(
        request_id=request_id,
        as_of=as_of,
        trigger="USER_REQUEST",
        policy_version=policy_version,
        sim_run_id=sim_run_id,
    )

    day_gate = check_day_gate(as_of, sim_run_id=sim_run_id)
    if day_gate.gate == "BLOCKED":
        return Revalidation(
            outcome="ERROR",
            reason=f"재검증할 날({as_of.isoformat()})이 안 열려 재검증을 못 돌렸다: "
            f"{day_gate.reason or day_gate.result}",
        )

    missing = wiring.missing(ADVISORS)
    if missing:
        return Revalidation(
            outcome="ERROR",
            reason=f"매입 안을 검증할 조언자가 등록되지 않아 재검증을 못 돌렸다: "
            f"{', '.join(missing)}",
        )

    runner = MasterRunner(context, wiring.registry(), CallBudget(limit=REVALIDATION_BUDGET))
    payload = procurement_validation_payload(proposal, scenario)
    validations: dict[str, Mapping[str, Any]] = {}
    adjustments: list[Mapping[str, Any]] = []

    try:
        for agent in ADVISORS:
            reply = runner.call(agent, PROCUREMENT_REVALIDATION_MODE, payload)
            validations[agent] = verdict_of(reply)
            adjustments.extend(wire_adjustment(a) for a in reply.suggested_adjustments)
    except (BudgetExhausted, AgentNotRegistered) as exc:
        return _recorded(
            context,
            Revalidation(
                outcome="ERROR",
                request_id=request_id,
                reason=f"매입 안 재검증이 끝나지 않았다: {exc}",
                validations=validations,
            ),
            runner=runner,
            item=item,
            cycle=PROCUREMENT_CYCLE,
        )

    outcome, reason, conditions = revalidation_verdict(
        validations, (), adjustments, original_conditions
    )
    return _recorded(
        context,
        Revalidation(
            outcome=outcome,
            request_id=request_id,
            reason=reason,
            conditions=conditions,
            validations=validations,
        ),
        runner=runner,
        item=item,
        cycle=PROCUREMENT_CYCLE,
    )


def _recorded(
    context: ExecutionContext,
    result: Revalidation,
    *,
    runner: MasterRunner,
    item: str | None,
    cycle: str,
) -> Revalidation:
    """재검증 실행 1건을 이력에 남긴다 (설계 §4).

    실패 처리: 적재 실패가 재검증을 죽이지 않는다(`service/persistence.py` 의 `record` 와
    같은 태도). 그때 `revalidation_request_id` 는 `master_agent_runs` 에 없는 키를
    가리키는데, 그것이 곧 "적재가 실패했다" 이고 설계 §4 가 숨기지 말라고 적은 자리다.
    """
    record_revalidation(
        context,
        cycle=cycle,
        outcome=result.outcome,
        reason=result.reason,
        # 사람 말(`reason`)과 표지 원문(`conditions`)을 같은 행에 나란히 남긴다.
        # `reason` 만 남기면 조정 표지가 이 표에서 사라진다 — 근거는
        # `Revalidation.conditions` 에 적어 두었다.
        conditions=result.conditions,
        validations=result.validations,
        unroutable=result.unroutable,
        plan=runner.plan,
        item=item,
    )
    return result


def _missing_for(routes: Mapping[str, tuple[AgentName, Mode] | None]) -> tuple[str, ...]:
    """필수 검증을 부를 수 없는 이유들. 필수만 본다.

    ```text
    라우팅이 없다   capability → (agent, mode) 표에 값이 None 이다
    등록이 없다     그 에이전트 어댑터가 프로세스에 없다
    ```

    조건부는 여기서 막지 않는다. 조건부는 못 부르면 "안 왔다"(`unroutable`)로 둔다
    (설계 §5). `ADDITIONAL_SUPPLY_CONTEXT` 는 라우팅이 있지만(`contracts/envelope.py` 의
    `CAPABILITY_ROUTING` → 매입 `SUPPLY_CAPACITY_QUERY`) 조건부이고, 재검증에서는 아예
    부르지 않는다(`_NOT_REVALIDATED`).
    """
    out: list[str] = []
    for capability in REQUIRED_CAPABILITIES:
        route = routes.get(capability)
        if route is None:
            out.append(f"{capability}(부를 대상 없음)")
            continue
        agent, _mode = route
        if wiring.missing((agent,)):
            out.append(f"{capability}({agent} 미등록)")
    return tuple(out)
