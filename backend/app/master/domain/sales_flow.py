"""판매 Flow 의 결과 모델 — 후보 판정(CandidateVerdict) · 결과(SalesOutcome). 실행은
  `service/sales_flow.py`.

★ 2026-09-30 재구성 BL-018: `master/sales_flow.py` 에서 옮겼다 — `_CONCLUDED_VERDICTS`,
  `_validation_concluded`, `CandidateVerdict`, `SalesOutcome`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.contracts.core import SuggestedAdjustment
from app.contracts.envelope import PASSING_VERDICTS, AgentFailure, SourcedEvidence
from app.master.domain.plan import ExecutionPlan
from app.master.domain.sales_approval import missing_term_origins, missing_terms_reason
from app.master.schemas.sales import SalesEndCode

#: 부서가 **실제로 판정을 낸** 업무 상태. 통과 여부는 묻지 않는다.
#:
#: 🔴 **`PASSING_VERDICTS` 의 반대가 아니다.** *"통과가 아니다"* 안에는 두 가지가
#:   섞여 있다 — **판정이 났는데 안 된다**(`reject`)와 **판정 자체가 안 났다**
#:   (`skipped`). 둘을 한 덩어리로 다루면 자료가 없어 못 본 안이 거절당한 안과
#:   같은 자리에 놓이고, 사용자는 고칠 수 없는 것을 고치러 간다.
#:
#: ★ 매입 `flow._JUDGED_VERDICTS` 와 같은 뜻이다. 두 Flow 가 각자 들고 있는 이유는
#:   `PASSING_VERDICTS` 를 봉투로 올릴 때와 같다 — 판매가 매입 모듈에 매이지 않는다.
_CONCLUDED_VERDICTS: frozenset[str] = PASSING_VERDICTS | {"reject"}


def _validation_concluded(verdict: Mapping[str, Any]) -> bool:
    """이 검증이 **판정까지 갔는가.**

    두 축을 같이 본다. `runtime_status` 가 `READY` 가 아니면 부서가 실행을 끝내지
    못한 것이고(`RUNTIME_NOT_READY` · `ERROR`), `READY` 라도 업무 상태가 판정 어휘
    밖이면(`skipped`) 부서가 **판정을 안 낸 것**이다 — 재무 `INPUT_INCOMPLETE` 가
    그 자리다.
    """
    return (
        str(verdict.get("runtime_status") or "") == "READY"
        and str(verdict.get("business_status") or "") in _CONCLUDED_VERDICTS
    )


@dataclass(frozen=True)
class CandidateVerdict:
    """후보 하나에 대한 판정 — **무엇을 물었고 무엇이 왔는가.**

    ★ **후보 단위다** (설계 정정 ① · 2026-09-06). 판매 v1.7 §4 는
      `required_validations` 를 최상위 배열로 적었지만 구현은 시나리오별이다
      (`app/sales/schemas/proposal.py` `SalesScenario.required_validations`). 1안은 재무만,
      2안은 재무+매입 식으로 **후보마다 요구가 다를 수 있다.**

    ★ **통과/탈락을 필드로 들지 않는다.** `passed` 는 `validations` 와 `unroutable`
      에서 나오는 값이라 필드로 두면 같은 사실의 주인이 둘이 된다.
    """

    #: 판매가 낸 후보 그대로. **마스터는 고르지도 재계산하지도 않는다** (§3.2.2).
    scenario: Mapping[str, Any]

    #: capability → 그 검증의 회신. 키는 판매가 요구한 이름 그대로다.
    validations: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    #: 🔴 **부를 대상이 없어 못 물어본 요구.** 조용히 버리지 않는다 (설계 §3).
    #: 이 칸이 비어 있지 않으면 그 후보는 **통과로 치지 않는다** — 미해결로 둔다.
    unroutable: tuple[str, ...] = ()

    #: 사용자가 말한 조건 그대로 (`SalesFlow.user_request`). **누락의 위치를 가르는 데만
    #: 쓴다** — 후보를 고르거나 값을 채우는 데 쓰지 않는다 (§3.2.2).
    #:
    #: 🔴 **`SalesFlow` 를 통째로 들지 않는다.** 판정 하나가 실행기를 참조하면 이
    #:   판정을 재는 검사가 실행기를 세워야 한다 — 그래서 필요한 두 값만 들고 온다.
    user_request: Mapping[str, Any] | None = None

    #: 무슨 판매인가 (`SalesProposalInput.business_mode`). **`CONTRACT_FULFILLMENT` 을
    #: 가르는 데 쓴다** — 그 경로는 계약값을 쓰므로 `preferred_*` 가 안 실리는 것이
    #: 정상이고, 거기서 `REQUEST_MISSING` 을 내면 거짓말이 된다.
    business_mode: str | None = None

    @property
    def scenario_id(self) -> str:
        return str(self.scenario.get("scenario_id") or "")

    @property
    def unvalidated(self) -> bool:
        """요구한 검증이 **하나도 없었다.**

        🔴 이 후보는 통과로 나가지만 **아무도 안 본 안이다.** 마스터가 요구를 지어내지
          않기 때문이다 — 무엇이 필요한지는 제안자가 정한다 (§3.2.2). 대신 그 사실이
          여기 남아 화면이 *"검증 0건"* 을 말할 수 있다.
          *"검사하지 못한 것을 검사했다고 말하지 않는다"* (설계서 §8).
        """
        return not self.validations and not self.unroutable

    @property
    def missing_terms(self) -> tuple[str, ...]:
        """🔴 **확정에 필요한데 비어 있는 상업조건 — 어디서 끊겼는지까지.** 없으면 빈 튜플이다.

        ```text
        REQUEST_MISSING_<FIELD>     부르는 쪽이 안 보냈다   → 고칠 사람: 화면 · 걷기 · API 호출자
        TERMS_UNRESOLVED_<FIELD>    정말 조건이 없다        → 고칠 사람: 판매 · 계약
        ```

        ★ **이 구분의 값은 "누가 고쳐야 하나" 가 이름에서 보이는 것이다.** 전에는
          `delivery_date` 하나만 말해서, 화면에 채울 칸이 있는데 안 채운 것인지 판매가
          못 만든 것인지 사람이 코드를 읽어야 알았다.

        ★ **부서 판정과 다른 칸이다.** `validations` 에 가짜 항목을 밀어 넣지 않는다 —
          탈락 사유가 *"재무가 반려"* 처럼 보이면 사람이 재무를 본다. *"납품일이
          없다"* 로 보여야 판매를 본다.

        ★ **목록의 주인은 `sales_approval.REQUIRED_COMMERCIAL_TERMS` 다.** 승인 뒤
          `confirm_sale` 이 막는 것과 **같은 목록**을 여기서 미리 읽는다 — 베껴 두면
          *"올려도 되는 안"* 과 *"확정할 수 있는 안"* 이 갈린다.
        """
        return missing_term_origins(
            self.scenario,
            user_request=self.user_request,
            business_mode=self.business_mode,
        )

    @property
    def passed(self) -> bool:
        """사용자에게 올려도 되는가.

        ★ **허용목록으로 정한다** (`PASSING_VERDICTS`). *"reject 가 아니면 통과"* 로
          정하면 봉투 어휘가 늘 때마다 새 값이 통과 쪽으로 샌다 (#173).

        🔴 **상업조건 필수값도 여기서 본다** (2026-09-08 계약). `delivery_date` 나
          `payment_days` 가 없는 안은 사용자가 골라도 확정할 수 없다 — 값이 없는
          안을 고른 **뒤에야** 막는 것보다 **승인 가능한 후보의 필수조건**으로 두는
          편이 계약상 명확하다.

          ⚠️ 이 줄을 지우면 값 없는 안이 화면에 오르고, 사용자가 그것을 고른 뒤에야
            `sales_approval` 이 `BLOCKED` 를 낸다.
        """
        if self.unroutable:
            return False
        if self.missing_terms:
            return False
        return all(
            str(v.get("business_status") or "") in PASSING_VERDICTS
            for v in self.validations.values()
        )

    @property
    def unresolved_validations(self) -> tuple[str, ...]:
        """판정이 **나지 않은** 검증의 이름. 탈락과 다른 칸이다.

        🔴 **후보를 살려 두는 근거가 이 칸이다.** `passed` 는 여전히 거짓이고
          그래야 한다 — 판정 못 낸 안을 통과시키면 보지 않은 것을 통과시킨 것이다.
          하지만 *"통과가 아니다"* 와 *"거절당했다"* 는 다르고, 그 차이를 담을 자리가
          없어서 지금까지 둘이 같은 모양으로 나갔다.

        ★ **`skipped` 를 통과로 만들지 않는다.** 이 칸은 통과 여부를 바꾸지 않고
          *"왜 통과가 아닌가"* 만 가른다 — `PASSING_VERDICTS` 는 그대로다.

        ★ `unroutable`(아예 못 물어본 요구)은 **여기 넣지 않는다.** 그것은 이미 자기
          칸이 있고, 재검증도 그것으로는 실패를 내지 않는다 (`revalidation._verdict`).
        """
        return tuple(
            capability
            for capability, verdict in self.validations.items()
            if not _validation_concluded(verdict)
        )

    @property
    def rejected_validations(self) -> tuple[str, ...]:
        """부서가 **판정해서 막은** 검증. 자료가 없어 못 본 것과 섞지 않는다."""
        return tuple(
            capability
            for capability, verdict in self.validations.items()
            if _validation_concluded(verdict)
            and str(verdict.get("business_status") or "") not in PASSING_VERDICTS
        )

    @property
    def detail(self) -> str:
        """왜 탈락했나 — **사람이 읽는 한 줄. 여기서만 만든다.**

        ★ 마스터가 사유를 새로 쓰지 않는다. 부서가 보낸 `reasoning` 을 그대로 옮기고,
          없으면 상태값만 적는다 (§3.2.2 · `AgentFailure.detail` 과 같은 자리).
        """
        parts: list[str] = []
        if self.unroutable:
            parts.append(f"부를 대상이 없는 요구: {', '.join(self.unroutable)}")
        # 🔴 **부서 판정 줄과 섞이지 않게 따로 적는다.** 아래 줄은
        #   `capability(runtime/business)` 모양이고 이 줄은 칸 이름을 부른다.
        if self.missing_terms:
            parts.append(missing_terms_reason(self.missing_terms))
        for capability, verdict in self.validations.items():
            business = str(verdict.get("business_status") or "?")
            if business in PASSING_VERDICTS:
                continue
            runtime = str(verdict.get("runtime_status") or "?")
            head = f"{capability}({runtime}/{business})"
            why = str(verdict.get("reasoning") or "").strip()
            parts.append(f"{head}: {why}" if why else head)
        return " / ".join(parts) if parts else "통과"


@dataclass(frozen=True)
class SalesOutcome:
    """판매 Flow 한 번의 결과. **무엇을 못 했는지도 담는다.**

    ★ 매입 `ProcurementOutcome` 과 모양이 닮았지만 담는 것이 다르다. 매입은
      *"시나리오 배열 + 부서별 판정"* 이고 판매는 **후보마다 자기 판정을 들고 있다** —
      부분 통과가 정상이라 부서 축으로 접으면 어느 후보가 왜 떨어졌는지가 사라진다.
    """

    end_code: SalesEndCode
    reason: str
    plan: ExecutionPlan

    #: 통과·탈락을 **한 칸에** 담는다. 가르는 것은 아래 property 다 — 두 칸으로 두면
    #: 같은 후보가 양쪽에 들어가는 날을 아무도 못 막는다.
    candidates: tuple[CandidateVerdict, ...] = ()

    #: `scenarios` 를 뺀 제안 최상위 — 판매의 `situation`·`business_mode`·`self_check`.
    #: 매입 `_judgment_of` 와 같은 자리이고, **키를 고르지 않는다.**
    judgment: Mapping[str, Any] = field(default_factory=dict)

    #: ②에서 받은 초기 물류 컨텍스트. **못 받았으면 비어 있고** 그 사유는 아래 칸에 있다.
    supply_context: Mapping[str, Any] = field(default_factory=dict)

    #: 🔴 **물류가 컨텍스트를 못 냈다는 사실.** 판매는 밴드가 없어 여기서 멈추지 않지만,
    #: 멈추지 않는 것과 없던 일로 하는 것은 다르다 — 후보의 질이 왜 떨어졌는지를
    #: 나중에 읽는 사람이 볼 수 있어야 한다.
    context_failure: AgentFailure | None = None

    #: 🔴 **재무가 선행 사실을 못 냈다는 사실** (②' · 2026-09-16).
    #:
    #: 위 물류 칸과 나란히 둔다 — 같은 종류의 사실이고, 한 칸에 합치면 *"물류가 못
    #: 답했다"* 와 *"재무가 못 답했다"* 가 화면에서 같아진다.
    finance_context_failure: AgentFailure | None = None

    #: 🔴 **ML 예측을 못 실은 이유. 실었으면 빈 문자열이다** (M-1).
    #:
    #:   판매 v1.7 은 *"ML missing 은 전체 Sales 실패가 아니다"* 라고 적었다. 그래서
    #:   여기서 Flow 를 세우지 않는다 — `context_failure` 와 같은 태도다.
    #:
    #: ★ **멈추지 않는 것과 없던 일로 하는 것은 다르다.** 예측 없이 만든 후보와 예측을
    #:   보고 만든 후보는 무게가 다른데, 조용히 빠지면 화면이 둘을 같게 보여준다.
    #:
    #: ⚠️ **`AgentFailure` 가 아니다.** ML 은 호출 대상이 아니라 **입력**이라
    #:   (`inputs.py` 머리말) 부서 실패 모양에 담으면 없는 에이전트를 지어내게 된다.
    ml_context_note: str = ""

    evidences: tuple[SourcedEvidence, ...] = ()
    adjustments: tuple[SuggestedAdjustment, ...] = ()

    #: 실제로 돈 되먹임 회차. **0 이면 되먹임하지 않았다** (통과 후보가 있었거나,
    #: 권위 있는 대안이 없어 다시 물어도 같았거나).
    feedback_attempts: int = 0

    @property
    def presented(self) -> tuple[CandidateVerdict, ...]:
        return tuple(c for c in self.candidates if c.passed)

    @property
    def rejected(self) -> tuple[CandidateVerdict, ...]:
        """탈락 후보. **SL1 에서도 비어 있지 않을 수 있다** — 사유를 동봉해 함께 낸다.

        ⚠️ **이 칸에는 판정이 안 난 후보도 들어 있다** — `passed` 의 여집합이기
          때문이다. 둘을 갈라 보려면 `unresolved` 를 쓴다. 이름을 바꾸지 않는 이유는
          화면·이력이 이 칸을 쓰고 있어서다.
        """
        return tuple(c for c in self.candidates if not c.passed)

    @property
    def unresolved(self) -> tuple[CandidateVerdict, ...]:
        """**판정이 끝나지 않은** 후보. 후보는 살아 있고 승인만 못 한다.

        ★ `rejected` 의 부분집합이다 — 통과가 아니라는 점은 같고, **왜** 통과가
          아닌지가 다르다. 화면이 둘을 같은 줄로 보여 주면 사용자는 자료를 채워야
          할 날에 조건을 바꾼다.
        """
        return tuple(c for c in self.candidates if not c.passed and c.unresolved_validations)

    @property
    def unroutable_capabilities(self) -> tuple[str, ...]:
        """이번 실행에서 **못 불러 본 요구**의 전부. 후보들 것을 모아 이름만 남긴다."""
        return tuple(sorted({cap for c in self.candidates for cap in c.unroutable}))

    @property
    def presentable(self) -> bool:
        return self.end_code == "SL1_PRESENTED" and bool(self.presented)
