"""마스터 포트 — ``AgentRequest`` → 그래프 → ``(AgentReply, ExecutionMetadata)``.

그래프를 바꾸지 않는다. payload를 State로 펴서 ``build_graph()``를 부르고, 나온 제안을
봉투에 담는 것이 전부다. 노드·스키마·불변조건은 그대로이고, 회귀 테스트 전량이 어댑터를
거치지 않는 경로로 계속 돈다 (IO명세 §2-B).

계약 근거: 정의서 v2.3 §3.2.2·§3.2.5 · M-1 §5~§8 · `매입Agent_필요데이터_260827.md`.

시점은 요청이 준다 (규칙 1). ``context.as_of``만 보고 벽시계를 읽지 않으므로 과거
날짜로도 그대로 돈다 — 백테스트가 성립하는 근거다.

역할: 어댑터는 번역만 한다 — mode 분기, 봉투 회신 · 실행 메타데이터 · LLM 호출 기록.
수신 payload 검사는 `domain/payload.py`, payload → State · 그래프 실행은
`service/scenarios.py`, 공급 가능량의 시세 읽기 · 계산은 `service/supply_capacity.py`,
근거 · 설명문은 `domain/evidence.py` · `domain/reasoning.py` · `domain/supply_capacity.py` 다.
"""

from collections.abc import Mapping
from datetime import date
from typing import Any

from app.contracts.core import Evidence
from app.contracts.envelope import (
    AgentReply,
    AgentRequest,
    ExecutionMetadata,
    LLMCallMetadata,
    LLMStatus,
    summarize_llm_calls,
)
from app.purchase_agent import AGENT_VERSION, mocks
from app.purchase_agent.config import load_constraints
from app.purchase_agent.domain.evidence import build_evidences
from app.purchase_agent.domain.payload import not_ready_reason, validate_payload
from app.purchase_agent.domain.quotes import observed_at, quote_block_reason
from app.purchase_agent.domain.reasoning import build_reasoning
from app.purchase_agent.domain.supply_capacity import (
    supply_capacity_evidences,
    supply_capacity_reasoning,
)
from app.purchase_agent.llm.runtime import MIX_ROLE, RoleSpec, get_llm_settings
from app.purchase_agent.llm.split_allocation import ROLE as SPLIT_ROLE
from app.purchase_agent.readmodel.quotes import QuoteSource
from app.purchase_agent.service.scenarios import generate_scenarios
from app.purchase_agent.service.supply_capacity import read_supply_capacity
from app.purchase_agent.service.tracing import ToolRecorder

AGENT_NAME = "purchase"
#: ⑤ 등급 조합 판단자의 역할 이름 — 봉투 ``llm_calls[].role`` 에 그대로 실린다.
#: 문자열을 두 곳에서 짓지 않는다. 역할이 늘면 여기 옆에 한 줄씩 는다.
SOURCING_SELECTION = "sourcing_selection"
#: ④ 회차 배분 판단자의 역할 이름.
SPLIT_ALLOCATION_SELECTION = "split_allocation_selection"
#: ⑧ 근거 자기 검토의 역할 이름.
RATIONALE_SELF_REVIEW = "rationale_self_review"

#: 이 어댑터가 실제로 처리하는 mode. ``_status_query`` 가 답하는 목록이자 문 앞
#: 검사(``purchase_port``)의 기준이다 — 두 곳에 따로 적지 않는다.
#:
#: 목록이 하나여야 하는 이유: 답하는 목록만 있고 문 앞 검사가 없으면 모르는 mode 가
#: ``_generate_scenarios`` 로 떨어져 오류 없이 안이 만들어진다. 봉투
#: (``contracts/envelope.py`` 의 ``_AGENT_MODES``)도 앞에서 막아 정상 경로에서는 안
#: 일어나지만, 막는 쪽이 우리 밖에만 있으면 그 목록이 넓어지는 날을 우리가 못 본다.
#:
#: 이 목록은 마스터에게 보내는 신호이기도 하다. ``SUPPLY_CAPACITY_QUERY`` 는
#: ``envelope.CAPABILITY_ROUTING`` 의 ``ADDITIONAL_SUPPLY_CONTEXT`` 가 이 mode 로 보낸다.
#: 제약: mode 를 이 목록에 넣는 것은 구현과 같은 커밋에서만 한다. 목록만 먼저 열면
#: 마스터가 라우팅을 채우고, 봉투와 문 앞이 같이 열린 채 구현이 없다.
SUPPORTED_MODES: tuple[str, ...] = (
    "GENERATE_SCENARIOS",
    "STATUS_QUERY",
    "SUPPLY_CAPACITY_QUERY",
)


