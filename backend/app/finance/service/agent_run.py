"""재무 에이전트 실행 준비 — 봉투 축으로 런타임 컨텍스트를 고정하고 Controller 에 넘긴다.

★ 준비되지 않은 경계(축 없음 · 상태 없음 · 날짜 어긋남 · 급여 · 매입 정책 출처)는 실행 전에
  `RUNTIME_NOT_READY` 회신으로 닫는다. Controller 는 고정된 컨텍스트 위의 DataPort
  (`RuntimeContextDataPort`)만 본다.

★ 2026-09-29 재구성 BL-014: `finance/adapter.py` 에서 옮겼다(몸통 그대로, 밑줄만 뗐다).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

from app.contracts.envelope import AgentReply, AgentRequest, ExecutionMetadata
from app.finance.domain import messages
from app.finance.domain.evidence import PAYROLL_SOURCE_KEYS
from app.finance.domain.status_facts import JUDGMENT_FIELDS, T_POSITION
from app.finance.readmodel.partner_credit import load_partner_credit_limit, load_partner_receivables
from app.finance.readmodel.runtime_context import get_current_finance_runtime_context
from app.finance.schemas.agent import FinancePolicy, FinanceRuntimeContext
from app.finance.schemas.agent_state import missing_source_name
from app.finance.schemas.data_port import FinanceDataNotReady
from app.finance.schemas.sales_validation import PartnerReceivable
from app.finance.service.agent import FinanceAgentController
from app.finance.service.agent_replies import axis_not_ready_reply, new_run_id, not_ready_reply

_POLICY_KEYS_IN_USE: tuple[str, ...] = (
    "purchase_payment_days",
    "payroll_date",
    "monthly_labor_cost_krw",
    "minimum_cash_balance_krw",
    "cashflow_projection_days",
)
"""이 어댑터의 산출에 실제로 들어가는 정책값.

★ **전부 `source_refs` 에 있어야 한다** — 없으면 DB 가 아니라 Schema default 다.
  Repository 가 그 키를 조회하지 않으면 Pydantic 기본값이 대신 쓰이는데, 값은 멀쩡히
  나오고 에러도 안 난다. **DB 를 고쳐도 반영되지 않는다는 사실만 조용히 숨는다.**

  실제로 `payroll_date` 가 그 상태였다(2026-08-27 재무 후속회신 §3). DB(10)와
  default(10)가 우연히 같아 양쪽 다 눈치채지 못했다.

