"""pending_transition.py — **승인은 났는데 원장에 안 닿은 것을 다시 세운다** (2026-09-11).

```text
개장 → **미적용 전이 재시도** → 입고 → 채권 → 수금 → [장부 관문] → …
```

★★ **왜 이 단계가 생겼나 — 모든 승인이 같은 모양으로 실패했다.**

  ```text
  실측 2026-09-11 · SIM-WALK-2026-APPROVED · 2026-01-01 ~ 01-09
    종료코드  E1_APPROVED 15
    승인어휘  RECORDED 15
    🔴 원장    purchases 0행 · purchase_items 0행 · inventory_lots 0행
  ```

  승인 하나를 골라 전이를 그대로 재현하면 이렇게 선다.

  ```text
  약정 조립   buildable=True
  전이        FAILED
    전이 적재 실패: 갱신할 물류 runtime fixture 행이 없다
    (sim_run_id=…, as_of=2026-01-10, usage_scope=AGENT_MVP_DEMO)
  ```

  🔴 **하루가 어긋난다.** 승인이 선 날은 01-09 이고 상태가 설 날은 01-10 인데
  (`transition._target_state_date` — 승인일 다음 날), 걷기는 날짜 순으로 돌므로
  **01-10 행은 다음 차례에** 열린다. 전이는 **언제나 하루 앞을 본다.**

  🟢 **물류가 그 행을 미리 안 만드는 것은 옳다.** `evidence_grade` · `approved_by` ·
  나머지 두 status 는 물류의 주장이고, 전이가 지어내면 **없는 근거가 물류 표에
  앉는다** (`logistics/schemas/transition.py` 의 `LogisticsFixtureMissing` 원문).

  ★★ **그래서 고칠 자리는 물류도 승인도 아니라 「언제 다시 부르나」다.** 도착일이
  열린 날 — 즉 그 날 개장이 그 행을 세운 **직후** — 에 다시 한 번 세우면 된다.

---

🔴 **개장 바로 뒤, 입고 앞이다.**

  그날 도착 행이 방금 열렸고, **입고가 그 도착을 잡아야** 하기 때문이다. 뒤로
  가면 그날 도착이 하루 더 밀린다 (`scheduler.run_scheduled_day` 의 하루 순서).

🔴 **미적용 목록을 새 표에 들고 있지 않는다.**

  ```text
  🟢 이미 있는 사실로 찾는다   승인된 결정 중 **원장에 안 닿은 것**
  ✗  미적용 목록을 새 표에
  ```

  새 표를 만들면 같은 사실의 주인이 둘이 되고, 한쪽만 고치는 날 갈린다. 승인은
  `master_decisions` 에 있고 원장은 `purchases` 에 있으니 **둘을 맞대면 답이 나온다**
  (`pending_transition_repository` 가 두 목록을 가져오고, 맞대는 것은 아래
  `pending_approvals` 다).

🔴 **재시도가 하루를 죽이지 않는다.** 또 실패하면 **세어서 요약에 올리고 하루는
   계속 간다.** `apply_approval` 이 이미 예외 대신 값을 돌려주고 (승인한 사실이
   전이 실패로 지워지면 안 되니까), 이 파일도 같은 태도다.

⚠️ **`apply_approval` 의 태도를 바꾸지 않는다.** 여기서 예외로 올리면 그 규율이
  이 자리 하나만 안 지나게 된다.

★ 2026-09-30 재구성 BL-018: `master/pending_transition.py` 에서 옮겼다. 역할이 다른 부분은 갈랐다 —
  `domain/pending_transition.py`; `schemas/pending_transition.py`. 무엇이 어디로 갔는지는 설계서
  대응표 `master/` 절.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date
from typing import Any

from app.contracts.commitment import ApprovedCommitment
from app.master.domain.pending_transition import pending_approvals
from app.master.readmodel.approvals import current_approved_commitment
from app.master.readmodel.pending_transitions import approved_decisions, ledger_purchase_ids
from app.master.readmodel.purchase_record import recorded_decision_keys
from app.master.schemas.pending_transition import (
    PendingApproval,
    RetriedTransition,
    RetryOut,
    RetryOutcome,
)
from app.master.schemas.transition import TransitionOut
from app.master.service.transition import apply_approval


def retry_pending_transitions(
    as_of: date,
    *,
    sim_run_id: str,
    decisions_of: Callable[..., Sequence[Mapping[str, Any]]] = approved_decisions,
    purchase_ids_of: Callable[..., Sequence[str]] = ledger_purchase_ids,
    recorded_of: Callable[..., Iterable[tuple[str, int]]] = recorded_decision_keys,
    commitment_of: Callable[[str], ApprovedCommitment | None] = current_approved_commitment,
    apply_fn: Callable[..., TransitionOut] = apply_approval,
) -> RetryOut:
    """미적용 전이를 **다시 세운다.** 🔴 **예외를 밖으로 내지 않는다.**

    ```text
    1. 두 목록을 가져온다   승인 · 원장 ID
    2. 맞대어 미적용을 고른다 (pending_approvals)
    3. 하나씩 약정을 재조립하고 apply_approval 을 다시 부른다
    4. 결과를 세어 값으로 돌려준다 — **하루는 계속 간다**
    ```

    🔴 **하나가 터져도 나머지를 계속 세운다.** 하나 때문에 멈추면 그 뒤의 승인이
       전부 미적용으로 남고, 다음 날 같은 자리에서 또 멈춘다.

    🔴 **약정을 새로 적재하지 않는다.** `decision_service.current_approved_commitment`
       이 **승인 시점의 실행으로 재조립**한다 — 같은 입력·같은 코드라 승인 응답에
       실렸던 것과 같은 값이 나온다.

    ★ **축을 지어내지 않는다.** 승인 행이 든 `sim_run_id` 를 그대로 넘긴다 — 조회가
      이미 축으로 좁혔으므로 받은 값과 같지만, 값의 주인은 실행 이력 행이다.

    :param as_of: 오늘. 🔴 **오늘보다 앞선 날의 승인만 본다** (`pending_approvals`
        의 `before`). 오늘 승인은 상태가 설 날이 내일이라 오늘 세울 자리가 없고,
        같은 범위를 다시 걸을 때 미래의 승인을 끌어오지 않게 막는 줄이기도 하다.
    :param commitment_of: 약정을 재조립하는 자리. 🔴 **기본값이 실제 함수 자체다**
        (`clock.py` · `verifier.py` 와 같은 규율) — `None` 을 안 받는다.
    :param apply_fn: 전이 경계. 기본이 `apply_approval` 자체다.
    :param recorded_of: 실매입 기록이 있는 승인 키. 기본이 저장소 함수 자체다.
        🔴 사람 승인은 기록이 있을 때만 다시 세운다 · 기록이 있으면 `commitment_of`
        (`current_approved_commitment`)가 **기록값으로 덮어** 조립한다.
    """
    try:
        found = pending_approvals(
            decisions=decisions_of(sim_run_id=sim_run_id),
            purchase_ids=purchase_ids_of(sim_run_id=sim_run_id),
            before=as_of,
            recorded=recorded_of(sim_run_id=sim_run_id),
        )
    except Exception as exc:  # noqa: BLE001 - 조회가 터져도 하루는 계속 간다.
        # 🔴 **`NOTHING_DUE` 로 접지 않는다.** 미적용이 없는 것과 있었는지 못 물어본
        #    것은 다르고, 접으면 조회가 죽은 날 이 단계가 조용해진다.
        return RetryOut(
            status="FAILED",
            reason=f"미적용을 못 찾았다: {type(exc).__name__}: {exc}",
        )

    if not found:
        # 🟢 **정상이다.** 승인이 다 닿았거나 아직 승인이 없다.
        return RetryOut(status="NOTHING_DUE", reason="미적용 전이가 없다")

    retried = tuple(
        _retry_one(one, commitment_of=commitment_of, apply_fn=apply_fn) for one in found
    )
    return RetryOut(
        status="RAN",
        reason=f"미적용 {len(retried)}건을 다시 세웠다",
        retried=retried,
    )


def _retry_one(
    pending: PendingApproval,
    *,
    commitment_of: Callable[[str], ApprovedCommitment | None],
    apply_fn: Callable[..., TransitionOut],
) -> RetriedTransition:
    """미적용 하나. **예외를 값으로 옮긴다** (`scheduler._stage` 와 같은 태도)."""

    def 결과(outcome: RetryOutcome, reason: str = "", block_kind: str = "") -> RetriedTransition:
        return RetriedTransition(
            request_id=pending.request_id,
            decision_seq=pending.decision_seq,
            as_of=pending.as_of,
            outcome=outcome,
            reason=reason,
            block_kind=block_kind,
        )

    try:
        commitment = commitment_of(pending.request_id)
    except Exception as exc:  # noqa: BLE001 - 하나가 터져도 나머지는 계속 선다.
        return 결과("FAILED", f"약정 재조립이 터졌다: {type(exc).__name__}: {exc}")
    if commitment is None:
        # ⚠️ **`FAILED` 가 아니다.** 약정을 못 만든 것은 전이 **앞에서** 끝난 일이고,
        #   그 사유는 `current_commitment` 의 `reason` 이 든다.
        return 결과("NOT_BUILDABLE", "승인이 현재 결정이 아니거나 약정을 못 만들었다")
    try:
        out = apply_fn(commitment, sim_run_id=pending.sim_run_id)
    except Exception as exc:  # noqa: BLE001 - 전이 실패가 적재된 결정을 지우면 안 된다.
        # ★ `apply_approval` 은 예외를 안 내겠다고 적어 뒀지만 여기서 한 번 더 잡는다 —
        #   하루의 진행이 그 약속에 걸리면 안 된다.
        return 결과("FAILED", f"전이가 터졌다: {type(exc).__name__}: {exc}")
    # ⚠️ **어휘를 접지 않고 그대로 적는다** — 무엇이 왜 안 닿았는지가 이 줄이다.
    # 🔴 **갈래도 버리지 않는다** (2026-09-16). 이 한 칸이 없으면 다음 날 재시도에서
    #    막힌 건이 요약에서 안 세어지고, **한쪽 경로만 세면 수가 조용히 작아진다.**
    return 결과(str(out.status), out.reason, out.block_kind)  # type: ignore[arg-type]