class UnsupportedMode(RuntimeError):
    """매입이 받지 않는 mode 로 불렸다.

    조용히 «안 만들었다» 로 답하지 않는다. 그 답은 "오늘은 낼 안이 없다" 로 읽히는데,
    실제로 일어난 일은 "배선이 우리가 안 만든 길을 열었다" 이다. 완전히 다른 사실이고,
    다음에 할 일도 다르다 (``MockNotAllowed`` 와 같은 이유).

    봉투가 앞에서 막으므로 정상 경로에서는 안 난다. 이 예외가 실제로 나면 라우팅과 이
    목록이 어긋났다는 뜻이다.
    """

#: 표기와 무관하게 근거를 요구할 판정 필드 (M-1 §7.2 · 전달_2차 §1).
#: 봉투의 라벨 휴리스틱은 대문자만 보므로 재무의 ``MEDIUM``은 걸리지만 매입의
#: ``stable``·``["quantity","timing"]``은 빠진다. 선언하면 표기와 무관하게 걸린다.
#: payload에 없는 이름을 적으면 ``E-JUDGMENT-UNKNOWN``이다 — 오타를 조용히 넘기면
#: 그 검사가 통째로 빈다.
JUDGMENT_FIELDS: tuple[str, ...] = ("situation", "allowed_axes")

def _run_id(request: AgentRequest) -> str:
    """``request_id``에서 결정적으로 만든다.

    난수·벽시계를 쓰지 않는다 (규칙 1) — 같은 요청을 다시 돌렸을 때 같은 ``run_id``가
    나와야 실행 이력이 대조된다. ``call_seq``를 넣는 이유는 재호출(최대 2회)이 서로 다른
    실행이기 때문이다.
    """
    return f"PUR-RUN-{request.context.request_id}-{request.call_seq}"


def _observed_at(
    quotes: list[dict[str, Any]],
    item: str,
    as_of: date,
    constraints: Mapping[str, Any],
) -> date | None:
    """봉투에 실을 관측 기준시점 (`#393` · `#626`).

    봉투가 이 칸을 "그 값이 세상에 언제 드러났나" 로 규정한다 — ``created_at`` 이
    아니고 ``as_of`` 도 아니다 (``contracts/envelope.py`` ``AgentReply.observed_at``).

    모수는 지금 시세 하나다. 봉투는 "계산에 쓴 입력이 여럿이면 그중 가장 늦은 것" 이라고
      정하는데, 우리 입력 여섯 중 우리가 직접 관측하는 것은 시세 하나다 (``bootstrap`` 이
      ``auction_quote_source`` 를 주입 · `#70`). 나머지 다섯은 봉투로 오고, 그 다섯에 실린
      ``observed_at`` 은 아직 0건이다.

      그 다섯에 칸이 서면 여기가 ``max(...)`` 가 되는 자리다 — `#393` 이 그 칸을
        기다리는 이슈다. 지금 ``max()`` 를 쓰지 않는다: 넣을 값이 없어서 원소 하나인
        ``max()`` 가 되고, 그러면 «다섯을 본다» 를 구현한 것처럼 보인다.

      그때도 ``max()`` 하나로는 모자란다 — 접는 순서가 셋이다 (마스터 정정)::

            ① 알려진 입력 중 하나라도 ``> as_of``  →  max(알려진 것)
                                                     미래 날짜가 그대로 실린다
            ② 미래가 없고 하나라도 ``None``         →  ``None``        안 쟀다
            ③ 전부 알려졌으면                       →  ``max(전부)``

        ①이 ②보다 앞이라는 것이 이 규칙의 핵심이다. 미지를 먼저 보면 이미 확인된 미래가
          미지 뒤에 숨는다 — 한 입력이 ``as_of`` 보다 뒤이고 다른 하나가 ``None`` 이면 접은
          값이 ``None`` 이 되어 게이트가 「안 쟀다」로 읽는다. 이 칸은 안전을 재는 칸이
          아니라 위험을 드러내는 칸이라(마스터 통보), 확실한 결함이 모르는 것 뒤에 숨으면
          안 된다. 둘이 섞이면 확실한 쪽이 이긴다.

      이 판정은 「부서가 값을 만드는 자리」, 즉 여기서 한다. 봉투로 나가는 것은
        ``observed_at`` 한 칸이라 접고 나면 입력들의 시점이 남지 않고, 마스터는 접힌
        결과만 받는다. 접기가 정보를 버리면 검사가 되살릴 수 없다. 타입도 게이트도 같다 —
        ``date | None`` 이고 게이트는 ``observed_at > as_of`` 하나다.

    막힌 시세의 날짜는 싣지 않는다 (규칙 3). ``observed_at`` 는 ``max(dates)`` 라
      관측일이 여러 날 섞여도 조용히 값을 낸다. 그런데 그런 날 우리는 그 시세로 판단하지
      않는다 — ③이 ``no_quote_plan`` 으로 0안을 낸다. 안 쓴 값의 관측일을 실으면 "우리가
      이 시점 기준으로 판단했다" 가 거짓이 된다. 「모른다」를 날짜로 메우는 것이다.

      주의: ``domain/allocate_sourcing.py`` 의 ``observed_at(quotes) or state["date"]`` 를
        베끼지 않는다. 그쪽은 사람이 읽는 사유 문장의 표시용 폴백이고, 이 칸은 계보다 —
        봉투가 ``as_of`` 로 메우는 것을 이름 걸고 금지한다.

    경계가 계약보다 하루 엄격하다 — 그대로 둔다. 계약은 ``observed_at <= as_of`` 를
      허용하는데 우리 ``provenance_problem`` 은 ``observed >= as_of`` 를 막는다. 우리는
      아침에 판정하고 경매는 저녁에 끝나므로 ``== as_of`` 인 값은 그 시각에 존재하지
      않았다. 남의 계약에 맞추려고 이 검사를 느슨하게 하지 않는다. 그래서 여기서 나가는
      값은 항상 ``< as_of`` 이고, 마스터가 ``observed_at > as_of`` 게이트를 걸어도 우리는
      한 건도 안 걸린다.

    mock 은 전부 ``None`` 이다 — mock 시세에 관측일 표기가 없고 ``observed_at`` 가 그것을
      "표기가 없으면 None" 으로 규정한다. 값이 나는 것은 실 DB 직독뿐이라 회귀 경로에서는
      이 칸이 비어 있다.

    ``constraints`` 를 인자로 받는다 — ③·⑤와 같은 판정을 봐야 한다
      (``quote_block_reason`` docstring 의 "각자 판단하면 한쪽만 바뀐다").
    """
    if quote_block_reason(quotes, item, as_of.isoformat(), constraints):
        return None
    text = observed_at(quotes)
    return None if text is None else date.fromisoformat(text)