목록을 여기 두는 이유는 재무 Policy 에 필드가 늘어도 **우리가 쓰는 것만** 보기 위해서다."""


class RuntimeContextDataPort:
    """이미 고정된 Finance Runtime 컨텍스트를 Agent Tool에 제공한다.

    Master Policy 버전은 오케스트레이션 요청을 식별한다. Finance Policy는 별도
    버전을 가지므로, Controller는 이 경계에서 실제로 읽은 버전을 사용한다.
    """

    def __init__(self, context: FinanceRuntimeContext):
        self.context = context

    def _check_as_of(self, as_of: date) -> None:
        if self.context.snapshot.state_date != as_of:
            raise FinanceDataNotReady("historical_finance_position")

    def load_finance_position(self, as_of: date) -> dict[str, object]:
        self._check_as_of(as_of)
        return self.context.snapshot.model_dump()

    def load_policy(self, as_of: date, policy_version: str) -> FinancePolicy:
        self._check_as_of(as_of)
        # ``policy_version``은 Master 실행 컨텍스트의 값이며 Finance Policy 선택자가
        # 아니다. 이 Controller 실행에서 사용할 Finance Policy 버전은 고정된 Runtime
        # 컨텍스트가 정본이다.
        del policy_version
        policy = self.context.policy
        if isinstance(policy, FinancePolicy):
            return policy
        # 컨텍스트가 덕타이핑 Policy 를 들고 있으면 계약 타입으로 정규화한다.
        #
        # 🔴 **없는 `source_ref` 를 지어내지 않는다.** 예전에는 여기서 빠진 키마다
        #    `finance-policy:{version}:{key}` 를 채워 넣었다. 값은 멀쩡히 나오고 에러도
        #    안 나지만, 그 ref 는 DB 어디에도 없어서 **따라가면 아무 데도 닿지 않는다.**
        #    출처가 없는 것은 감출 일이 아니라 밝힐 일이다 — `_source_ref` 가
        #    `RUNTIME_NOT_READY` + `missing_data` 로 세운다 (§16).
        raw = {
            field: getattr(policy, field, None)
            for field in FinancePolicy.model_fields
            if field not in {"usage_scope"}
        }
        return FinancePolicy.model_validate(raw | {"usage_scope": "AGENT_MVP_DEMO"})

    def _events(self, direction: str) -> list:
        return [event for event in self.context.cash_events if event.direction == direction]

    def load_obligations(self, as_of: date, horizon: date) -> list:
        self._check_as_of(as_of)
        return self._events("OUTFLOW")

    def load_receivables(self, as_of: date, horizon: date) -> list:
        self._check_as_of(as_of)
        return self._events("INFLOW")

    def load_payroll(self, as_of: date, horizon: date) -> Decimal | None:
        self._check_as_of(as_of)
        return self.context.policy.monthly_labor_cost_krw

    def load_debt_schedule(self, as_of: date, horizon: date) -> list:
        self._check_as_of(as_of)
        return []

    def load_partner_receivables(self, as_of: date, partner_id: str) -> list[PartnerReceivable]:
        """거래처 채권만은 **고정된 컨텍스트 밖**에서 읽는다.

        ★ 컨텍스트는 요청 payload 를 보기 전에 선다. 어느 거래처인지는 그때 알 수
          없으므로 채권을 미리 담아 둘 수 없다 — 여기서 이 실행의 `sim_run_id` 와
          `as_of` 를 걸고 읽는다.

        ★ `_check_as_of` 를 먼저 통과시켜, 다른 날짜 요청이 오늘 채권을 대신 받는
          일이 없게 한다.
        """
        self._check_as_of(as_of)
        return load_partner_receivables(
            sim_run_id=self.context.snapshot.sim_run_id, as_of=as_of, partner_id=partner_id
        )

    def load_partner_credit_limit(self, as_of: date, partner_id: str) -> Decimal | None:
        """거래처 여신한도도 고정 컨텍스트 밖에서 읽는다 — 어느 거래처인지는 payload 가 안다.

        ★ 실행 축을 걸지 않는다. 한도는 거래처·계약이 소유한 사실이라 어느 시뮬레이션
          에서 보든 같다. 시점만 `as_of` 로 자른다.
        """
        self._check_as_of(as_of)
        return load_partner_credit_limit(as_of=as_of, partner_id=partner_id)


def _controller_request(request: AgentRequest, context: FinanceRuntimeContext) -> AgentRequest:
    """Master 컨텍스트를 다시 쓰지 않고 Data Port가 Finance 자체 Policy를 해석하게 한다."""
    del context
    return request


def run_controller(
    request: AgentRequest, context: FinanceRuntimeContext
) -> tuple[AgentReply, ExecutionMetadata]:
    """레거시 업무 계약 주석만 보강하고 Controller 메타데이터는 그대로 유지한다."""
    reply, metadata = FinanceAgentController(RuntimeContextDataPort(context)).run(
        _controller_request(request, context)
    )
    if reply.runtime_status != "READY" or request.mode != "PRE_PURCHASE":
        return reply, metadata
    missing = list(reply.missing_data)
    if getattr(context.policy, "margin_defense_floor_rate", None) is None:
        missing.append("margin_defense_floor_rate")
    missing.extend(
        missing_source_name(key)
        for key in _POLICY_KEYS_IN_USE
        if key not in context.policy.source_refs
    )
    payload = dict(reply.payload)
    if getattr(context.policy, "margin_defense_floor_rate", None) is None:
        payload.pop("margin_defense_floor_rate", None)
    return replace(
        reply,
        payload=payload,
        missing_data=tuple(dict.fromkeys(missing)),
        judgment_fields=JUDGMENT_FIELDS,
    ), metadata


def controller_boundary(
    request: AgentRequest,
) -> tuple[FinanceRuntimeContext | None, tuple[AgentReply, ExecutionMetadata] | None]:
    """Controller 위임 전에 Adapter 수준의 준비 상태 의미를 보존한다."""
    run_id = new_run_id(request)
    sim_run_id = request.context.sim_run_id
    if not sim_run_id.strip():
        return None, axis_not_ready_reply(request, run_id)
    context = load_runtime_context(request.context.as_of, sim_run_id=sim_run_id)
    if context is None:
        return None, not_ready_reply(
            request, run_id, [T_POSITION],
            missing=("finance_state", "finance_policy"),
            reason=messages.CONTEXT_UNAVAILABLE,
        )
    if context.snapshot.state_date != request.context.as_of:
        return None, not_ready_reply(
            request, run_id, [T_POSITION],
            missing=(f"finance_state@{request.context.as_of.isoformat()}",),
            reason=messages.AS_OF_MISMATCH,
        )
    payroll_refs = tuple(
        missing_source_name(key)
        for key in PAYROLL_SOURCE_KEYS
        if not context.policy.source_refs.get(key)
    )
    if payroll_refs:
        return None, not_ready_reply(
            request, run_id, [T_POSITION],
            missing=payroll_refs,
            reason=messages.PAYROLL_SOURCE_MISSING,
        )
    # 🔴 매입 전용 정책이 **모든 mode** 를 막고 있었다.
    #
    #    `purchase_payment_days` 는 매입 지급일 상한(`calculate_finance_cap`)에만 쓰인다 —
    #    판매 검증은 이 값을 한 번도 읽지 않는다. 그런데 공통 boundary 에 있어서, 매입
    #    지급일 정책이 없는 날에는 **판매 검증도 실행 전에 통째로 막혔다.** 재무가 판매를
    #    못 본 이유가 "매입 정책이 없어서" 가 되는 것이라 사유 자체가 거짓이다.
    #
    # ★ 매입 쪽 방어는 그대로다. 아래 두 mode 에서는 여전히 실행 전에 요구한다.
    if request.mode in _PURCHASE_POLICY_MODES and context.policy.purchase_payment_days is None:
        return None, not_ready_reply(
            request, run_id, [T_POSITION],
            missing=("purchase_payment_days",),
            reason=messages.PAYMENT_DAYS_MISSING,
        )
    return context, None


# ---------------------------------------------------------------------------
# 도우미
# ---------------------------------------------------------------------------


def load_runtime_context(as_of: date, *, sim_run_id: str) -> FinanceRuntimeContext | None:
    """봉투의 ``sim_run_id`` · ``as_of`` 로 상태를 고른다.

    ★ **축은 받는 것이지 고르는 것이 아니다** (재무 기준 ①②③ · 2026-09-11).
      어느 실행의 잔액인가는 재무 사실이 아니라 마스터가 정한 실행 축이다. 재무가
      스스로 추측하거나 전역 Current State 에서 하나를 집으면, 번인과 걷기가 함께
      서 있는 날 **남의 실행 잔액**으로 매입을 판단하게 된다.

    🔴 **`sim_run_id` 는 기본값이 없다.** 부르는 자리가 빠뜨리면 조용히 전역으로
       떨어지는 대신 그 자리에서 `TypeError` 로 선다 — 축을 안 넘기는 호출이
       새로 생기는 것을 **문법이 막는다.**

    🔴 **빈 축으로는 DB 에 묻지 않는다.** 축이 비었다는 것은 *"물어볼 수 없다"* 이지
       *"자료가 없다"* 가 아니다. 물어보면 그 결과가 `LookupError` 로 돌아오고,
       아래 `except` 가 그것을 "없음" 으로 바꿔 **묻지 못한 것이 자료 없음으로**
       기록된다. 여기서 끊고, 사유는 부르는 자리가 축 누락으로 적는다.

    ★ 예외를 삼키는 태도는 그대로다 — 없는 것은 예외가 아니라 상태다.
    """
    if not sim_run_id.strip():
        return None
    try:
        return get_current_finance_runtime_context(as_of, sim_run_id=sim_run_id)
    except Exception:  # noqa: BLE001 — 없는 것은 예외가 아니라 상태다
        return None


#: 매입 실행 정책(`purchase_payment_days`)을 **실행 전에** 요구하는 mode.
#:
#: ★ 판매 검증은 여기 없다. 그 값은 매입 지급일 상한 계산에만 쓰이므로, 판매를
#:   막을 이유가 되지 않는다 — 막으면 "매입 정책이 없어서 판매를 못 봤다" 는
#:   거짓 사유가 이력에 남는다.
_PURCHASE_POLICY_MODES: frozenset[str] = frozenset({"PRE_PURCHASE", "SCENARIO_VALIDATION"})
