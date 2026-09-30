"""어댑터 경로가 Controller 없이 확정하는 회신 — 준비 안 됨 · 잘못된 요청 · 미지원, 그리고 그
실행을 이력에 남기기.

★ 이력 저장 실패는 **업무 회신을 바꾸지 않는다** — 관측(`finance_run_persistence_failed`)만
  남긴다. Controller 경로(`service/agent.py`)는 저장 실패를 ERROR 로 바꾼다. 두 처리를 합치지
  않는다(설계서 §진입점 4 규칙 4).

★ 2026-09-29 재구성 BL-014: `finance/adapter.py` 에서 옮겼다(몸통 그대로, 밑줄만 뗐다).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from uuid import uuid4

from pydantic import ValidationError

from app.contracts.envelope import AgentReply, AgentRequest, ExecutionMetadata
from app.finance.domain import messages
from app.finance.domain.status_facts import T_POSITION
from app.finance.llm.client import finance_llm_enabled
from app.finance.service.run_history import save_finance_execution


def invalid_scenario_input_reply(
    request: AgentRequest, run_id: str, exc: ValidationError
) -> tuple[AgentReply, ExecutionMetadata]:
    reply = AgentReply(
        request_id=request.context.request_id, as_of=request.context.as_of, agent="finance",
        mode=request.mode, run_id=run_id, runtime_status="ERROR", business_status="skipped",
        payload={
            "validation_errors": [
                ".".join(str(part) for part in item["loc"]) or item["type"]
                for item in exc.errors()
            ]
        },
        reasoning=messages.INVALID_REQUEST,
    )
    return recorded_reply(request, reply, adapter_metadata(request, run_id, []))


def invalid_scenario_as_of_reply(
    request: AgentRequest, run_id: str, proposal_as_of: date
) -> tuple[AgentReply, ExecutionMetadata]:
    reply = AgentReply(
        request_id=request.context.request_id, as_of=request.context.as_of, agent="finance",
        mode=request.mode, run_id=run_id, runtime_status="ERROR", business_status="skipped",
        payload={"validation_errors": ["proposal.meta.as_of"]},
        reasoning=messages.INVALID_REQUEST_AS_OF,
    )
    return recorded_reply(request, reply, adapter_metadata(request, run_id, []))


def not_implemented_reply(request: AgentRequest) -> tuple[AgentReply, ExecutionMetadata]:
    """★ 시나리오 판정은 **매입 시나리오 필드명이 확정돼야** 붙는다.

    재무의 판정 엔진(`FinanceAgentController`)은 이미 있다. 막힌 것은
    **번역**이다 — 마스터가 받는 `scenarios[]` 의 키 이름을 아직 받지 못했다.

    추측해서 매핑하면 **숫자는 나오는데 틀린 값을 판정하게 된다.** 그건 에러도 안 나고
    검증도 통과한다 — §1.2-10 이 막으려는 바로 그 종류다.

    `skipped` 로 돌려주므로 Flow 는 끝까지 돌고, **못 본 사실이 verdicts 와 실행 계획에
    남는다** (§3.7.6).
    """
    run_id = new_run_id(request)
    # 🔴 mode 마다 못 하는 **이유가 다르다.** 예전에는 어느 mode 로 와도
    #    "매입 시나리오 필드명이 확정되지 않아" 를 냈는데, 그건 `SCENARIO_VALIDATION`
    #    의 사유일 뿐이다. 다른 mode 에 그대로 붙이면 **거짓 사유가 이력에 남고**,
    #    마스터가 사용자에게 요청할 대상을 잘못 알려 준다.
    if request.mode == "SCENARIO_VALIDATION":
        missing = ("purchase_scenario_schema",)
        reason = messages.SCENARIO_SCHEMA_MISSING
    else:
        missing = (f"{request.mode}_translation",)
        reason = messages.MODE_NOT_SUPPORTED
    reply = AgentReply(
        request_id=request.context.request_id,
        as_of=request.context.as_of,
        agent="finance",
        mode=request.mode,
        run_id=run_id,
        runtime_status="RUNTIME_NOT_READY",
        business_status="skipped",
        missing_data=missing,
        missing_capability=(f"{request.mode} 번역",),
        reasoning=reason,
    )
    return reply, adapter_metadata(request, run_id, [])


def new_run_id(request: AgentRequest) -> str:
    """어댑터가 직접 답할 때의 실행 식별자.

    ★ **UUID 다.** 예전에는 `FIN-{request_id}-{call_seq}` 였는데, Finance 실행이력
      (`finance_agent_runs_v22.run_id`)이 `UUID PRIMARY KEY` 라서 어댑터가 직접 낸
      회신은 **저장할 방법 자체가 없었다** — Controller 에 닿지 못한 실행은 이력에
      구멍으로 남았다.

    ★ **실행 식별자이지 요청 식별자가 아니다.** 한때 uuid5 로 (request_id, call_seq,
      mode) 를 넣어 결정론을 지키려 했는데, 그러면 **같은 요청을 두 번 실행하면 같은
      run_id 가 나온다.** 이력은 append-only 이고 run_id 가 기본키라, 두 번째 실행은
      저장되지 못하고 통째로 사라진다 — 하필 재실행은 남아야 할 실행이다.
      `FinanceAgentController.run` 도 같은 이유로 `uuid4` 를 쓴다.

    ★ 요청을 되짚는 축은 따로 있다. 같은 행의 `request_id` · `call_seq` · `mode` 로
      찾으면 되고, `idx_finance_runs_v22_request` 가 그 조회를 받친다.

    `del request` — 인자는 계약을 위해 남긴다. 이 함수는 요청 내용에 의존하지 않는다.
    """
    del request
    return str(uuid4())


def adapter_metadata(request: AgentRequest, run_id: str, tools: Sequence[str]) -> ExecutionMetadata:
    """어댑터가 LLM 없이 답한 실행의 메타데이터.

    🔴 예전에는 `llm_status="DISABLED"` 로 고정이었다. 그러면 LLM 을 **켜 둔** 배포에서
       조회·미준비 회신이 전부 *"LLM 을 안 켰다"* 로 남는다. 실제로는 **켜 뒀는데 이
       경로가 부를 일이 없었다** 이고, 그것이 `SKIPPED_TEMPLATE` 다 (envelope §LLMStatus).
    """
    return ExecutionMetadata(
        run_id=run_id,
        request_id=request.context.request_id,
        agent="finance",
        used_tools=tuple(tools),
        tool_order=tuple(range(1, len(tools) + 1)),
        llm_status="SKIPPED_TEMPLATE" if finance_llm_enabled() else "DISABLED",
    )


def not_ready_reply(
    request: AgentRequest,
    run_id: str,
    tools: Sequence[str],
    *,
    missing: tuple[str, ...],
    reason: str,
) -> tuple[AgentReply, ExecutionMetadata]:
    """입력이 없어서 못 낸 답. **`ERROR` 가 아니다** — 다시 불러도 같다 (M-1 §5.1)."""
    reply = AgentReply(
        request_id=request.context.request_id,
        as_of=request.context.as_of,
        agent="finance",
        mode=request.mode,
        run_id=run_id,
        runtime_status="RUNTIME_NOT_READY",
        business_status="skipped",
        missing_data=missing,
        reasoning=reason,
    )
    return recorded_reply(request, reply, adapter_metadata(request, run_id, tools))


#: 봉투에 실행 축이 없어 못 낸 답의 `missing_data`.
#:
#: 🔴 **`finance_state` · `finance_policy` 를 적으면 안 된다.** 그 이름들은 *"그 자료를
#:    찾아 오라"* 는 뜻이라, 읽는 사람은 멀쩡히 있는 재무 자료를 찾으러 간다. 없는
#:    것은 자료가 아니라 **봉투의 `sim_run_id`** 다 — 고칠 자리가 완전히 다르다.
#:
#: ★ 그래서 이름을 그대로 적는다. `missing_data` 는 사람이 읽는 문장이 아니라 기계와
#:   개발자가 읽는 **주소**이고, 이 경우 주소는 봉투의 그 필드다.
_MISSING_EXECUTION_AXIS: tuple[str, ...] = ("sim_run_id",)


def axis_not_ready_reply(
    request: AgentRequest, run_id: str
) -> tuple[AgentReply, ExecutionMetadata]:
    """봉투에 실행 축이 없다. **번인으로 대신하지 않는다** (재무 기준 ④⑥).

    ★ 빈 축일 때 아무 실행이나 집으면 오류는 안 나고 **숫자만 남의 것**이 된다.
      그래서 답을 내지 않고, 무엇이 없었는지를 이름으로 남긴다.

    ★ `RUNTIME_NOT_READY` / `skipped` 다 — `ERROR` 가 아니다. 같은 봉투로 다시 불러도
      같으므로 재시도 가치가 없고, 축을 채워 다시 보내야 하는 일이다.
    """
    return not_ready_reply(
        request,
        run_id,
        [T_POSITION],
        missing=_MISSING_EXECUTION_AXIS,
        reason=messages.EXECUTION_AXIS_MISSING,
    )


#: 실행이력에 남기는 mode. **닫힌 허용목록이다** — 모르는 mode 는 여기 없다.
#:
#: ★ `SALES_VALIDATION` 은 `finance_agent_runs_v22.mode` CHECK 에
#:   SALES_VALIDATION 이 들어간 뒤에 열었다 (신규 DDL + 기존 DB 마이그레이션).
#:   제약보다 먼저 열면 판정은 되는데 **저장이 전부 실패한다.**
_CONTROLLER_MODES = ("PRE_PURCHASE", "SCENARIO_VALIDATION", "SALES_VALIDATION")


def recorded_reply(
    request: AgentRequest, reply: AgentReply, metadata: ExecutionMetadata
) -> tuple[AgentReply, ExecutionMetadata]:
    """Controller 에 닿지 못한 실행도 이력에 남긴다.

    ★ **이중 저장이 아니다.** 이 경로들은 전부 `FinanceAgentController.run` 을 부르기
      *전에* 회신을 확정하고 돌아간다 — Controller 가 저장하는 실행과 겹치지 않는다.

    ★ `STATUS_QUERY` 는 여기 오지 않는다. `finance_agent_runs_v22.mode` 의 CHECK 가
      두 core mode 만 허용하기 때문이다 (아래 §UNRESOLVED). 조회 이력을 남기려면
      스키마를 고쳐야 하는데, 그것은 Finance 코드 밖이다.

    ★ 저장 실패가 **업무 답을 바꾸지 않는다.** "재무 상태를 못 읽었다"는 사실은 저장
      여부와 무관하게 참이고 재시도 가치도 같다. 대신 실패 자체는 감추지 않고
      observations 에 남겨 마스터가 실행계획에서 볼 수 있게 한다.
    """
    if request.mode not in _CONTROLLER_MODES:
        return reply, metadata
    try:
        save_finance_execution(request=request, reply=reply, metadata=metadata)
    except Exception as error:  # noqa: BLE001 - 이력 실패로 업무 회신을 뒤집지 않는다.
        failure = json.dumps(
            {
                "observation_type": "finance_run_persistence_failed",
                "reason": type(error).__name__,
            },
            sort_keys=True,
        )
        return reply, replace(metadata, observations=(*metadata.observations, failure))
    return reply, metadata