def _reply(
    request: AgentRequest,
    *,
    runtime_status: str,
    business_status: str,
    payload: Mapping[str, Any] | None = None,
    evidences: tuple[Evidence, ...] = (),
    reasoning: str = "",
    missing_data: tuple[str, ...] = (),
    judgment_fields: tuple[str, ...] = (),
    observed_at: date | None = None,
) -> AgentReply:
    """봉투 4종(E-BIND)을 한 곳에서 채운다.

    ``request_id``·``as_of``·``agent``·``mode``를 호출부마다 적으면 한 경로만 어긋나도
    검증이 잡는데 원인은 흩어진다. 왕복 일치를 이 함수가 유일하게 책임진다.

    ``suggested_adjustments``는 넘기지 않는다 — 매입은 축 조정을 제안할 권한이 없고
    (제안자 ≠ 조언자), 하나라도 담으면 봉투 생성 시점에 ``ContractViolation``이다.

    ``observed_at`` 기본값이 ``None`` 이고 그것이 「안 쟀다」다 (`#393`). 시세를 읽는
      경로만 값을 넘긴다 — 안 읽는 경로(``STATUS_QUERY`` · 조기반환 둘)는 아무것도
      관측하지 않았으므로 여기서 ``request.context.as_of`` 로 메우면 안 잰 호출이 잰
      호출로 세어진다. 봉투가 그 메움을 금지한다.
    """
    return AgentReply(
        request_id=request.context.request_id,
        as_of=request.context.as_of,
        agent=AGENT_NAME,
        mode=request.mode,
        run_id=_run_id(request),
        runtime_status=runtime_status,  # type: ignore[arg-type]
        business_status=business_status,  # type: ignore[arg-type]
        payload=dict(payload or {}),
        evidences=evidences,
        reasoning=reasoning,
        missing_data=missing_data,
        judgment_fields=judgment_fields,
        observed_at=observed_at,
    )


def _metadata(
    request: AgentRequest,
    recorder: ToolRecorder | None,
    *,
    tools: tuple[str, ...] = (),
    state: Mapping[str, Any] | None = None,
) -> ExecutionMetadata:
    """``run_id``는 회신과 같아야 한다 (E-BIND-RUN-ID).

    LLM 실행 상태를 여기 담는다 — ``provenance``가 아니라 ``ExecutionMetadata``다
    (전달_2차 §2 · 회신 §5-4). "모델·fallback 상태"는 업무 결과가 아니라 실행 흔적이라
    Business Reply와 섞지 않는다는 M-1 §6 원칙이다.

    담지 않으면 risks에는 "판단자 응답 실패"가 남는데 메타데이터는 ``DISABLED``·
    fallback ``false``로 나가 두 값이 서로를 부정한다 (Codex 교차검증 P1).
    """
    used = recorder.used_tools if recorder is not None else tools
    calls = _llm_calls(state)
    status, model, attempts, fallback = summarize_llm_calls(calls)
    return ExecutionMetadata(
        run_id=_run_id(request),
        request_id=request.context.request_id,
        agent=AGENT_NAME,
        used_tools=used,
        tool_order=tuple(range(1, len(used) + 1)),
        llm_status=status,
        llm_model=model,
        llm_attempts=attempts,
        llm_fallback_used=fallback,
        llm_calls=calls,
    )


