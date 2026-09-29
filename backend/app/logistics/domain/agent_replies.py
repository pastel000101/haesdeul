"""마스터 회신(`AgentReply`)과 실행 흔적(`ExecutionMetadata`)의 모양 — Tool 입력 계약 · DeptMeta ·
LLM 관측 · 준비 안 됨/실행 오류 회신.

★ 2026-09-30 재구성 BL-015: `logistics/adapter.py` 에서 옮겼다(내용 그대로). Tool 입력 계약이
  빠짐없는지는 이 모듈을
  import 할 때 확인한다(`assert_tool_input_contracts_complete`) — 종전 어댑터 기동 검사와 같다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from app.contracts.envelope import DEPT_CAP_CHECK_ID, AgentReply, AgentRequest, ExecutionMetadata
from app.logistics.llm.schemas import InterpretationResult

AGENT = "inventory"

# 물류 Tool — 실제로 부른 것만 남긴다.
T_RULES = "evaluate_procurement_rules"
T_CAP = "calculate_cap_by_date"
T_ARRIVAL = "calculate_expected_arrival_dates"
T_LOTS = "build_lot_constraints"
T_INVENTORY = "build_inventory_by_item"
T_SIGNALS = "evaluate_procurement_business_signals"
T_SALES_SIGNALS = "evaluate_sales_business_signals"
# STATUS_QUERY 전용 (#396) — 판정이 아니라 **측정**을 부르는 둘이다.
T_USAGE = "calculate_window_capacity_usage"
T_FRESHNESS = "measure_freshness_facts"


# --- Critic DeptMeta (#134) -------------------------------------------------

#: Critic 의 물류 밴드 검사 id — **마스터 소유 상수**다
#: (`app/master/critic_bridge.py:51 _INVENTORY_CAP_CHECK`). 마스터가 물류 PRE_PURCHASE
#: payload 의 `warehouse_free_kg`·`cap_by_date` 로 check 를 합성할 때 붙이는 이름이고,
#: **물류는 `checks[]` 를 내지 않는다** (2026-09-01 마스터 확정 — 같은 사실의 주인이
#: 둘이 되면 마스터 합성분과 갈리는 자리가 새로 생긴다).
#:
#: 🔴 **이름이 어긋나면 검사가 조용히 통과한다.** Critic 은
#: `inputs_used.get(check.check_id, ())` 로 읽고 못 찾으면 빈 튜플인데, 빈 튜플은
#: *"금지 입력이 없다"* 로 읽혀 통과다 (`critic_v0_4.py:401`). 에러가 없으니 아무도
#: 모른다.
#:
#: ★ **그래서 문자열을 베끼지 않고 참조한다** (#137). 마스터가 PR #135 로 상수를
#:   공개하며 같은 이유를 적었다 — *"부서가 문자열을 베껴 두면 마스터가 이름을 바꾸는
#:   날 그 부서의 검사가 조용히 무력화된다."* 마스터의
#:   `tests/master/test_dept_meta_check_id.py` 는 **마스터 합성 결과와 상수**가 같은지만
#:   보므로, 물류가 베낀 문자열을 들고 있으면 그 테스트는 초록불인 채 물류 검사만 죽는다.
CAP_CHECK_ID = DEPT_CAP_CHECK_ID[AGENT]


class ToolInputContractMissing(RuntimeError):
    """실행한 Tool 의 입력 계약이 없다. **조용히 0개로 보고하지 않는다.**

    빈 `inputs_used` 는 Critic 이 *"금지 입력이 없다"* 로 읽고 통과시킨다 — 모르는
    것이 통과가 되는 구조라 여기서는 크게 실패하는 편이 낫다 (재무와 같은 판단).
    """

    def __init__(self, tool: str) -> None:
        self.tool = tool
        super().__init__(f"Logistics tool has no declared input contract: {tool}")


#: Tool 하나가 스냅샷·요청에서 **실제로 읽는** 입력. `inputs_used` 의 재료다.
#:
#: ★ **선언이 아니라 관측의 재료다.** 이 표만으로 관측을 만들지 않는다 — 그날 실행에
#:   실제로 들어간 Tool 만 골라 합집합을 낸다. 안 돈 Tool 의 입력은 안 실린다.
#:
#: ★ **과다 선언이 안전한 방향이다.** 적게 적으면 Critic 이 금지 입력을 못 보고 조용히
#:   통과시키고, 많이 적으면 검사가 더 걸릴 뿐이다. 그래서 Tool 이 판정에 곁들여 읽는
#:   것(경고용 `snapshot_id`·`evidence_refs`)도 빼지 않는다.
#:
#: ★ **시나리오 계열 Tool 은 금지 이름을 그대로 적는다.** `calculate_expected_arrival_dates`
#:   는 매입 제안을 읽는다. 지금은 SCENARIO_VALIDATION 에서만 돌아 `inputs_used` 에
#:   실리지 않지만, 언젠가 경계 경로로 새면 Critic 이 `E-SCENARIO-LEAK` 으로 잡아야
#:   한다 (`FORBIDDEN_SCENARIO_INPUTS`). 정직하게 적는 것이 그때의 방어다.
TOOL_INPUTS: dict[str, tuple[str, ...]] = {
    T_RULES: (
        "logistics_snapshot.guaranteed_capacity_kg",
        "logistics_snapshot.guaranteed_capacity_by_zone_kg",
        "logistics_snapshot.daily_inbound_capacity_kg",
        "logistics_snapshot.inbound_transport_capacity_kg",
        "logistics_snapshot.inbound_lead_days",
        "logistics_snapshot.in_transit",
        "logistics_snapshot.confirmed_inbound_schedule",
        "logistics_snapshot.confirmed_outbound_schedule",
        "logistics_snapshot.on_hand_by_lot",
        "logistics_snapshot.snapshot_id",
        "logistics_snapshot.evidence_refs",
    ),
    T_CAP: (
        "logistics_snapshot.guaranteed_capacity_kg",
        "logistics_snapshot.used_capacity_kg",
        "logistics_snapshot.on_hand_by_lot",
        "logistics_snapshot.confirmed_inbound_schedule",
        "logistics_snapshot.confirmed_outbound_schedule",
    ),
    T_LOTS: ("logistics_snapshot.on_hand_by_lot",),
    T_INVENTORY: (
        "logistics_snapshot.on_hand_by_lot",
        "logistics_snapshot.confirmed_outbound_schedule",
    ),
    # PRE_SALES 전용 — `inputs_used` 에 실리지 않는다 (PRE_SALES 는 DeptMeta 를 내지
    # 않는다, `inventory_dept_meta` 참조). 그래도 적는 이유는 위 ★ 넷째 항목과 같다:
    # **관측을 내게 되는 날 이 표가 이미 맞아 있어야** 조용한 누락이 안 생긴다.
    T_SALES_SIGNALS: (
        "logistics_snapshot.on_hand_by_lot",
        "logistics_snapshot.freshness_pressure_ratio",
    ),
    # STATUS_QUERY 전용 (#396) — 조회는 DeptMeta 를 내지 않아 `inputs_used` 에 실리지
    # 않는다. 그래도 적는 이유는 위 ★ 넷째 항목과 같다 (관측을 내게 되는 날의 대비).
    T_USAGE: (
        "logistics_snapshot.guaranteed_capacity_kg",
        "logistics_snapshot.used_capacity_kg",
        "logistics_snapshot.inbound_lead_days",
        "logistics_snapshot.on_hand_by_lot",
        "logistics_snapshot.in_transit",
        "logistics_snapshot.confirmed_inbound_schedule",
        "logistics_snapshot.confirmed_outbound_schedule",
    ),
    T_FRESHNESS: (
        "logistics_snapshot.on_hand_by_lot",
        "logistics_snapshot.freshness_pressure_ratio",
    ),
    # 아래 둘은 SCENARIO_VALIDATION 전용이라 `inputs_used` 에 실리지 않는다. 계약을
    # 비워 두지 않는 이유는 위 ★ 넷째 항목이다.
    T_ARRIVAL: ("scenarios", "split_plan"),
    T_SIGNALS: (
        "scenarios",
        "logistics_snapshot.on_hand_by_lot",
        "logistics_snapshot.used_capacity_kg",
        "logistics_snapshot.guaranteed_capacity_kg",
        "logistics_snapshot.confirmed_inbound_schedule",
        "logistics_snapshot.confirmed_outbound_schedule",
        "logistics_snapshot.capacity_tight_ratio",
        "logistics_snapshot.freshness_pressure_ratio",
        "logistics_snapshot.item_storage_policies",
    ),
}

#: 어댑터가 **Tool 없이** 직접 만드는 밴드 값의 입력. `free_capacity()` 가
#: `warehouse_free_kg` 를 만드는데, 이것이 마스터 합성 check 의 `cap_total_kg` 이 된다
#: (`critic_bridge.py:350`). 재무는 cap 을 Tool 이 만들어 이 자리가 없지만 물류는
#: 어댑터가 만든다 — 여기 안 적으면 밴드 절반의 입력이 통째로 빠진다.
ADAPTER_BAND_INPUTS: tuple[str, ...] = (
    "logistics_snapshot.guaranteed_capacity_kg",
    "logistics_snapshot.used_capacity_kg",
)


def assert_tool_input_contracts_complete() -> None:
    """모든 물류 Tool 은 입력 계약을 가져야 한다.

    기동 시점에 확인한다 — Tool 을 새로 만들고 계약을 안 적으면 그 사실이 조용한
    `inputs_used` 누락이 아니라 **import 실패**로 즉시 드러난다 (재무와 같은 규율).
    """
    undeclared = sorted(
        {
            T_RULES,
            T_CAP,
            T_ARRIVAL,
            T_LOTS,
            T_INVENTORY,
            T_SIGNALS,
            T_SALES_SIGNALS,
            T_USAGE,
            T_FRESHNESS,
        }
        - set(TOOL_INPUTS)
    )
    if undeclared:
        raise ToolInputContractMissing(", ".join(undeclared))


assert_tool_input_contracts_complete()


def produced_fields(payload: Mapping[str, Any]) -> list[str]:
    """이번 회신에 **실제로 실린** 필드.

    값이 `None` 인 키는 뺀다 — 산출하지 않은 것을 산출했다고 적으면 권한 검사
    (`E-AUTHORITY`)가 엉뚱한 것을 본다. 어댑터는 원래 없는 값의 키를 아예 안 싣지만
    (§1.2-10), 기준을 명시해 둔다.
    """
    return sorted(key for key, value in payload.items() if value is not None)


LLM_TRACE_OBSERVATION = "inventory_llm_trace"
"""LLM 실행 관측의 이름 — 결정론 관측(`inventory_dept_meta`)과 **다른 축이다.**

