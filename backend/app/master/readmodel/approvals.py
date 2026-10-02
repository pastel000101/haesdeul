"""현재 승인 조회 — 결정 · 실행 이력을 읽어 현재 승인과 약정을 조립한다."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any
from uuid import UUID

from app.contracts.commitment import ApprovedCommitment, CommitmentNotBuildable
from app.core import db as core_db
from app.core.settings import get_db_schema
from app.master.domain.commitment import RecordedLeg, with_purchase_record
from app.master.domain.decision import (
    PROCUREMENT_CYCLE,
    as_of_of,
    awaits_purchase_record,
    commitment_parts,
    cycle_of,
    payment_days_of,
    run_sim_run_id_of,
)
from app.master.readmodel.decisions import list_decisions
from app.master.readmodel.runs import get_run, get_run_by_request_id, list_runs
from app.master.repository.purchase_records import select_purchase_record_legs
from app.master.schemas.approval import CurrentApproval
from app.master.schemas.decision import CommitmentOut, DecisionIn, DecisionRejected


def run_for(request_id: str, history_run_id: str | None) -> dict[str, Any]:
    """결정이 걸릴 실행 한 건을 고른다.

    화면이 본 실행으로 검사한다. `history_run_id` 를 주면 그 행을 읽고, 종료 코드도
    시나리오 라벨도 그 실행 것을 쓴다. 최신 실행으로 검사하면 "사람이 본 안" 과
    "검사한 안" 이 갈린다 — 라벨이 같아 눈에 안 띈다.

    막지 않고 드러낸다. 그 사이 재실행이 있어 최신이 아니게 됐어도 거절하지 않는다.
    사람이 그 실행을 보고 결정한 것은 사실이고, 그 사실을 그대로 적는 것이 이 표의
    일이다 (8/26 회의: 승인 게이트를 마스터가 들지 않는다). 낡았다는 것은 `run_id` 가
    최신 행과 다르다는 사실로 이미 드러난다.

    안 주면 최신을 고른다. 다른 클라이언트가 깨지지 않게 하려는 것이고, 그 경우
    경합이 남는다 — 화면은 반드시 실어 보내야 한다.

    :raises DecisionRejected: 준 실행이 이 업무 키의 것이 아니다 (422).
    """
    if history_run_id is None:
        # 승인 대상은 매입 실행이다. 조회는 승인할 수 없다.
        # 어휘를 여기서 다시 적지 않는다 — 주인은 `decision.PROCUREMENT_CYCLE` 이다.
        return dict(get_run_by_request_id(request_id, cycle=PROCUREMENT_CYCLE))
    try:
        run = dict(get_run(UUID(history_run_id)))
    except ValueError as exc:  # UUID 파싱 실패
        raise DecisionRejected(f"실행 id 형식이 아니다: {history_run_id}") from exc
    if run.get("request_id") != request_id:
        # DB 의 복합 FK 가 이것을 최종적으로 막지만, 여기서 잡아야 이유를 돌려준다.
        raise DecisionRejected(
            f"실행 {history_run_id} 는 업무 키 {request_id} 의 것이 아니다 "
            f"(그 실행의 업무 키: {run.get('request_id')})."
        )
    return run


def current_commitment(request_id: str) -> CommitmentOut | None:
    """현재 유효한 승인이 만든 확정 입고 약정 (H1 · `GET /runs/{id}/commitment`).

    물류 회신이 전달 방식 ⓐ(GET)에 동의해 열었다. 승인 응답을 놓친 소비자가 약정을
    다시 볼 유일한 길이다 — 약정 적재는 계약이 굳은 뒤로 미뤘다.

    결정 시점의 실행으로 재조립한다. 약정을 저장해 두지 않으므로, 현재 결정이
    가리키는 실행(`history_run_id`)을 읽어 같은 함수로 다시 만든다 — 승인 응답에
    실렸던 것과 같은 값이 나온다 (같은 입력·같은 코드).

    번복은 여기서 저절로 반영된다 — `is_current` 인 결정 하나만 보므로, 앞 승인의
    약정은 이 경로에서 사라진다. 앞 약정을 이미 받아 간 소비자에게 취소를 알리는
    일은 이 경로가 못 한다 (전달 계약 미결 — H1 리뷰 §3).

    :returns: 승인이 없으면 `None` — 라우터가 404 로 접는다. 승인인데 못 만들면
              `buildable=False` 와 사유가 실린다. 둘을 섞지 않는다 (§1.2-10).
    """
    return _current_approval_parts(request_id)[0]


def current_approved_commitment(request_id: str) -> ApprovedCommitment | None:
    """현재 유효한 승인이 만든 약정 객체. 승인이 없거나 못 만들면 `None`.

    `current_commitment` 과 같은 재조립을 쓴다. 미적용 전이를 다시 세우는
    자리(`pending_transition`)는 응답 모양이 아니라 객체가 있어야 `apply_approval` 을
    부를 수 있는데, 응답 모양에서 되만드는 것은 같은 사실을 두 번 만드는 것이라 둘이
    갈리는 날이 온다 (`commitment_parts` 의 그 규율).

    주의: `None` 이 두 가지 뜻이다 — "승인이 없다" 와 "승인인데 약정을 못 만들었다".
    둘을 갈라야 하는 자리는 `current_commitment` 을 같이 읽는다 (`buildable` ·
    `reason` 이 거기 실린다).
    """
    return _current_approval_parts(request_id)[1]


def _current_approval_parts(
    request_id: str,
) -> tuple[CommitmentOut | None, ApprovedCommitment | None]:
    """현재 유효한 승인을 그 실행으로 재조립한다. 응답 모양과 객체를 함께 낸다.

    재조립이 두 벌이 되지 않게 한 자리에 둔다. `current_commitment` 과
    `current_approved_commitment` 이 같은 사실을 서로 다른 코드로 만들면, 화면이 본
    약정과 원장에 실린 약정이 갈리는 날이 온다.

    번복은 여기서 저절로 반영된다 — `is_current` 인 결정 하나만 본다.

    실매입 기록이 있으면 기록값으로 덮는다 (설계 260915 안 A §4-4). 재시도 · 조회가
    같은 값을 봐야 한다. 기록은 사람 승인에만 있으므로 자동 승인은 조회도 안 한다.
    """
    approval = current_approval(request_id)
    if approval is None:
        return None, None
    if approval.plan is None or not awaits_purchase_record(approval.decision.decided_by):
        return approval.plan_out, approval.plan
    if approval.sim_run_id is None:
        # 기록은 실행 축으로 적힌다 (PK). 축을 못 읽은 승인에는 기록이 설 수 없다.
        return approval.plan_out, approval.plan
    rows = list_purchase_record_legs(
        sim_run_id=approval.sim_run_id,
        request_id=request_id,
        decision_seq=approval.decision.decision_seq,
    )
    if not rows:
        return approval.plan_out, approval.plan
    try:
        recorded = commitment_with_record(approval, recorded_legs_of(rows), str(rows[0]["grade"]))
    except CommitmentNotBuildable as exc:
        reason = f"실매입 기록을 약정에 덮지 못했다: {exc}"
        return CommitmentOut(buildable=False, reason=reason), None
    return CommitmentOut.of(recorded), recorded


def current_approval(request_id: str) -> CurrentApproval | None:
    """현재 유효한 승인. 결정이 없거나 현재 결정이 승인이 아니면 `None`.

    재조립은 한 줄기다 — 결정이 가리키는 실행을 읽어 `commitment_parts` 로 선정안
    약정을 만든다. 기록을 덮는 것은 이 뒤의 일이다.
    """
    current = next((row for row in list_decisions(request_id) if row.is_current), None)
    if current is None or current.decision != "APPROVE":
        return None

    row = run_for(request_id, current.history_run_id)
    response_payload = dict(row.get("response_payload") or {})
    replay = DecisionIn(
        decision="APPROVE",
        scenario_label=current.scenario_label,
        decided_by=current.decided_by,
        history_run_id=current.history_run_id,
    )
    plan_out, plan = commitment_parts(request_id, current.decision_seq, replay, response_payload)
    return CurrentApproval(
        decision=current,
        cycle=cycle_of(row),
        run_row=dict(row),
        response_payload=response_payload,
        as_of=as_of_of(response_payload),
        purchase_payment_days=payment_days_of(response_payload),
        sim_run_id=run_sim_run_id_of(row),
        plan_out=plan_out,
        plan=plan,
    )


def recorded_legs_of(rows: Sequence[Mapping[str, Any]]) -> tuple[RecordedLeg, ...]:
    """기록 행을 회차 값으로 바꾼다. 값을 고치지 않는다."""
    return tuple(
        RecordedLeg(
            seq=int(row["leg_seq"]),
            qty_kg=float(row["quantity_kg"]),
            amount_krw=float(row["amount_krw"]),
            purchase_date=row["purchase_date"],
            arrival_date=row["arrival_date"],
        )
        for row in rows
    )


def commitment_with_record(
    approval: CurrentApproval, legs: Sequence[RecordedLeg], grade: str
) -> ApprovedCommitment:
    """선정안 약정 사본에 실매입 값을 덮는다.

    지급 일수는 약정 조립이 쓰던 그 값이다 (`payment_days_of` · N5). 여기서 재무
    정책을 다시 읽지 않는다.

    :raises CommitmentNotBuildable: 선정안이 없거나 기록이 선정안 회차와 맞지 않는다.
    """
    if approval.plan is None:
        reason = approval.plan_out.reason if approval.plan_out is not None else None
        raise CommitmentNotBuildable(reason or "선정안 약정을 만들지 못했다.")
    return with_purchase_record(
        approval.plan,
        legs=legs,
        grade=grade,
        purchase_payment_days=approval.purchase_payment_days,
    )


def commitments_before(item: str, as_of: date, *, limit: int = 50) -> list[CommitmentOut]:
    """그 품목에서 `as_of` 이전에 승인된 확정 입고 약정 전부 (#185).

    오늘 실행이 어제를 아는 유일한 길이다. `current_commitment` 은 `request_id` 를
    알아야만 답하므로 "피마늘 · 1-02 까지 승인된 것을 다 다오" 는 이 함수로 묻는다.
    이것이 없으면 어제 승인한 매입이 오늘 창고에 없는 것처럼 된다.

    새로 적재하지 않는다. `current_commitment` 을 그대로 부른다 — 약정을 만드는
    함수가 하나뿐이어야 같은 사실의 주인이 하나로 남는다. 표를 새로 만들어 두 벌로
    두면 재조립본과 적재본이 갈리는 날이 온다.

    `as_of` 당일은 뺀다 (`as_of_before` 가 `<`). 오늘 것을 같이 세면 실행이 자기
    자신을 입력으로 먹는다.

    번복은 저절로 빠진다 — `current_commitment` 이 `is_current` 하나만 보므로 뒤집힌
    승인의 약정은 이 목록에 안 들어온다.

    :returns: 승인이 없으면 빈 목록. 없는 것을 만들지 않는다 — 빈 목록과
              "조회를 못 했다" 는 다르고, 후자는 예외로 올라간다.
    """
    seen: set[str] = set()
    out: list[CommitmentOut] = []
    for run in list_runs(item=item, as_of_before=as_of, limit=limit):
        request_id = run["request_id"] if isinstance(run, dict) else run.request_id
        if request_id in seen:
            continue  # 한 업무 키에 실행이 여럿이어도 약정은 하나다
        seen.add(request_id)
        commitment = current_commitment(request_id)
        if commitment is not None:
            out.append(commitment)
    return out


def list_purchase_record_legs(
    *, sim_run_id: str, request_id: str, decision_seq: int
) -> list[dict[str, Any]]:
    """그 실행 축 · 그 승인에 적힌 기록 행 전부. 회차 순. 없으면 빈 목록."""
    schema = get_db_schema()
    with core_db.read_connection() as conn:
        return select_purchase_record_legs(
            conn,
            sim_run_id=sim_run_id,
            request_id=request_id,
            decision_seq=decision_seq,
            schema=schema,
        )
