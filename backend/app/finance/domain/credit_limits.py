"""거래처 여신한도 등록의 판정 — 새 기간이 기존 활성 기간과 어떻게 만나는가.

순서 · 트랜잭션은 `service/credit_limits.py`, SQL 은 `repository/credit_limits.py`.
"""

from collections.abc import Mapping, Sequence
from datetime import date


def credit_limit_period_to_close(
    active_rows: Sequence[Mapping[str, object]], *, effective_from: date
) -> Mapping[str, object] | None:
    """새 적용일로 끝내야 할 열린 기간 한 행. 끝낼 것이 없으면 `None`.

    과거 금액은 덮어쓰지 않는다 — 열린 기간을 새 적용일 전날로 끝내고 새 기간을 더한다.
    새 적용일과 겹치거나 그 뒤에 있는 활성 기간이 둘 이상이거나, 그 한 행이 새 적용일과 같은
    날 또는 뒤에 시작하면 받지 않는다(`ValueError` — 부르는 쪽이 409 · 충돌 문장으로 옮긴다).
    """
    future_or_overlap = [
        row
        for row in active_rows
        if row["effective_from"] >= effective_from
        or row["effective_to"] is None
        or row["effective_to"] >= effective_from
    ]
    if len(future_or_overlap) > 1:
        raise ValueError("여신한도 기간이 겹치거나 미래 이력이 있어 변경할 수 없습니다.")
    if not future_or_overlap:
        return None
    current = future_or_overlap[0]
    if current["effective_from"] >= effective_from:
        raise ValueError("새 적용일은 현재 한도 적용일보다 뒤여야 합니다.")
    return current