def _llm_calls(state: Mapping[str, Any] | None) -> tuple[LLMCallMetadata, ...]:
    """이 실행에서 역할별로 무엇이 있었나. 요약 칸 넷은 이것을 접은 값이다.

    역할은 셋이다 — ⑤ 등급 조합, ④ 회차 배분, ⑧ 근거 검토. 요약은 한 호출을 대표로
      세우지 않고 목록 전체를 정해진 순서로 접는다 (``summarize_llm_calls`` 참조).

    ``llm_attempts`` 는 실제 시도 수를 싣는다. 시도 수를 나르지 않으면 LLM 이 두 번
      시도한 날에도 실행 흔적이 「안 불렀다」로 보인다.

    ⑤를 부를 자리까지 못 간 실행도 한 줄을 남긴다. 안 남기면 목록이 비어
      「설정이 꺼졌다」와 구분되지 않는다 — ``_uncalled_status`` 가 가르는 자리다.
    """
    return (
        *_sourcing_call(state),
        *_split_allocation_call(state),
        *_self_review_calls(state),
    )


def _self_review_calls(
    state: Mapping[str, Any] | None,
) -> tuple[LLMCallMetadata, ...]:
    """⑧ 근거 검토. 안마다 한 줄이라 ``target`` 에 라벨이 실린다.

    게이트가 안 고른 안도 남긴다. 지우면 "봤는데 깨끗했다" 와 구분되지 않는다 —
    검토율이 거짓이 되는 자리다.
    """
    # 판을 여기서 다시 안 붙인다. ⑧ 은 안마다 한 줄을 만들면서 그때 적는다 — 두 곳에서
    #   붙이면 한쪽만 고치는 날이 온다 (``service/nodes/review_rationale.py`` 의 ``_기록``).
    기록 = ((state or {}).get("review_calls")) or ()
    return tuple(기록)


def _판(role: "RoleSpec", attempts: int) -> dict[str, str]:
    """부른 호출에만 지시문·응답 계약의 판을 적는다.

    안 부른 호출(꺼짐·게이트·상한)에서는 빈 문자열이고, 그 빈칸이 곧 «그 판이
    없었다» 는 뜻이다. 안 불렀는데 판을 적으면 "이 판으로 물어봤다" 로 읽힌다 —
    ``LLMCallMetadata`` 가 그 등식을 계약으로 잠근다.
    """
    if attempts <= 0:
        return {}
    return {
        "prompt_version": role.prompt_version,
        "schema_version": role.schema_version,
    }


def _split_allocation_call(
    state: Mapping[str, Any] | None,
) -> tuple[LLMCallMetadata, ...]:
    """④ 배분 판단 한 줄. 안 전체에 걸리는 호출이라 ``target`` 이 ``None`` 이다.

    ④ 가 분할에 진입조차 안 한 날은 줄을 안 남긴다 — 그날은 배분이라는 판단 자체가
    없었고, 「꺼졌다」도 「건너뛰었다」도 아니다. 없는 판단에 상태를 붙이면 «매일 뭔가를
    건너뛴다» 로 읽힌다.
    """
    결정 = ((state or {}).get("split_plan") or [{}])[0].get("decision") or {}
    판단 = 결정.get("allocation_judgment")
    if 판단 is None:
        return ()
    return (
        LLMCallMetadata(
            role=SPLIT_ALLOCATION_SELECTION,
            status=판단.llm_status,
            attempts=판단.llm_attempts,
            fallback_used=판단.llm_fallback_used,
            provider=판단.llm_provider or None,
            model=판단.llm_model or None,
            **_판(SPLIT_ROLE, 판단.llm_attempts),
            skip_reason=(
                _split_skip_reason(결정) if 판단.llm_status == "SKIPPED_TEMPLATE" else None
            ),
        ),
    )


def _split_skip_reason(결정: Mapping[str, Any]) -> str:
    """④ 판단자를 왜 안 불렀나 — 승인 전과 «적용되지 않을 것이 확정» 을 가른다.

    한 문장으로 뭉치지 않는다. 둘은 여는 방법이 다르다 — 앞은 정책
      승인이고, 뒤는 그날 입력(날짜별 여유 · 궤적)이다. 뭉치면 흔적만 보고 «비율을
      승인하면 불리겠지» 로 읽는데, 승인해도 뒤쪽 날은 계속 안 불린다.
    """
    if not 결정.get("allocation_approved"):
        return "배분 비율이 승인 전이라 후보가 균등 하나뿐이었다"
    if 결정.get("allocation_excluded"):
        return "다른 배분 후보가 결과에 그대로 적용되지 않을 것이 미리 확정돼 고를 것이 없었다"
    return "배분 후보가 하나뿐이라 고를 것이 없었다"


