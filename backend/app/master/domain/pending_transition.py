"""미적용 전이 목록 판정 — 승인 행과 원장 행을 대조해 재시도할 승인을 고른다.

★ 2026-09-30 재구성 BL-018: `master/pending_transition.py` 에서 옮겼다 — `pending_approvals`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any

from app.master.domain.decision import awaits_purchase_record
from app.master.domain.purchase_ids import purchase_id_prefix_for
from app.master.schemas.pending_transition import PendingApproval


def pending_approvals(
    *,
    decisions: Iterable[Mapping[str, Any]],
    purchase_ids: Iterable[str],
    before: date,
    recorded: Iterable[tuple[str, int]] = (),
) -> tuple[PendingApproval, ...]:
    """승인 목록과 원장 ID 목록을 **맞대어** 미적용을 고른다. 🔴 **순수 함수다.**

    ★★ **어떻게 찾았는지가 이 함수 세 줄이다.**

      ```text
      승인 하나가 원장에 남기는 행의 ID    PUR-{request_id}-D{decision_seq}-S{회차}
      그 앞머리                            purchase_id_prefix_for(request_id, seq)
      앞머리로 시작하는 purchase_id 가
        하나라도 있다   → 닿았다 (다시 안 한다)
        하나도 없다     → 🔴 미적용이다
      ```

    🔴 **앞머리를 여기서 짓지 않는다.** `transition.purchase_id_prefix_for` 를 부른다 —
       ID 규칙의 주인은 그쪽 하나이고, 여기 문자열을 복사하면 규칙이 바뀌는 날 이
       함수가 **에러 없이 늘 0건**을 돌려준다. 그러면 이 단계는 있으나 마나가 된다
       (`tests/master/test_pending_transition.py` 가 그 짝을 잠근다).

    ★ **회차 번호를 안 본다.** 회차는 약정을 조립해야 나오는데, 조립하기 전에
      먼저 걸러야 한다 — 그래서 앞머리까지만으로 판정한다. 회차 하나라도 서 있으면
      그 승인은 원장에 **닿은** 것이고, 반쪽만 선 승인은 이 단계가 아니라
      `apply_approval` 의 한 트랜잭션이 막는 자리다.

    :param before: 이 날보다 **앞선 날**의 승인만 본다. 🔴 **오늘 것을 빼는 이유.**
        상태가 설 날은 승인일 **다음 날**이라(`transition._target_state_date`) 오늘
        승인은 오늘 세울 자리가 없다. 그리고 같은 범위를 **다시 걸을 때** 이 줄이
        없으면 첫날 재시도가 **아직 오지 않은 날의 승인**까지 장부에 밀어 넣는다.
    :param recorded: 실매입 기록이 있는 승인 `(request_id, decision_seq)`.
        🔴 **사람 승인은 여기 있을 때만 고른다** (설계 260915 안 A §4-4). 기록이 없는
        사람 승인을 계획값으로 원장에 앉히면, 사람이 실제로 산 값을 적기도 전에 안의
        계획값이 채무 · 입고 일정이 된다. 기본이 빈 목록인 이유가 그것이다.
    """
    applied = tuple(purchase_ids)
    recorded_keys = frozenset(recorded)
    found: list[PendingApproval] = []
    for row in decisions:
        as_of = row["as_of"]
        if as_of >= before:
            continue
        request_id = row["request_id"]
        decision_seq = int(row["decision_seq"])
        key = (request_id, decision_seq)
        if awaits_purchase_record(row.get("decided_by")) and key not in recorded_keys:
            # 🔴 **사람 승인인데 실매입 기록이 없다 — 건너뛴다.** 기록이 들어오면 그날
            #    `record_purchase` 가 세우고, 못 섰으면 다음 재시도가 기록값으로 세운다.
            continue
        prefix = purchase_id_prefix_for(request_id, decision_seq)
        if any(one.startswith(prefix) for one in applied):
            # 🔴 **이미 닿았다. 다시 안 한다** — 멱등이 이 한 줄이다. 다시 하면
            #    같은 승인이 두 번 앉고, 되돌리는 경로가 저장소에 없다.
            continue
        found.append(
            PendingApproval(
                request_id=request_id,
                decision_seq=decision_seq,
                as_of=as_of,
                sim_run_id=row["sim_run_id"],
            )
        )
    return tuple(found)
