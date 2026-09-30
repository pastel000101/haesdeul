"""결정 접수 — 실행 이력을 읽고, 규칙을 걸고, 적재한다.

★ `service.py` 와 나눈 이유 — 저쪽은 **Flow 실행**의 경계 변환이고 여기는 **사람의
  결정**이다. 한 파일에 두면 `run_procurement` 에서 결정 함수를 부르기가 쉬워지는데,
  그 순간 승인 게이트가 툴 안으로 들어온다.

★ 2026-09-30 재구성 BL-018: `master/decision_service.py` 에서 옮겼다. 역할이 다른 부분은 갈랐다 —
  `domain/decision.py`; `readmodel/approvals.py`; `schemas/approval.py`. 무엇이 어디로 갔는지는
  설계서 대응표 `master/` 절. 함께 모은 것: `master/decision_repository.py` 의 `save_decision`,
  `link_follow_up`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID, uuid4

from app.contracts.commitment import ApprovedCommitment
from app.core import db as core_db
from app.core.settings import get_db_schema
from app.master.domain.decision import (
    PROCUREMENT_CYCLE,
    SALES_CYCLE,
    as_of_of,
    available_scenario_names,
    awaits_purchase_record,
    check_decidable,
    check_scenario_exists,
    commitment_parts,
    cycle_of,
    end_code_of,
    next_seq,
    policy_version_of,
    reject_repeat_approval,
    run_sim_run_id_of,
)
from app.master.domain.revalidation import conditions_of_original, find_scenario
from app.master.domain.sales_approval import financial_summary_of
from app.master.readmodel.approvals import run_for
from app.master.readmodel.decisions import list_decisions
from app.master.repository.decisions import insert_decision, row_to_out, update_follow_up
from app.master.schemas.approval import CurrentApproval
from app.master.schemas.decision import Decision, DecisionIn, DecisionOut, RevalidationOutcome
from app.master.schemas.revalidation import Revalidation
from app.master.schemas.sales_approval import SaleConfirmationOut
from app.master.schemas.transition import TransitionOut
from app.master.service.revalidation import revalidate_procurement_scenario, revalidate_scenario
from app.master.service.sales_approval import confirm_approved_sale
from app.master.service.transition import apply_approval


def record_decision(request_id: str, payload: DecisionIn) -> DecisionOut:
    """결정 1건을 받아 적재하고 돌려준다.

    순서가 중요하다 — **읽고 → 검사하고 → 🔴 재검증하고 → 적재한다.** 적재 후
    검사하면 잘못된 결정이 이력에 남고, **재검증 뒤에 적재해야 그 결과가 같은 행에
    담긴다** (설계 2026-09-07 §1).

    🔴 **재검증이 막혀도 결정 행은 쓴다** (설계 §0). `decision=APPROVE` 는 **사용자가
      누른 사실**이고, `revalidation_outcome` 이 그 옆에 결과를 적는다.

      ```text
      결정 행       항상 쓴다        무엇을 눌렀나
      승인의 효력   PASSED 일 때만    도메인 Write 로 흘러가는 것 (M-5)
      ```

      막혔다고 행을 안 쓰면 *"승인하려다 막혔다"* 가 사라지고, 그것은 M-3 이 칸을
      나눈 이유를 되돌리는 것이다.

    ⚠️ **`PASSED` 여도 아직 아무 일도 안 일어난다.** 승인 후 도메인 Write 는 M-5 이고
      그 앞에 실행 원장(saga) 문제가 있다 (설계 §5).

    🔴 **`as_of` 는 여기서 안 만든다** (2026-09-09 · `#452`). 재검증이 설 날이고,
      **진입점이 정해서 넘긴다** — 운영은 `router.master_decide` 가 `clock` 을 읽고,
      발화문 경로는 `ask_service` 가 그 요청의 `as_of` 를 넘긴다. 이 함수가 시계를
      읽으면 걷기가 승인 경로를 타는 날 곡선에 벽시계가 섞인다.

      ⚠️ **기본값을 두지 않는다.** 안 넘기면 터져야 한다 — 기본값은 곧 업무 규칙이다.

    ⚠️ **승인이 아니어도 받는다.** `REJECT_ALL` · `REQUEST_CHANGE` · `CANCEL` 은
      재검증을 안 돌리므로 이 값을 안 쓰지만, 넘기고 안 넘기고가 결정 종류에 따라
      갈리면 **부르는 쪽이 종류를 먼저 알아야 한다.** 받는 것은 항상 같다.

    :param as_of: 이 결정이 서는 날. 승인이면 재검증이 그날로 돈다.
    :raises LookupError: 그 업무 키의 실행이 없다 (라우터가 404).
    :raises DecisionRejected: 지금 상태에서 받을 수 없다 (라우터가 409/422).
    """
    row = run_for(request_id, payload.history_run_id)  # 없으면 LookupError
    response_payload = dict(row.get("response_payload") or {})

    # 🔴 **어느 어휘로 검사할지는 실행 행이 정한다** (2026-09-08 계약). `payload` 에서
    #    읽지 않는다 — 읽으면 매입 실행을 판매라고 우겨 `SL1_PRESENTED` 어휘로
    #    검사받을 수 있고, 그 순간 승인 게이트가 부르는 쪽 손에 들어간다.
    cycle = cycle_of(row)

    end_code = end_code_of(response_payload)
    check_decidable(end_code, payload.decision, cycle=cycle)
    check_scenario_exists(payload.scenario_label, available_scenario_names(response_payload, cycle))

    existing = list_decisions(request_id)
    reject_repeat_approval(existing, payload)

    seq = next_seq(existing)

    # 🔴 **축을 여기서 한 번만 읽는다** (2026-09-11). 재검증 · 판매 확정 · 상태전이
    #    셋이 각자 읽으면 언젠가 갈리고, 갈리는 날 **재검증은 이 실행을 보고 확정은
    #    다른 실행에 쓴다.** 한 번 읽어 셋에 흘린다.
    sim_run_id = run_sim_run_id_of(row)

    revalidation = _revalidation_for(row, response_payload, payload, seq, sim_run_id=sim_run_id)
    saved = save_decision(
        request_id=request_id,
        decision_seq=seq,
        decision=payload.decision,
        decided_by=payload.decided_by,
        end_code_at_decision=end_code,
        scenario_label=payload.scenario_label,
        condition_text=payload.condition_text,
        history_run_id=str(row["run_id"]),
        revalidation_request_id=None if revalidation is None else revalidation.request_id,
        revalidation_outcome=None if revalidation is None else revalidation.outcome,
        note=payload.note,
    )
    if cycle == SALES_CYCLE:
        # 🔴 **매입 약정을 만들지 않는다.** `commitment_parts` 는
        #    `response_payload["scenarios"]` 만 보므로 판매 응답에서는 늘
        #    `buildable=False` 를 낸다 — *"판매를 확정했다"* 자리에 *"매입 약정을 못
        #    만들었다"* 가 실린다.
        return saved.model_copy(
            update={
                "sale": _sale_for(
                    request_id,
                    row,
                    response_payload,
                    payload,
                    revalidation,
                    sim_run_id=sim_run_id,
                )
            }
        )
    out, commitment = commitment_parts(request_id, seq, payload, response_payload)
    # 🔴 **사람 승인은 전이를 부르지 않는다** (설계 260915 안 A §4-2). 선정만 적고,
    #    실매입을 기록하는 순간 그 값으로 전이가 선다 (`purchase_record.record_purchase`).
    #    자동 승인(`AUTO-BACKFILL`)은 지금 그대로 승인 즉시 계획값으로 전이한다.
    if commitment is not None and awaits_purchase_record(payload.decided_by):
        transition = _awaiting_purchase_record()
    else:
        # 🔴 **축은 실행 행에서 온다.** 여기서 상수를 읽지 않는다 —
        #    `run_sim_run_id_of` 가 왜인지를 적었다.
        transition = _transition_for(commitment, sim_run_id=sim_run_id)
    return saved.model_copy(update={"commitment": out, "transition": transition})


def _awaiting_purchase_record() -> TransitionOut:
    """사람 승인의 전이 자리. **전이를 부르지 않았다**는 사실을 값으로 싣는다.

    ★ `None` 으로 비우지 않는다 — `None` 은 *"반영할 약정이 없다"* 이고, 이쪽은
      약정이 섰는데 **기록을 기다린다**이다.
    """
    return TransitionOut(status="AWAITING_PURCHASE_RECORD", reason="실매입을 기록하면 반영됩니다")


def _sale_for(
    request_id: str,
    row: Mapping[str, Any],
    response_payload: Mapping[str, Any],
    payload: DecisionIn,
    revalidation: Revalidation | None,
    *,
    sim_run_id: str | None,
) -> SaleConfirmationOut | None:
    """승인이면 **판매를 확정한다** (`confirm_sale`).

    ★ **승인이 아니면 `None`** 이다 — 거절·조건부 재요청·취소에는 확정할 것이 없고,
      그때의 `None` 은 *"확정에 실패했다"* 가 아니다 (`_transition_for` 와 같은 태도).

    🔴 **재검증 결과를 여기서 다시 재지 않는다.** `_revalidation_for` 가 낸 것을
      그대로 넘긴다 — 두 곳에서 판정하면 *"재검증은 막혔는데 확정은 됐다"* 가 가능해진다.

    🔴 **기여이익도 그 재검증에서 온다** (2026-09-11). `financial_summary_of` 가
      `Revalidation.validations` 에서 꺼낸다.

      ★★ **첫 검증(`response_payload["candidates"][].validations`) 에서 읽지
        않는다.** 확정은 재검증 **뒤에** 서므로 재검증이 그날 사실로 다시 센 값이
        정본이다 — 첫 검증 값을 쓰면 *"제안 시점 사실"* 로 장부가 서고, 그 사이
        재고·원가가 움직인 것이 사라진다.

      ★ **세 겹을 여기서 파고들지 않는다.** 그 매핑의 주인은 `sales_approval` 이다.

    ★ **`as_of` 는 그 실행의 날이다** (`as_of_of`). 벽시계를 읽지 않는다 —
      `order_date` 가 되고, 그것이 곧 수금 곡선의 시점이다.

    🔴 **축도 여기서 읽지 않는다** (2026-09-11). 부르는 쪽이 **재검증에 넘긴 것과
      같은 한 값**을 넘긴다. 각자 읽으면 갈리는 날이 오고, 그날 재검증은 이 실행을
      보고 확정은 다른 실행의 `sales` 에 쓴다.

      ⚠️ **못 읽었으면 확정을 안 한다.** 이 자리까지 오려면 재검증이 `PASSED` 여야
        하는데, 축을 못 읽으면 재검증이 `ERROR` 라 애초에 못 온다. 그래도 막아 둔다 —
        그 순서에 기대면 순서가 바뀌는 날 번인 장부에 없는 판매가 쌓인다.

    :param sim_run_id: 원 실행 이력 행이 실은 축. 🔴 **여기서 짓지 않는다.**
    """
    if payload.decision != "APPROVE" or payload.scenario_label is None:
        return None
    scenario = find_scenario(response_payload, payload.scenario_label)
    if scenario is None:
        return SaleConfirmationOut(
            status="BLOCKED",
            reason=f"승인한 안 '{payload.scenario_label}' 을 원 실행에서 유일하게 찾지 못했다.",
        )
    if sim_run_id is None:
        return SaleConfirmationOut(
            status="BLOCKED",
            reason="원 실행의 sim_run_id 를 못 읽어 어느 장부에 확정할지 정할 수 없다.",
        )
    return confirm_approved_sale(
        request_id=request_id,
        run_id=str(row["run_id"]),
        as_of=as_of_of(response_payload),
        policy_version=policy_version_of(row),
        scenario=scenario,
        revalidation_outcome=None if revalidation is None else revalidation.outcome,
        financial_summary=(
            None if revalidation is None else financial_summary_of(revalidation.validations)
        ),
        sim_run_id=sim_run_id,
    )


def _revalidation_for(
    row: Mapping[str, Any],
    response_payload: Mapping[str, Any],
    payload: DecisionIn,
    decision_seq: int,
    *,
    sim_run_id: str | None,
) -> Revalidation | None:
    """승인이면 **선택된 1안을 `as_of` 로 다시 검증한다** (설계 2026-09-07 · M-4).

    ★ **`as_of` 를 만들지 않고 흘린다.** 받은 날을 그대로 `revalidate_scenario` 에
      넘긴다 — 중간에서 손대면 진입점이 정한 날과 부서가 받은 날이 갈린다.

    ★ **`sim_run_id` 도 같다** (2026-09-11). 실행 이력 행에서 읽은 값을 흘린다.
      🔴 **여기서 읽지 않는다** — `record_decision` 이 한 번 읽어 재검증 · 판매 확정 ·
      상태전이 셋에 같은 값을 준다. 각자 읽으면 언젠가 갈린다.

    🔴 **`APPROVE` 일 때만이다.** `REJECT_ALL` · `REQUEST_CHANGE` · `CANCEL` 은 승인이
      아니라 재검증할 대상이 없다 — 그때 두 칸은 `None` 이고, 그 `None` 은 *"재검증에
      실패했다"* 가 아니라 **"재검증을 하지 않았다"** 이다.

    ★ **`commitment_parts` 앞에서 돈다.** 약정은 결정을 적은 **뒤에** 만들지만
      재검증은 **적기 전에** 돌아야 한다 — 결과가 같은 행에 담기기 때문이다.

    :returns: 승인이 아니면 `None`. 승인인데 못 돌렸으면 `ERROR` 가 실린 `Revalidation`
              이다 — 둘을 섞지 않는다 (§1.2-10).
    """
    if payload.decision != "APPROVE" or payload.scenario_label is None:
        return None

    scenario = find_scenario(response_payload, payload.scenario_label)
    if scenario is None:
        # ★ `check_scenario_exists` 를 이미 지났는데도 못 찾는 경우가 있다 — 라벨이
        #   겹치면 유일하지 않아 `None` 이 온다. 어느 안을 재검증했는지가 운에 걸리는
        #   것보다 **못 돌렸다고 적는 편**이 낫다 (`commitment_parts` 와 같은 판단).
        return Revalidation(
            outcome="ERROR",
            reason=f"승인한 안 '{payload.scenario_label}' 을 원 실행에서 유일하게 찾지 못했다.",
        )
    return _revalidate_scenario_of(
        row,
        response_payload,
        payload.scenario_label,
        scenario,
        decision_seq,
        sim_run_id=sim_run_id,
    )


def revalidate_recorded(approval: CurrentApproval, scenario: Mapping[str, Any]) -> Revalidation:
    """실매입 기록값으로 바꾼 **안 사본**을 승인 때와 같은 재검증에 태운다.

    ★ **재검증 경로를 새로 만들지 않는다** (설계 260915 안 A §4-6 ①). 승인 재검증과
      같은 함수(`_revalidate_scenario_of`)를 지나고, 다른 것은 넘기는 안 하나뿐이다 —
      기록값이 재무 Cap · 현금흐름을 우회하지 못하게 하려는 것이다.

    ★ 조건 비교의 기준(`original_conditions`)은 **원 실행의 그 안**이다. 기록값이 새
      조건을 붙이면 승인 때와 같이 `CONDITIONAL` 로 잡힌다.
    """
    return _revalidate_scenario_of(
        approval.run_row,
        approval.response_payload,
        approval.decision.scenario_label or "",
        scenario,
        approval.decision.decision_seq,
        sim_run_id=approval.sim_run_id,
    )


def _revalidate_scenario_of(
    row: Mapping[str, Any],
    response_payload: Mapping[str, Any],
    scenario_label: str,
    scenario: Mapping[str, Any],
    decision_seq: int,
    *,
    sim_run_id: str | None,
) -> Revalidation:
    """안 하나를 **그 실행의 날 · 정책판 · 축**으로 재검증한다 (승인 · 실매입 기록 공용).

    ★ 2026-09-15 에 `_revalidation_for` 의 뒷부분을 떼어 냈다. 실매입 기록이 **같은
      문**을 지나야 하는데, 두 벌로 두면 한쪽만 기준이 바뀌는 날이 온다.
    """
    # 🔴 **재검증이 설 날은 그 실행의 날이다** (2026-09-09 · 마스터 판단).
    #    부르는 쪽에서 받지 않는다 — 받으면 화면이든 걷기든 아무 날이나 넣을 수 있고,
    #    그 순간 재검증이 자기가 언제 도는지를 남에게 맡기게 된다. 실행 행이 정한다.
    #
    #    ⚠️ 벽시계를 읽던 것을 진입점으로 올렸다가(#452) 여기까지 내렸다. 이유는
    #      실측이다 — 2026-01-20 안을 오늘 승인하면 재검증이 **오늘** 로 개장을 묻고,
    #      오늘은 안 열린 날이라 `ERROR` 가 나 승인이 막혔다.
    as_of = as_of_of(response_payload)
    if as_of is None:
        return Revalidation(
            outcome="ERROR",
            reason="원 실행의 기준일을 못 읽어 재검증할 날을 정할 수 없다.",
        )

    policy_version = policy_version_of(row)
    if policy_version is None:
        # 🔴 **정책판 없이 봉투를 만들 수 없다** (`ExecutionContext` 가 막는다). 아무
        #   값이나 채워 넣으면 재현 4종의 하나가 거짓이 된다 (§3.2.4).
        return Revalidation(
            outcome="ERROR",
            reason="원 실행의 policy_version 을 못 읽어 재검증 봉투를 만들 수 없다.",
        )

    if sim_run_id is None:
        # 🔴 **여기서 메우지 않는다** (2026-09-11). 전에는 재검증이 축을
        #   `BURN_IN_SIM_RUN_ID` 로 박아, 판매를 한 번도 안 한 실행의 승인이 번인
        #   장부의 채권·현금에 걸려 통째로 `FAILED` 로 떨어졌다. 못 읽으면 못 읽었다고
        #   적는 편이 남의 실행 장부로 판정하는 것보다 낫다.
        return Revalidation(
            outcome="ERROR",
            reason="원 실행의 sim_run_id 를 못 읽어 재검증 봉투를 만들 수 없다.",
        )

    item = row.get("item") if isinstance(row.get("item"), str) else None
    original_conditions = conditions_of_original(response_payload, scenario_label)

    # 🔴 **매입 안은 매입의 물음으로 다시 묻는다** (2026-09-16 실측). 판매 경로
    #    (`revalidate_scenario`)로 보내면 재무 `SALES_VALIDATION` 이 판매 사실을 못 찾아
    #    `READY/skipped` 로 답하고, 매입 재검증이 늘 `FAILED` · `cycle=SALES` 로 남는다.
    #
    #    ★ **사이클은 실행 행이 정한다** (`cycle_of`). 안의 모양으로 짐작하지 않는다.
    if cycle_of(row) == PROCUREMENT_CYCLE:
        proposal = response_payload.get("judgment")
        if not isinstance(proposal, Mapping) or not proposal:
            # 🔴 **제안 최상위 없이 안만 보내지 않는다.** 재무 · 물류가 `meta.as_of` ·
            #   `meta.item` 을 거기서 읽는다 — 빠지면 판정 대신 입력 오류가 온다.
            return Revalidation(
                outcome="ERROR",
                reason="원 실행의 매입 제안(judgment)을 못 읽어 재검증 요청을 만들 수 없다.",
            )
        return revalidate_procurement_scenario(
            scenario=scenario,
            proposal=proposal,
            original_conditions=original_conditions,
            decision_seq=decision_seq,
            policy_version=policy_version,
            as_of=as_of,
            sim_run_id=sim_run_id,
            item=item,
        )

    return revalidate_scenario(
        scenario=scenario,
        original_conditions=original_conditions,
        decision_seq=decision_seq,
        policy_version=policy_version,
        as_of=as_of,
        sim_run_id=sim_run_id,
        item=item,
    )


def _transition_for(
    commitment: ApprovedCommitment | None, *, sim_run_id: str | None
) -> TransitionOut | None:
    """약정이 섰으면 그것을 재무·물류 장부에 반영한다 (C 형태 ⑦).

    ★ **약정이 없으면 부르지 않는다.** 승인이 아니거나 약정을 못 만든 날에는 반영할
      사실 자체가 없다 — 그때의 `None` 은 *"전이가 실패했다"* 가 아니다.

    ★ **여기서도 결정을 죽이지 않는다.** `apply_approval` 은 예외를 밖으로 내지
      않고 `FAILED` 를 값으로 돌려준다. 적재된 결정이 전이 실패로 지워지면,
      사람이 승인한 사실이 사라진다.

    :param sim_run_id: 실행 이력 행이 실은 축. 🔴 **여기서 짓지 않는다.**
    """
    if commitment is None:
        return None
    return apply_approval(commitment, sim_run_id=sim_run_id)


def save_decision(
    *,
    request_id: str,
    decision_seq: int,
    decision: Decision,
    decided_by: str,
    end_code_at_decision: str,
    scenario_label: str | None = None,
    condition_text: str | None = None,
    follow_up_request_id: str | None = None,
    history_run_id: str | None = None,
    revalidation_request_id: str | None = None,
    revalidation_outcome: RevalidationOutcome | None = None,
    note: str | None = None,
) -> DecisionOut:
    """결정 1건을 적재한다.

    ★ `UNIQUE (request_id, decision_seq)` 가 동시 결정을 막는다. 두 사람이 같은 회차로
      동시에 밀면 뒤엣것이 `UniqueViolation` 으로 떨어진다 — **조용히 덮어쓰지 않는다.**
      호출자가 회차를 다시 읽어 재시도할지 정한다.

    ★ 재검증 두 칸은 **기본이 `None`** 이다. 승인이 아닌 결정에는 재검증할 대상이
      없고, 2026-09-07 이전 결정에는 절차 자체가 없었다 — 여기서 NULL 은
      *"재검증을 하지 않았다"* 이지 실패가 아니다.

    ⚠️ **`ERROR` 만 키 없이 들어올 수 있다** (짝 CHECK
      `master_decisions_revalidation_pairing`). *"돌리지 못한"* 재검증에는 가리킬
      실행이 없다 — 나머지 셋은 반드시 키가 같이 온다.

    ⚠️ `revalidation_request_id` 를 `follow_up_request_id` 자리에 넘기지 않는다.
      DB 도 두 칸으로 갈라 두었다 (`master/master_decision_revalidation.sql`).
    """
    # ★ 종전 `execute_returning_one` 과 같은 경계 — 결정 1건 = 연결 하나 · 트랜잭션 하나.
    #   전이(`apply_approval`)와 다른 트랜잭션이라 전이가 실패해도 결정은 남는다.
    schema = get_db_schema()
    with core_db.connection() as conn, core_db.transaction(conn):
        row = insert_decision(
            conn,
            schema=schema,
            decision_id=uuid4(),
            request_id=request_id,
            decision_seq=decision_seq,
            decision=decision,
            decided_by=decided_by,
            end_code_at_decision=end_code_at_decision,
            scenario_label=scenario_label,
            condition_text=condition_text,
            follow_up_request_id=follow_up_request_id,
            history_run_id=history_run_id,
            revalidation_request_id=revalidation_request_id,
            revalidation_outcome=revalidation_outcome,
            note=note,
        )
    out = row_to_out(dict(row))
    return out.model_copy(update={"is_current": True})


def link_follow_up(*, decision_id: UUID, follow_up_request_id: str) -> bool:
    """조건부 재요청이 실제로 실행됐을 때 후속 키를 잇는다.

    ⚠️ **이 한 곳만 UPDATE 한다.** 결정 내용(무엇을 골랐나)은 안 건드리고 NULL 이던
      후속 링크만 채운다. `IS NULL` 조건이 **한 번만** 채워지게 한다 — 이미 이어진
      결정에 다른 실행을 덧붙이면 체인이 갈라진다.

    :return: 실제로 이었으면 True. 이미 이어져 있었으면 False — **예외가 아니다.**
      호출자가 "이 결정은 이미 후속이 있다"를 판단할 몫이다.
    """
    # ★ 종전 `fetch_one` 헬퍼 그대로 **조회 연결(autocommit)** 에서 한 문장으로 끝난다 — UPDATE 가
    #   그 문장으로 바로 확정된다. 트랜잭션 블록으로 바꾸지 않는다(경계 유지 · 2026-09-30 BL-018).
    schema = get_db_schema()
    with core_db.read_connection() as conn:
        return update_follow_up(
            conn,
            decision_id=decision_id,
            follow_up_request_id=follow_up_request_id,
            schema=schema,
        )