def _sourcing_call(state: Mapping[str, Any] | None) -> tuple[LLMCallMetadata, ...]:
    """⑤ 등급 조합 한 줄."""
    mix = _mix_decision(state)
    if mix is None:
        상태 = _uncalled_status()
        return (
            LLMCallMetadata(
                role=SOURCING_SELECTION,
                status=상태,
                skip_reason=(
                    None
                    if 상태 == "DISABLED"
                    else "등급 조합 후보가 서지 않아 판단자를 부를 자리까지 안 갔다"
                ),
            ),
        )
    return (
        LLMCallMetadata(
            role=SOURCING_SELECTION,
            status=mix.llm_status,
            attempts=mix.llm_attempts,
            fallback_used=mix.llm_fallback_used,
            # provider 를 비우지 않는다 — 역할 셋 중 하나만 추적이 끊기면
            #   「추적 가능하다」가 성립하지 않는다.
            provider=mix.llm_provider or None,
            model=mix.llm_model or None,
            **_판(MIX_ROLE, mix.llm_attempts),
            skip_reason=(
                "규칙이 중품을 안 골라 후보가 하나였다"
                if mix.llm_status == "SKIPPED_TEMPLATE"
                else None
            ),
        ),
    )


def _uncalled_status() -> LLMStatus:
    """판단자를 한 번도 안 부른 실행의 상태. 설정이 갈림길이다.

    무조건 ``DISABLED`` 로 두면 "LLM 을 안 켰네" 와 "켰는데 이번엔 안 썼네" 가 한 값이
      되고, 사람이 없는 문제를 찾는다. 예: 2025-12-31 실행은 등급이 미상이라 ⑤가 후보를
      만들기 전에 막혔는데, 설정은 켜져 있었다.

    봉투가 네 값의 뜻을 규정한다 (``contracts/envelope.py`` ``LLMStatus``)::

        DISABLED           설정이 꺼져 있다
        SKIPPED_TEMPLATE   켜져 있는데 이번 실행에서는 안 불렀다 — 부를 조건이 아니었다

    새로 정한 규칙이 아니다. 마스터 ``IntentService``·Critic ``JudgeService``·우리
    ``MixSelectionService`` 가 이미 ``DISABLED → SKIPPED_TEMPLATE → SUCCESS → FALLBACK``
    순서를 쓴다. 이 함수는 서비스에 닿기 전에 막히는 경로도 같은 뜻을 따르게 한다.

    STATUS_QUERY 처럼 애초에 판단 단계가 없는 실행도 ``SKIPPED_TEMPLATE`` 이다 —
    Critic 이 "이 Flow 에는 그 문장을 쓰는 단계가 없다" 를 같은 값으로 적는 것과 같다.
    """
    return "SKIPPED_TEMPLATE" if get_llm_settings().enabled else "DISABLED"


def _mix_decision(state: Mapping[str, Any] | None) -> Any:
    """⑤의 LLM 판단. ⑤가 비율 목록 첫 줄에 얹어 보낸다 (``sourcing_decision``과 같은 자리).

    ``None``인 경우가 둘이다 — ⑤가 아예 안 돈 경로(수신 검증 실패·STATUS_QUERY)와,
    돌았지만 게이팅에 걸려 LLM을 부르지 않은 날. 둘 다 실패가 아니고, 어느 쪽이든
    "이번 실행에서 안 불렀다"는 같은 사실이라 ``_uncalled_status()`` 가 설정으로 가른다.
    """
    if not state:
        return None
    ratios = state.get("sourcing_plan") or []
    decision = ratios[0].get("decision", {}) if ratios else {}
    return decision.get("mix")


def build_payload(state: Mapping[str, Any], proposal: Mapping[str, Any]) -> dict[str, Any]:
    """봉투에 실을 payload. 제안에 ``allowed_axes``를 얹는다.

    ``PurchaseProposal``(우리 출력 스키마)에는 ``allowed_axes``가 없다. 그런데 마스터가
    ``judgment_fields``로 선언하라고 한 두 이름 중 하나가 그것이고, 선언한 이름이
    payload에 없으면 ``E-JUDGMENT-UNKNOWN``이다 (전달_2차 §1). 검증 Tool이
    "타이밍이 닫혔는데 분할이 있다"를 잡으려면 그 값이 실려 나가야 한다 (답변 §4-3).

    스키마를 고치지 않고 어댑터에서 얹는 이유: ``PurchaseProposal``은 매입 내부의
    출력 계약이고 봉투 payload는 v2.2 계약이다. 둘을 잇는 것이 어댑터의 일이라, 여기서
    합치면 출력 스키마가 안 바뀐다. 스키마에 넣는 편이 낫다고 팀이 정하면 그때 스키마에
    넣는다 (프로세스 기준 8/26 — 스키마에 닿으면 합의 후).
    """
    return {**proposal, "allowed_axes": list(state.get("allowed_axes") or [])}