DeptMeta 는 LLM 상태와 무관하게 같아야 하고(그것을 #399 가 잠근다), 이쪽은 LLM
상태에 따라 달라지는 것이 정상이다. 두 책임을 한 이름에 담으면 결정론 보존 검사가
LLM 변화를 잡아 매번 깨지거나, 반대로 검사를 느슨하게 만들게 된다.

★ 상수로 두는 것은 테스트가 문자열을 베끼지 않게 하려는 것이다 (`CAP_CHECK_ID` 와
  같은 규율). 이름이 바뀌는 날 `_trace_view` 의 제외 필터가 조용히 빗나가면
  결정론 비교가 LLM 관측까지 삼켜 매번 깨진다."""


def inventory_llm_trace(llm: InterpretationResult | None) -> dict[str, Any] | None:
    """실제로 일어난 Provider 호출 하나의 실행 관측 (#402). **업무 결과가 아니다.**

    `execution_meta()` 경계에서 종전에 통째로 유실되던 것을 여기로 나른다 — 어떤 Provider 를
    썼나 · 최종 실패 원인이 무엇인가 · 얼마나 걸렸나 · **토큰을 얼마나 썼나**(#406) ·
    어떤 종류의 Fact 가 나갔나.
    공통 `ExecutionMetadata` 에 필드를 넷 더 세우지 않고 기존 확장 채널
    (`observations`)을 쓴다 — 재무가 `finance_llm_provider` 로 같은 성격의 사실을
    싣는 자리와 같다. 마스터는 읽지 않고 나르고, Critic 은 `<dept>_dept_meta` 가
    아닌 관측을 조용히 건너뛴다 (`critic_bridge._dept_meta_in`).

    🔴 **`llm_attempts > 0` 일 때만 만든다.** 그래서

    ```text
    관측이 있다  ⇔  Provider 를 실제로 불렀다
    ```

    가 계약이 된다. `llm_status` 문자열 목록을 여기 복제하지 않는 이유이기도 하다 —
    상태 어휘가 늘면 그 목록만 낡는다. DISABLED · SKIPPED_TEMPLATE 은 관측을 내지
    않는다: 부르지 않은 실행에 provider 이름을 적으면 *"썼다"* 로 읽히고, 그 사실은
    이미 `llm_status` + `llm_attempts=0` 이 정확히 말한다.

    ★ **Sanitized Context 에서 `fact_id` 만 옮긴다.** `label` · `display_value` 는
      싣지 않는다 — display_value 가 곧 판정 수치의 확정 표기라("91.7% (임계 90%)"),
      그것을 실행이력에 복제하면 Provider 전송 경계에서 막은 업무 데이터가 마스터
      실행이력·화면으로 우회해 나간다. 여기서 알아야 할 것은 *"어떤 종류의 Fact 가
      나갔나"* 이지 *"그 값이 얼마였나"* 가 아니다.
    """
    if llm is None or llm.llm_attempts <= 0:
        return None
    return {
        "observation_type": LLM_TRACE_OBSERVATION,
        "provider": llm.llm_provider or "",
        # 최종 실패 원인 하나. SUCCESS(재시도 후 성공 포함)면 null 이다 — attempt 별
        # 이력을 만들지 않는다(중간 실패는 로그 몫)는 기존 의미를 그대로 나른다.
        "error_kind": llm.llm_error_kind,
        "provider_elapsed_ms": llm.llm_provider_elapsed_ms,
        # 관측된 호출들의 토큰 합 (#406). **숫자 둘만 나른다** — 원본 필드명
        # (`promptTokenCount` · `prompt_eval_count`)도, raw 응답도 오지 않는다.
        # ★ 값이 `None` 이어도 키는 **뺴지 않고 null 로 남긴다.** 관측의 key 집합이
        #   실행마다 달라지면 읽는 쪽이 "필드가 없다"와 "값이 없다"를 구별하지 못하고,
        #   `_LLM_TRACE_KEYS` 의 `==` 잠금도 성립하지 않는다.
        "observed_input_tokens": llm.llm_observed_input_tokens,
        "observed_output_tokens": llm.llm_observed_output_tokens,
        "context_fact_ids": [fact.fact_id for fact in llm.llm_context_facts],
    }


def inventory_dept_meta(
    mode: str,
    payload: Mapping[str, Any],
    tools: Sequence[str],
) -> dict[str, Any] | None:
    """이번 실행이 읽은 입력과 낸 산출을 **물류 자신이** 기계가 읽을 형태로 낸다.

    Critic 의 `E-AUTHORITY`(부서가 S3 전속 판정을 냈나)와 `E-SCENARIO-LEAK`(밴드 검사가
    매입 시나리오를 읽었나)은 이것이 없으면 아예 돌지 않는다 — **통과가 아니라 생략**
    이다. 마스터는 Tool 이름이나 payload 키를 보고 물류가 무엇을 읽었는지 알 수 없어
    추측하지 않는다.

    `PRE_PURCHASE` 만 `inputs_used` 를 낸다. 마스터가 밴드 check 를 합성하는 입력은
    `constraints` 이고 그것은 PRE_PURCHASE 회신만 모으므로
    (`master/flow.py::_collect_constraints`), SCENARIO_VALIDATION 에는 대응하는 cap
    검사 축이 없다. 없는 검사에 가짜 `inputs_used` 를 지어내지 않고 **실제 산출 필드만**
    낸다 — `E-AUTHORITY` 는 그것으로 돈다. 마스터가 두 mode 의 관측을 **합쳐서** 나르므로
    빈 `inputs_used` 가 경계 관측을 덮지 않는다 (`critic_bridge._dept_meta_in`).

    🔴 **`PRE_SALES` 는 관측 자체를 내지 않는다 — `None` 이 답이다** (#346).
      `_dept_meta_in` 을 부르는 것은 매입 Flow(`master/flow.py` → `critic_bridge`)뿐이고
      판매 Flow(`master/sales_flow.py`)에는 Critic 경로가 아예 없다. 소비자가 없는데
      관측을 내면 두 가지가 동시에 틀린다 — `CAP_CHECK_ID` 는
      `DEPT_CAP_CHECK_ID["inventory"]`, 즉 **매입 밴드 전용 이름**이라 판매 회신의
      입력이 그 이름으로 실리면 매입의 밴드 검사가 판매 입력을 읽고, 새 check_id 를
      지어내면 **마스터가 만들지 않은 계약**을 물류가 먼저 만드는 것이 된다.
      마스터가 판매용 check 계약을 내는 날 여기에 분기를 추가한다.
    """
    if mode == "SCENARIO_VALIDATION":
        return {
            "observation_type": "inventory_dept_meta",
            "inputs_used": {},
            "produced_fields": produced_fields(payload),
        }
    if mode != "PRE_PURCHASE":
        return None
    inputs: list[str] = list(ADAPTER_BAND_INPUTS)
    for tool in tools:
        if tool not in TOOL_INPUTS:
            raise ToolInputContractMissing(tool)
        for name in TOOL_INPUTS[tool]:
            if name not in inputs:
                inputs.append(name)
    return {
        "observation_type": "inventory_dept_meta",
        "inputs_used": {CAP_CHECK_ID: inputs},
        "produced_fields": produced_fields(payload),
    }


def not_implemented_reply(request: AgentRequest) -> tuple[AgentReply, ExecutionMetadata]:
    run_id = logistics_run_id(request)
    reply = AgentReply(
        request_id=request.context.request_id,
        as_of=request.context.as_of,
        agent=AGENT,
        mode=request.mode,
        run_id=run_id,
        runtime_status="RUNTIME_NOT_READY",
        business_status="skipped",
        # RUNTIME_NOT_READY 에는 이름이 반드시 있어야 한다 (M-1 §5.1) — 없으면 마스터가
        # 사용자에게 무엇을 달라고 할지 모른다. 여기서 없는 것은 값이 아니라 번역이다.
        missing_data=(f"{request.mode}_translation",),
        missing_capability=(f"{request.mode} 번역",),
        reasoning=f"{request.mode} 는 물류 어댑터에 아직 없다.",
    )
    return reply, execution_meta(request, run_id, [])


def no_run_axis_reply(request: AgentRequest) -> tuple[AgentReply, ExecutionMetadata]:
    """봉투에 실행 축이 안 실려 왔다 — **`ERROR` 가 아니다** (#345).

    🔴 **`ERROR` 로 접으면 두 실패가 한 이름이 된다.** `snapshot_error_reply` 가 말하는 것은
       *"DB 가 깨졌으니 다시 불러 봐라"* 인데(`worth_retry`), 값을 못 받은 것은 다시
       불러도 같다. 재시도하면 호출 예산만 탄다 (M-1 §5.1 · 정의서 §1.2-12).
       `failed_operation` 을 실을 수도 없다 — **조회를 시도조차 안 했다.**

    ★ **예외로 올리지 않는다.** 올리면 `MasterRunner._invoke` 가 잡아 `error_reply` 로
      바꾸므로 결국 `ERROR` 가 되고, 예외 원문이 `reasoning` 에 실려
      `E-REASONING-NUMERIC` 함정까지 같이 온다 — 부서 회신은 값으로 답한다.

    ★ **이름은 `sim_run_id` 하나다.** `logistics_runtime_fixture` 를 같이 싣지 않는다 —
      fixture 는 **없는 것이 아니라 어느 것인지 못 고르는 것**이고, 그 이름은 진짜
      부재(`status_query_reply` · `pre_purchase_reply`)가 이미 쓰고 있어 섞으면 마스터가 두 상황을
      못 가린다.

    ★ `tools` 가 비는 것은 사실이다 — **Tool 을 하나도 안 돌렸다.**
    """
    run_id = logistics_run_id(request)
    return not_ready_reply(
        request,
        run_id,
        [],
        missing=("sim_run_id",),
        reason="어느 실행의 물류 장부인지 확인할 수 없다",
    )


def logistics_run_id(request: AgentRequest) -> str:
    return f"LOG-{request.context.request_id}-{request.call_seq}"


def execution_meta(
    request: AgentRequest,
    run_id: str,
    tools: Sequence[str],
    reply: AgentReply | None = None,
    *,
    llm: InterpretationResult | None = None,
) -> ExecutionMetadata:
    """실행 흔적. **Business Reply 와 섞지 않는다.**

    `reply` 를 받으면 `runtime_status == "READY"` 일 때만 DeptMeta 관측을 붙인다.
    ★ **못 낸 회신에 관측을 달지 않는다** — *"안 돌았는데 무엇을 읽었다"* 가 된다.

    `llm` 은 해석 서비스가 낸 결과다 (#385). 받으면 그 상태를 그대로 적고, 안 받으면
    **그 mode 에 LLM 경로가 없다**는 뜻이라 `DISABLED` 다 — 지금은 `SCENARIO_VALIDATION`
    만 준다. 어댑터가 상태 어휘를 지어내지 않는다.

    `ExecutionMetadata` 는 LLM 칸이 넷뿐이라(status · model · attempts · fallback)
    provider · error_kind · Provider latency · Context fact 는 여기서 유실됐다.
    공통 봉투를 넓히는 대신 `inventory_llm_trace` 관측 하나로 나른다 (#402).
    ★ `elapsed_ms` 는 **건드리지 않는다** — 그 칸은 Agent 전체 실행시간이고(재무가
      채운다) LLM 시간을 넣으면 한 컬럼에 비교 불가능한 두 값이 섞인다.
    """
    observations: list[dict[str, Any]] = []
    if reply is not None and reply.runtime_status == "READY":
        dept_meta = inventory_dept_meta(request.mode, reply.payload, tools)
        if dept_meta is not None:
            observations.append(dept_meta)
    # ★ 맨 뒤에 붙인다 — 앞자리는 DeptMeta 가 쓰던 자리이고 읽는 쪽이 그것을 전제로
    #   붙어 있다 (재무가 harness trace 를 뒤에 붙인 것과 같은 판단).
    # ★ `reply` 의 READY 를 다시 묻지 않는다. Gate 가 `runtime_ready=True` 일 때만
    #   호출을 허용하므로 attempts>0 이면 이미 READY 다 — 같은 조건을 두 번 적으면
    #   한쪽이 낡는 날 둘이 어긋난다.
    llm_trace = inventory_llm_trace(llm)
    if llm_trace is not None:
        observations.append(llm_trace)
    return ExecutionMetadata(
        run_id=run_id,
        request_id=request.context.request_id,
        agent=AGENT,
        used_tools=tuple(tools),
        tool_order=tuple(range(1, len(tools) + 1)),
        llm_status=llm.llm_status if llm is not None else "DISABLED",
        llm_model=(llm.llm_model or "") if llm is not None else "",
        llm_attempts=llm.llm_attempts if llm is not None else 0,
        llm_fallback_used=llm.llm_fallback_used if llm is not None else False,
        observations=tuple(json.dumps(o, default=str, sort_keys=True) for o in observations),
    )


def snapshot_error_reply(
    request: AgentRequest,
    run_id: str,
    tools: Sequence[str],
    *,
    llm: InterpretationResult | None = None,
) -> tuple[AgentReply, ExecutionMetadata]:
    """스냅샷 조회의 **실행 실패** — `RUNTIME_NOT_READY` 가 아니다 (#121 4단계).

    다시 부르면 성공할 수 있는 쪽이라 재시도 가치가 있다 (M-1 §5.1 — DB 장애·크래시).
    ★ 예외 원문을 reasoning 에 싣지 않는다 — 숫자가 섞이면 E-REASONING-NUMERIC 에
      걸린다(재무 400 실측 함정). 원문은 `_load_snapshot` 이 로그로 남긴다.
    """
    reply = AgentReply(
        request_id=request.context.request_id,
        as_of=request.context.as_of,
        agent=AGENT,
        mode=request.mode,
        run_id=run_id,
        runtime_status="ERROR",
        business_status="skipped",
        payload={"failed_operation": "load_logistics_snapshot"},
        reasoning=(
            "물류 스냅샷 조회가 실행 오류로 실패했다 — "
            "데이터 부재가 아니라 재시도 가치가 있는 실패다."
        ),
    )
    return reply, execution_meta(request, run_id, tools, reply, llm=llm)


def not_ready_reply(
    request: AgentRequest,
    run_id: str,
    tools: Sequence[str],
    *,
    missing: tuple[str, ...],
    reason: str,
    llm: InterpretationResult | None = None,
) -> tuple[AgentReply, ExecutionMetadata]:
    """입력이 없어서 못 낸 답. **`ERROR` 가 아니다** — 다시 불러도 같다 (M-1 §5.1)."""
    reply = AgentReply(
        request_id=request.context.request_id,
        as_of=request.context.as_of,
        agent=AGENT,
        mode=request.mode,
        run_id=run_id,
        runtime_status="RUNTIME_NOT_READY",
        business_status="skipped",
        missing_data=missing,
        reasoning=reason,
    )
    return reply, execution_meta(request, run_id, tools, reply, llm=llm)
