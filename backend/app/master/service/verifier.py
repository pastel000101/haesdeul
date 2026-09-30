"""
verifier.py — 마스터가 직접 가진 검증 Tool (정의서 §3.7)

    ① 부서가 낸 답의 자격      ← 봉투 검증이 이미 호출마다 돈다 (runner.call)
    ② 마스터 자신의 계산 재검산  ← 결합·클리핑이 붙은 뒤
    ③ 합쳤을 때의 모순
    ④ 실행 계획 온전성 (M-16)   ← 여기서 전부 구현한다

★ 규칙(②의 항등식 · ③ · ④)은 `app/master/domain/verifier.py` 에 있다 — 받은 값만 보고
  `findings` · `concerns` · `skipped` 를 낸다. 이 파일은 **Critic 56검사를 부르는 자리**와
  규칙 → Critic → 미구현 검사 고지의 순서다.

★ **판정하지 못한 것을 판정했다고 말하지 않는다** (§3.7.6).
  Critic 을 못 돌렸으면 그 사유를 `skipped` 에 남긴다. 비워 두면 **"검사했고 통과했다"로
  읽힌다.**

★ **커버리지를 감추지 않는다** (§3.7.6).
  Critic 56검사를 붙였고(2026-08-27), **몇 개가 돌았는지를 `skipped` 에 적는다.**
  `findings: []` 만 보면 *"56검사를 통과했다"* 로 읽힌다 — 실제로는 부서 메타 미제출
  같은 이유로 절반이 안 돌 수 있고, 그 사실이 같이 보여야 한다.

★ 2026-09-30 재구성 BL-018: `master/verifier.py` 에서 자리만 옮겼고(내용 그대로), 같은 날
  규칙부(판정 규칙 여섯 · 결과 모델 `VerificationResult` · 공개 검사 둘)를 `domain/verifier.py`
  로 뗐다. 여기 남은 것은 Critic 진입점 Protocol · Critic 에 넘길 맥락 · 호출과 예외 분류다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

from pydantic import ValidationError

from app.contracts.core import Evidence
from app.contracts.envelope import AgentName
from app.master.adapters.critic_bridge import (
    CriticSkipped,
    build_request,
    build_sales_request,
    fold,
)
from app.master.critic.schemas import CriticProcurementRequest, CriticSalesRequest, CriticVerdictOut
from app.master.critic.service import run_critic_procurement, run_critic_sales
from app.master.domain.plan import ExecutionPlan
from app.master.domain.verifier import UNCOVERED_CHECKS, VerificationResult, check_proposal

# 🟢 **지연 import 와 `_default_critic` 래퍼를 지웠다** (2026-09-07 · 2판).
#
#   1판은 Critic 이 마스터 밖(`app/critic/`)에 있어 고리가 닫혔고, 그래서 최상단 import 를
#   못 썼다. `critic=None` 은 *"Critic 을 안 돌렸다"* 는 뜻이라 센티넬로 쓸 수 없어
#   래퍼 함수 하나를 기본값 자리에 세웠던 것이다.
#
# ★ critic 이 마스터 안으로 들어와 고리가 없어졌으므로 기본값이
#   `run_critic_procurement` **자체**가 된다. `None` 의 뜻은 그대로 살아 있다.


class CriticPort(Protocol):
    """Critic 진입점. 갈아 끼울 수 있게 두는 이유는 테스트가 아니라 **격리**다 —
    검증 Tool 이 도메인 구현에 직접 묶이면 Critic 이 바뀔 때 마스터가 흔들린다."""

    def __call__(self, req: CriticProcurementRequest) -> CriticVerdictOut: ...


class SalesCriticPort(Protocol):
    """판매 Critic 진입점 (사이클 B).

    ★ `CriticPort` 와 **따로 둔다.** 받는 계약이 다르다 —
      `CriticProcurementRequest` 와 `CriticSalesRequest` 는 필수 칸부터 갈린다
      (`allocations` · `lot_constraints`). 하나로 묶으면 타입이 `Any` 로 넓어지고,
      그러면 갈아 끼울 때 잘못된 사이클을 꽂아도 아무 데서도 안 걸린다.

    ★ 갈아 끼울 수 있게 두는 이유는 `CriticPort` 와 같다 — 테스트가 아니라 **격리**다.
    """

    def __call__(self, req: CriticSalesRequest) -> CriticVerdictOut: ...


@dataclass(frozen=True)
class VerificationContext:
    """검증이 **부서 판정을 넘어** 보려면 필요한 것.

    ★ `evidences` 가 여기 있는 이유 — `constraints` 는 payload 만 담는다. Critic 은
      cap 축마다 근거를 요구하므로(§1.2-5) 근거 없이 넘기면 **없는 것이 아니라 안 넘긴
      것인데 계약 위반으로 잡힌다.**
    """

    as_of: date
    item: str | None = None
    evidences: Mapping[AgentName, tuple[Evidence, ...]] = field(default_factory=dict)

    #: 부서가 스스로 남긴 관측. **마스터는 읽지 않고 나른다** (`critic_bridge`).
    #:
    #: `evidences` 와 같은 이유로 여기 있다 — `constraints` 는 payload 만 담는데,
    #: Critic 의 `E-AUTHORITY` · `E-GRADE-LEAK` 는 *"그 부서가 무엇을 읽고 무엇을
    #: 냈나"* 를 요구한다. 마스터가 추측하면 **모르는 것이 근거가 되므로** 부서가
    #: 적어 보낸 것만 옮긴다. 없으면 Critic 이 그 검사를 생략한다.
    observations: Mapping[AgentName, tuple[str, ...]] = field(default_factory=dict)


class MasterVerifier:
    """마스터의 검증 Tool.

    ★ 제안자와 심판을 분리하는 것이 전부다.
      재무·물류는 *"내 기준으로 되나"* 를 답하고, 이쪽은 *"그 답이 규칙대로 나왔나 ·
      마스터가 제대로 합쳤나 · 합친 결과가 앞뒤가 맞나"* 를 본다.

    ★ 규칙은 `domain/verifier.check_proposal` 이 본다. 여기는 그 결과에 Critic 56검사를
      붙이는 자리다 — 규칙 → Critic → 미구현 검사 고지 순서.
    """

    def __init__(
        self,
        required_advisors: tuple[AgentName, ...] = ("finance", "inventory"),
        critic: CriticPort | None = run_critic_procurement,
    ):
        self.required_advisors = required_advisors
        self.critic = critic
        """Critic 56검사. `None` 이면 **돌리지 않은 사실이 `skipped` 에 남는다.**"""

    def __call__(
        self,
        proposal: Mapping[str, Any],
        constraints: Mapping[AgentName, Mapping[str, Any]],
        verdicts: Mapping[AgentName, Mapping[str, Any]],
        plan: ExecutionPlan,
        context: VerificationContext | None = None,
    ) -> VerificationResult:
        """★ 시나리오 배열이 아니라 **제안 전체**를 받는다 (2026-08-27 매입 스키마 확인).

        `allowed_axes` · `situation` · `confidence` 는 `scenarios[]` 안이 아니라
        **제안 최상위**에 있다(`PurchaseProposal`). 배열만 받으면 그 판정들을 볼 수 없다.

        ★ `context` 는 Critic 에 넘길 때만 쓴다 — `as_of` · 품목 · 조언자 Evidence.
          주지 않으면 Critic 을 돌리지 않고 그 사실을 `skipped` 에 남긴다.
        """
        checks = check_proposal(proposal, constraints, verdicts, plan, self.required_advisors)
        findings = list(checks.result.findings)
        concerns = list(checks.result.concerns)
        skipped = list(checks.result.skipped)

        self._run_critic(
            proposal, constraints, context, checks.identity_broken, findings, concerns, skipped
        )
        # 아직 붙지 않은 검사는 Critic 결과 **뒤에** 드러낸다(규칙부의 `UNCOVERED_CHECKS`).
        skipped.extend(UNCOVERED_CHECKS)

        return VerificationResult(tuple(findings), tuple(concerns), tuple(skipped))

    # ── Critic 56검사 (§3.7.1) ──────────────────────────────────

    def _run_critic(
        self,
        proposal: Mapping[str, Any],
        constraints: Mapping[AgentName, Mapping[str, Any]],
        context: VerificationContext | None,
        identity_broken: bool,
        findings: list[str],
        concerns: list[str],
        skipped: list[str],
    ) -> None:
        """★ **항등식이 깨졌으면 돌리지 않는다.**

        Critic 의 금액 축은 `qty × unit_price` 로 다시 만들어지고, 그 단가는
        `total_amount_krw / total_qty_kg` 에서 온다 — **두 값이 서로 맞을 때만** 뜻이
        있는 표현이다. 어긋난 숫자 위에서 돌린 56검사는 **그럴듯한 통과**를 만든다.
        그건 검증이 아니라 검증처럼 보이는 것이다.

        ★ Critic 이 던지는 예외를 위로 올리지 않는다. 검증 Tool 이 죽으면 Flow 가
          통째로 `ERROR` 가 되는데, **검증 실패는 매입 판단의 실패가 아니다.**
        """
        if self.critic is None:
            skipped.append("Critic L0~L5 (56검사): 검증 Tool 에 주입되지 않음")
            return
        if context is None:
            skipped.append("Critic L0~L5 (56검사): 실행 맥락 미전달 — as_of · 품목 · 근거 없음")
            return
        if identity_broken:
            skipped.append(
                "Critic L0~L5 (56검사): 시나리오 항등식이 깨져 돌리지 않았다 — "
                "어긋난 숫자 위의 판정은 통과해도 뜻이 없다"
            )
            return

        try:
            request = build_request(
                as_of=context.as_of,
                item=context.item,
                proposal=proposal,
                constraints=constraints,
                evidences=context.evidences,
                observations=context.observations,
            )
            verdict = self.critic(request)
        except CriticSkipped as exc:
            skipped.append(f"Critic L0~L5 (56검사): {exc}")
            return
        except ValidationError as exc:
            # ★ 입력이 Critic 계약에 안 맞는 것은 **검증 Tool 의 고장이 아니다.**
            #   매입이 허용 목록 밖 어휘를 내면 여기서 걸린다(예: strategy_type).
            #   어느 필드인지 적어 `skipped` 로 남긴다 — 통과로 치지 않는다.
            fields = " · ".join(
                ".".join(str(part) for part in err["loc"]) for err in exc.errors()[:5]
            )
            skipped.append(f"Critic L0~L5 (56검사): 입력이 Critic 계약에 맞지 않는다 — {fields}")
            return
        except Exception as exc:  # noqa: BLE001 — 검증이 죽어도 Flow 는 살아야 한다
            concerns.append(f"CRITIC: 검증 Tool 이 돌지 못했다 — {type(exc).__name__}: {exc}")
            skipped.append("Critic L0~L5 (56검사): 실행 중 오류로 미판정")
            return

        critic_findings, critic_concerns, critic_skipped = fold(verdict)
        findings.extend(critic_findings)
        concerns.extend(critic_concerns)
        skipped.extend(critic_skipped)


# ===========================================================================
# 판매 (사이클 B) — 판단 경로에서 Critic 을 지나간다
# ===========================================================================
#
# 🔴 **실측 2026-09-08 — 판매는 자기 Critic 을 안 불렀다.**
#
#   ```text
#   매입(A)   verifier → critic_bridge → run_critic_procurement   판단 안에서 돈다
#   판매(B)   run_critic_sales ← critic/router.py 에서만          HTTP 로만 불렸다
#   ```
#
#   그래서 *"두 시나리오 다 critic 검증을 거친다"* 가 사실이 아니었다. 여기서 그
#   자리를 세운다 — 매입과 **같은 모양**(Protocol + 기본값 + 갈아끼우기)이다.
#
# ⚠️ **온전한 판정은 아직 안 난다.** `inventory_allocations` ·
#   `inventory_reservations` 가 0행이라 `allocation` · `lot_constraints` 재료가
#   없다(물류 답 대기). 그 사실은 `CriticSkipped` 문장으로 `skipped` 에 남는다 —
#   **통과로 접지 않는다.**


@dataclass(frozen=True)
class SalesVerificationContext:
    """판매 판정을 Critic 에 넘기려면 필요한 것.

    ★ `VerificationContext`(매입) 와 **따로 둔다.** 매입은 조언자 cap 과 근거가 필요하고
      판매는 후보 배분과 물류 sellable 컨텍스트가 필요하다 — 한 dataclass 에 둘을 담으면
      어느 사이클이 무엇을 쓰는지가 필드 목록에서 사라진다.
    """

    as_of: date
    item: str | None = None

    #: 판매가 낸 후보 그대로. **마스터는 고르지도 재계산하지도 않는다** (§3.2.2).
    candidates: tuple[Mapping[str, Any], ...] = ()

    #: 물류 sellable 컨텍스트 회신 봉투(`sales_flow._verdict_of`). 로트·가용재고·창고
    #: 여유가 그 안 `payload` 에 있다. **여기서 벗기지 않는다** — 번역은 bridge 몫이다.
    supply_context: Mapping[str, Any] = field(default_factory=dict)


class SalesVerifierPort(Protocol):
    """판매 판단이 지나가는 검증 자리 (매입 `flow.VerifierPort` 와 같은 역할).

    ★ 주입하지 않으면 **기본 검증 Tool 이 붙는다** — 매입 진입점이 `MasterVerifier()`
      를 세운 것과 같다. 끄려면 명시적으로 꺼야 한다.
    """

    def __call__(self, context: SalesVerificationContext | None = None) -> VerificationResult: ...


class SalesVerifier:
    """판매 판단의 검증 Tool. **Critic B 를 부르는 자리다.**

    ★ 매입 `MasterVerifier` 와 합치지 않는다. 저쪽은 제안·조언자 경계·실행 계획을
      함께 보는 네 갈래 검사이고 이쪽은 지금 Critic 한 갈래다. 합치면 매입 검사가
      판매 입력 위에서 돌게 되고, 그때 나오는 것은 검증처럼 보이는 것이다.
    """

    def __init__(self, critic: SalesCriticPort | None = run_critic_sales):
        self.critic = critic
        """Critic B. `None` 이면 **돌리지 않은 사실이 `skipped` 에 남는다.**

        🔴 `None` 은 *"Critic 을 안 돌렸다"* 는 뜻이라 **기본값 자리에 못 쓴다.**
          기본값은 `run_critic_sales` **자체**다 — 매입이 `run_critic_procurement`
          를 기본값으로 세운 것과 같은 규율이고, 그 이력은 이 파일 머리에 있다.
        """

    def __call__(self, context: SalesVerificationContext | None = None) -> VerificationResult:
        """판매 판정을 Critic B 에 넘기고 결과를 3단으로 돌려준다.

        🔴 **세 값을 섞지 않는다** (§3.7.6).

        ```text
        판정이 났다          findings / concerns 에 Critic 이 낸 것이 담긴다
        재료가 없어 못 냈다   skipped 에 CriticSkipped 문장이 남는다
        부르다 실패했다       concerns 에 CRITIC 오류 + skipped 에 미판정
        ```
        """
        findings: list[str] = []
        concerns: list[str] = []
        skipped: list[str] = []

        self._run_critic(context, findings, concerns, skipped)
        return VerificationResult(tuple(findings), tuple(concerns), tuple(skipped))

    def _run_critic(
        self,
        context: SalesVerificationContext | None,
        findings: list[str],
        concerns: list[str],
        skipped: list[str],
    ) -> None:
        """★ Critic 이 던지는 예외를 위로 올리지 않는다. 검증 Tool 이 죽으면 판매
        판단이 통째로 `ERROR` 가 되는데, **검증 실패는 판매 판단의 실패가 아니다.**
        매입 `MasterVerifier._run_critic` 과 같은 태도이고 같은 낱말을 쓴다.
        """
        if self.critic is None:
            skipped.append("Critic B (판매): 검증 Tool 에 주입되지 않음")
            return
        if context is None:
            skipped.append("Critic B (판매): 실행 맥락 미전달 — as_of · 품목 · 후보 없음")
            return

        try:
            request = build_sales_request(
                as_of=context.as_of,
                item=context.item,
                candidates=context.candidates,
                supply_context=context.supply_context,
            )
            verdict = self.critic(request)
        except CriticSkipped as exc:
            skipped.append(f"Critic B (판매): {exc}")
            return
        except ValidationError as exc:
            # ★ 매입과 같은 자리다 — 입력이 Critic 계약에 안 맞는 것은 **검증 Tool 의
            #   고장이 아니다.** 어느 필드인지 적어 `skipped` 로 남긴다.
            fields = " · ".join(
                ".".join(str(part) for part in err["loc"]) for err in exc.errors()[:5]
            )
            skipped.append(f"Critic B (판매): 입력이 Critic 계약에 맞지 않는다 — {fields}")
            return
        except Exception as exc:  # noqa: BLE001 — 검증이 죽어도 판매 판단은 살아야 한다
            concerns.append(f"CRITIC: 검증 Tool 이 돌지 못했다 — {type(exc).__name__}: {exc}")
            skipped.append("Critic B (판매): 실행 중 오류로 미판정")
            return

        critic_findings, critic_concerns, critic_skipped = fold(verdict)
        findings.extend(critic_findings)
        concerns.extend(critic_concerns)
        skipped.extend(critic_skipped)