def purchase_port(
    request: AgentRequest, *, quotes: QuoteSource | None = None
) -> tuple[AgentReply, ExecutionMetadata]:
    """마스터가 부르는 유일한 진입점.

    ``mode``는 ``SUPPORTED_MODES`` 의 셋이다. 봉투(``_AGENT_MODES``)가 앞에서 막지만
    여기서도 검사한다. 막는 쪽이 우리 밖에만 있으면, 봉투 허용 목록이 넓어지는 날 모르는
    mode 가 ``_generate_scenarios`` 로 떨어져 조용히 안이 만들어진다.

    ``quotes``는 등급별 시세 공급자다 (#70). 이 인자의 기본값은 mock 이지만 실운영 등록은
    실 경락가를 꽂는다 — ``master/registry/bootstrap.py`` 가
    ``partial(purchase_port, quotes=auction_quote_source())`` 로 등록한다.

    mock 으로 두면 안이 하나도 안 나온다. 실측:

    .. code-block:: text

        같은 payload · as_of=2025-12-31
          mock      배추 0안 · 무 0안   self_check 가 전부 컷
          실 경락가  배추 2안 · 무 2안   business=ok

    ``max_price`` 는 실 ML 예측 밴드 상단에서 오고 ``grade_unit_price`` 는 시세에서 온다.
    한쪽만 mock 이면 출처가 다른 두 값을 비교하게 되고, 그 판정은 뜻이 없다.

    인자 기본값을 mock 으로 남겨 둔 이유는 테스트다 — 결정론 스위트가 DB 없이 돈다.
      실운영 기본값은 등록 자리에서 정한다. 그 기본값은 pytest 안에서만 닿는다 (#228).
      운영 경로에서 mock 포트를 부르면 ``MockNotAllowed`` 로 막히므로, 등록에서 실
      공급자를 빠뜨려도 조용히 mock 으로 돌지 않고 예외가 난다.

    ML ``current_price`` 와 매입 물량가중 시세는 다른 값이고, 다른 것이 맞다. 그 칸은
      시세가 아니라 앵커(0.4×어제 + 0.6×최근 7 거래일 평균)다 — 산식과 실 DB 재현값은
      ``readmodel/quotes.py`` 머리말에 있다.

      미결정: 두 값을 어떻게 병기해 보여줄지. 매입단가로 무엇을 쓸지는 이와 별개다.
    """
    if request.mode == "STATUS_QUERY":
        return _status_query(request)
    if request.mode == "SUPPLY_CAPACITY_QUERY":
        return _supply_capacity_query(request, quotes=quotes)
    if request.mode not in SUPPORTED_MODES:
        raise UnsupportedMode(
            f"매입은 mode={request.mode!r} 를 받지 않는다. "
            f"받는 것: {sorted(SUPPORTED_MODES)}. "
            f"라우팅이 열렸다면 이 목록도 같이 열려야 한다"
        )
    return _generate_scenarios(request, quotes=quotes)


def _status_query(request: AgentRequest) -> tuple[AgentReply, ExecutionMetadata]:
    """살아 있는지와 무엇을 받을 수 있는지를 답한다. 시나리오를 만들지 않는다."""
    reply = _reply(
        request,
        runtime_status="READY",
        business_status="ok",
        # 중첩 Mapping 하나로 담는다. 봉투는 최상위 값 중 비어 있지 않은 배열마다
        # Evidence를 요구하는데(``_needs_evidence``), 능력 목록에 근거를 붙이는 것은
        # 의미가 없다 — "이 mode를 받을 수 있다"는 계산 결과가 아니라 정적 사실이다.
        # 봉투 자신이 "중첩 구조의 근거 규칙은 도메인이 정한다"고 밝히므로, 규칙을
        # 우회하는 것이 아니라 그 설계를 그대로 쓰는 것이다.
        payload={
            "capabilities": {
                "agent_version": AGENT_VERSION,
                # 목록을 여기 적지 않는다 — ``SUPPORTED_MODES`` 가 정본이고
                #   문 앞 검사(``purchase_port``)가 같은 것을 본다.
                "supported_modes": list(SUPPORTED_MODES),
                "items": list(mocks.ITEMS),
            }
        },
        reasoning="매입 에이전트는 요청을 받을 수 있는 상태다.",
    )
    # ``used_tools``를 비운다. 봉투가 ``STATUS_QUERY``를 ``E-PLAN-EMPTY`` 예외로
    # 뺐으므로(``_PLAN_EXEMPT_MODES``) 가짜 Tool 이름을 넣을 이유가 사라졌다.
    # 검사를 피하려고 넣은 이름은 M-16이 읽는 실행 계획을 그대로 오염시킨다.
    return reply, _metadata(request, None)


#: `available_date` 를 못 내는 사유. null 일 때 사유 한 줄 — 계약이다
#: (현서님 2026-09-09 §4.2 · 우리가 동의). 값 칸만 null 로 정직하고 위험 칸이
#: 비면 "확인 안 함" 이 "위험 없음" 이 된다.
_NO_LEAD_TIME = "입고 소요일이 아직 확정되지 않아 언제 댈 수 있는지는 답하지 못한다"

#: 판매가 한 품목분을 읽는다 (`sales.schemas.PurchaseAdditionalSupplyResult` 는
#: 최상위 하나다). 여러 품목이 오면 어느 것을 최상위에 둘지 우리가 못 정한다.
_MULTI_ITEM = "한 번에 한 품목만 답한다 — 품목별로 나눠 물어야 한다"

