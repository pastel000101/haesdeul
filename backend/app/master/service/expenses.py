"""그날 도래 운영비 일괄 지급 — 연결을 빌려 재무 일괄 지급을 부르고 한 번 commit 한다.

지급 계산과 기록은 재무(`app/finance/service/expenses.py` 의 `settle_due_expenses`)가 한다.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from datetime import date
from decimal import Decimal
from typing import Any

from app.core import db as core_db
from app.finance.schemas.expenses import ExpenseSettlement


def settle_expenses(
    *,
    as_of: date,
    sim_run_id: str,
    settle_expenses_fn: Callable[..., Any],
    enabled: bool,
    borrow: core_db.Borrow | None = None,
) -> tuple[str, tuple[ExpenseSettlement, ...], str | None]:
    """지급일이 된 운영비를 한 트랜잭션으로 지급한다.

    `enabled` 가 거짓이면 `settle_expenses_fn` 을 부르지도 않는다. 지급 여부가 갈리는
    자리는 이 한 줄뿐이다 — `_maintain` · `_approve` 와 같은 모양이고, 이유는 더 강하다.
    승인은 append-only 표에 한 줄이 남고 폐기는 물건이 없어지는데, 지급은 현금이 줄고
    재무에 되돌리는 경로가 없다(재무는 `ACCRUED` 만 취소하고 `PAID → CANCELLED` 는 없다).

    커넥션은 이 함수가 연다. `settle_due_expenses` 는 commit 도 rollback 도 하지 않으므로
    그 반대편이 여기다. 여러 건을 지급하다 중간에 실패하면 앞선 지급까지 같이 되돌아가야
    하므로 한 사이클이 한 커밋이다(`close_day` · `collect_receipts` 와 같은 모양).

    마감보다 먼저 커밋된다. 부르는 쪽이 이 함수를 마감 앞에 두었고, 마감은 그날 `PAID`
    인 비용을 다른 커넥션으로 읽는다 — 여기서 커밋을 미루면 마감이 방금 나간 돈을 못 본다.

    실패 처리: 실패하면 하루가 그대로 이어지지 않는다. `_stage` · `_maintain` 과 태도가
    다른 유일한 단계다. 사유를 돌려주고, 부르는 쪽이 그 문장으로 그날 마감을 `BLOCKED`
    로 막는다 — 그냥 넘기면 «현금은 줄었는데 비용은 0원» 이 확정값으로 앉는다.

    금액도 지급일도 여기서 정하지 않는다. 무엇이 얼마나 언제 나가는지의 주인은
    `expenses` 표이고, 이 함수가 넘기는 것은 실행 축과 기준일 둘뿐이다.

    :returns: `(단계 상태, 지급한 건들, 사유 한 줄)`. 끈 날은 `("NOT_ATTEMPTED", (), None)`
        이고 note 도 남기지 않는다. 끈 것은 사건이 아니라 기본값이고, 매일 한 줄씩 남기면
        진짜 사유가 읽히지 않는다(`_maintain` · `_approve` 와 같은 규율).
    """
    if not enabled:
        return "NOT_ATTEMPTED", (), None
    open_connection = core_db.connection if borrow is None else borrow
    with ExitStack() as stack:
        try:
            conn = stack.enter_context(open_connection())
        except Exception as exc:  # noqa: BLE001 - 못 붙은 것도 «지급을 못 했다» 는 사실이다.
            # 연결하지 못한 날을 조용히 «지급할 것이 없었다» 로 적지 않는다. 그 날도
            # 마감을 막는다 — 안 막으면 그날 나갔어야 할 돈이 0원으로 확정된다.
            return "FAILED", (), f"운영비 지급이 터졌다: {type(exc).__name__}: {exc}"
        try:
            settlements = tuple(settle_expenses_fn(conn, sim_run_id=sim_run_id, as_of=as_of))
            conn.commit()
        except Exception as exc:  # noqa: BLE001 - 절반만 나간 지급을 장부에 남기지 않는다.
            conn.rollback()
            return "FAILED", (), f"운영비 지급이 터졌다: {type(exc).__name__}: {exc}"
    if not settlements:
        # `NOT_ATTEMPTED` 와 접지 않는다 — "켰는데 지급일이 된 것이 없었다" 는 뜻이다.
        return "NOTHING_DUE", (), "운영비 지급: NOTHING_DUE 지급일이 된 비용이 없다"
    합계 = sum((one.amount_krw for one in settlements), Decimal(0))
    return "RAN", settlements, f"운영비 지급: RAN {len(settlements)}건 / {합계}원"