#: 이 경로가 실제로 하는 일. 그래프를 안 도니 노드 이름이 아니다 — 시세를 읽고
#: 남의 값 둘과 함께 최솟값을 잡는 것이 전부다.
_SUPPLY_CAPACITY_TOOLS: tuple[str, ...] = ("get_market_quotes", "compute_supply_capacity")


def _supply_capacity_query(
    request: AgentRequest, *, quotes: QuoteSource | None = None
) -> tuple[AgentReply, ExecutionMetadata]:
    """판매 부족분에 경계만 답한다 — 그래프를 안 돈다 (E4-7).

    그래프 완주는 평균 11.2초 · 최대 136.6초인데 묻는 쪽이 청한 것은 안이 아니라 경계다
      (`260909_…가능량이_남의_값입니다.md` §1.4).

    품목 하나를 받아 하나를 답한다. 실재하는 유일한 회신 계약
      (`sales.schemas.PurchaseAdditionalSupplyResult`)은 `procurable_quantity_kg` ·
      `risks` 를 최상위에 두므로 한 품목분이고, 마스터 판매 Flow 도 품목마다 한 번씩
      부른다 (`master/service/sales_flow.py` 의 `_supply_capacity_input`). 여러 품목을
      배열로 싣는 모양은 읽는 쪽이 없어 만들지 않는다.

    `warehouse_free_kg` · `finance_cap_amount_krw` 는 마스터가 실어 주는 남의 값이다 —
      마스터가 `master/readmodel/procurement_boundary.py` 로 그날 매입 판단이 받은 경계를
      읽어 싣는다. 값이 비는 날은 못 읽었다고 답하고 `0` 으로 채우지 않는다 (규칙 3).

    시세를 읽고 경계를 잡는 것은 ``service/supply_capacity.py`` 다. 여기서는 어느 품목을
      묻는지 가리고, 결과를 회신에 담는다.
    """
    payload = request.payload
    items = payload.get("items")
    item = payload.get("item")
    extra_risks: list[str] = []
    if isinstance(items, (list, tuple)) and items:
        # 하나면 받아 준다 — 원소 하나인 ``items`` 는 ``item`` 과 뜻이 같다.
        if len(items) == 1 and item is None:
            item = items[0]
        elif item is None:
            extra_risks.append(_MULTI_ITEM)
    if not isinstance(item, str) or not item:
        reply = _reply(
            request,
            runtime_status="RUNTIME_NOT_READY",
            business_status="skipped",
            reasoning="어느 품목을 묻는지가 요청에 없어 경계를 낼 수 없다.",
            missing_data=("item",),
        )
        return reply, _metadata(request, None)

    constraints = load_constraints()
    # 한 번 읽은 시세를 둘 다 쓴다 — 같은 시세를 ``_observed_at`` 도 봐야 한다. 두 번
    #   읽으면 그 사이에 적재가 들어와 경계와 관측일이 다른 조회에서 나온다.
    reading = read_supply_capacity(
        item,
        request.context.as_of,
        constraints,
        quotes=quotes,
        warehouse_free_kg=_read_optional_number(payload, "warehouse_free_kg"),
        finance_cap_amount_krw=_read_optional_number(payload, "finance_cap_amount_krw"),
    )
    market_quotes, capacity = reading.market_quotes, reading.capacity

    # 묻는 이름과 답하는 이름이 다르다. 일부러 그렇다.
    #
    #     받을 때  required_additional_quantity_kg   판매 어휘 — "원래 얼마가 모자랐나"
    #     낼 때    requested_quantity_kg             회신 어휘 — "그 물음에 답한다"
    #
    # 그 둘을 맞추려 하지 않는다. 판매가 부족량을 그 이름으로 쥐고 있고
    # (`sales/schemas/proposal.py` 의 `required_additional_quantity_kg`), 회신 계약은
    # `requested_quantity_kg` 로 정해져 있다. 마스터 판매 Flow 도 받는 쪽 이름으로 보낸다.
    #
    # 주의: 이름이 틀리면 조용히 사라진다. `_read_optional_number` 는 없는 키에 `None` 을
    # 주고, 판매 모델은 `extra="ignore"` 다 — 양쪽 다 오류를 안 낸다.
    requested = _read_optional_number(payload, "required_additional_quantity_kg")
    risks = [*capacity.risks, *extra_risks, _NO_LEAD_TIME]
    body: dict[str, Any] = {
        "item": item,
        "procurable_quantity_kg": capacity.procurable_quantity_kg,
        "risks": risks,
        # 물류 N4(`pending.inbound_lead_days`)가 NULL 이라 계산 자체를 안 한다
        #   (규칙 3). 사유는 `risks` 에 있다.
        "available_date": None,
        "expected_unit_price_krw": capacity.expected_unit_price_krw,
        "unit_price_grade": capacity.unit_price_grade,
        "basis": capacity.basis,
    }
    if requested is not None:
        body["requested_quantity_kg"] = requested

    reply = _reply(
        request,
        runtime_status="READY",
        business_status="ok",
        payload=body,
        evidences=supply_capacity_evidences(body, capacity),
        reasoning=supply_capacity_reasoning(item, capacity),
        # 이 경로는 시세를 실제로 읽으므로 관측일을 싣는다 (`#393`).
        observed_at=_observed_at(
            market_quotes, item, request.context.as_of, constraints
        ),
    )
    # ``STATUS_QUERY`` 와 달리 비워 두면 안 된다. 그쪽은 봉투가
    #   ``_PLAN_EXEMPT_MODES`` 로 뺐지만 이 경로는 실제로 시세를 읽고 계산한다 —
    #   비면 `E-PLAN-EMPTY` 다. 그래프를 안 도니 ``ToolRecorder`` 대신 직접 적는다.
    return reply, _metadata(request, None, tools=_SUPPLY_CAPACITY_TOOLS)


def _read_optional_number(payload: Mapping[str, Any], key: str) -> float | None:
    """숫자면 그대로, 없거나 숫자가 아니면 ``None``.

    `0` 을 `None` 으로 바꾸지 않는다. `0` 은 "자리가 없다" 라는 읽은 값이고
      `None` 은 "못 읽었다" 다 (규칙 3).
    """
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _generate_scenarios(
    request: AgentRequest, *, quotes: QuoteSource | None = None
) -> tuple[AgentReply, ExecutionMetadata]:
    as_of = request.context.as_of
    # 부서 선언을 요청마다 한 번 읽어 수신 검사 · 근거 · 관측일에 같이 넘긴다 — domain 은
    # 파일을 읽지 않는다. 노드는 각자 읽는다(``service/nodes``).
    constraints = load_constraints()
    missing = validate_payload(request.payload, as_of, constraints)
    if missing:
        # 제약이 하나라도 빠진 시나리오는 만들지 않는다 (M-1 §11-6 · 제출 §4).
        # 재시도해도 같은 결과이므로 ERROR가 아니라 RUNTIME_NOT_READY다.
        #
        # payload를 싣지 않는다. ``RUNTIME_NOT_READY``는 "안 돌았다"인데
        #   ``{"scenarios": []}`` 같은 반쪽짜리 제안 형태를 실으면 ``PurchaseProposal``로
        #   파싱할 때 깨진다(``meta``·``no_proposal_reason`` 부재).
        #
        #   온전한 제안 형태로 채우는 것도 답이 아니다. "돌았는데 안이 없다"는
        #   ``READY`` + ``no_proposal_reason``으로 이미 따로 있어서, payload를 같게 만들면
        #   두 상태가 payload만 봐서는 구분되지 않는다. ``runtime_status``를 안 보는
        #   소비자가 하나라도 생기면 "안 돌았다"가 "돌았는데 안이 없다"로 읽힌다.
        #
        #   재무·물류도 이 자리에 payload를 안 싣는다. 무엇이 없는지는 ``missing_data``가,
        #   왜인지는 ``reasoning``이 말한다 — 봉투는 ``RUNTIME_NOT_READY``에
        #   ``missing_data``가 비지 않을 것만 요구한다.
        #
        # 사유를 무엇으로 쓸지는 ``not_ready_reason`` 이 정한다 (#213 · #67).
        #   ``missing_data`` 이름은 어느 쪽이든 실리지만, 사람이 읽는 사유가 같으면
        #   "ML 이 이 예측을 쓰지 말라고 했다" 가 "마스터가 값을 안 보냈다" 로
        #   읽힌다 — 마스터가 사용자에게 요청할 대상이 서로 다르다.
        reply = _reply(
            request,
            runtime_status="RUNTIME_NOT_READY",
            business_status="skipped",
            reasoning=not_ready_reason(missing, request.payload.get("item")),
            missing_data=tuple(missing),
        )
        return reply, _metadata(request, None)

    recorder = ToolRecorder()
    final = generate_scenarios(request, quotes=quotes, recorder=recorder)
    proposal = final["proposal"]

    payload = build_payload(final, proposal)
    reply = _reply(
        request,
        runtime_status="READY",
        # 안이 없는 것은 사실이지 오류가 아니다. E5 판정은 마스터가 한다.
        business_status="ok" if payload.get("scenarios") else "skipped",
        payload=payload,
        evidences=build_evidences(final, payload, constraints),
        reasoning=build_reasoning(payload),
        judgment_fields=JUDGMENT_FIELDS,
        # 그래프가 본 시세를 그대로 본다 (`#393`). ``final`` 에서 꺼내야
        #   ③·⑤가 판정에 쓴 것과 같은 조회다 — 여기서 다시 읽으면 그 사이 적재가
        #   들어와 노드가 안 본 관측일이 계보에 실릴 수 있다.
        observed_at=_observed_at(final["market_quotes"], final["item"], as_of, constraints),
    )
    return reply, _metadata(request, recorder, state=final)
