"""거래처 여신 현황 read model — **얼마까지 더 팔 수 있고, 언제 풀리는가.**

★ 재무 화면에는 채권 목록과 연체 구간은 있었지만, 사용자가 판매 전에 묻는 질문에
  답하는 자리가 없었다.

```text
여신한도는 얼마인가              partner_credit_limits (그날 유효한 한 행)
지금 미수금은 얼마인가            receivables + master_collection_events (기준일 복원)
얼마까지 더 팔 수 있는가          한도 − 미수금
언제쯤 여신이 풀리는가            미수 채권의 계약상 결제 예정일
```

🔴 **계산을 새로 짓지 않는다.** 판정 경로와 같은 함수를 부른다.

```text
여신한도        partner_credit.partner_credit_limit_on  (0원 ≠ 없음)
미수·연체 집계   tools.summarize_partner_receivables     (미회수 상태의 정의가 한 곳)
가용 여신       tools.calculate_available_credit        (음수를 0으로 깎지 않는다)
사용률          tools.calculate_credit_utilization_rate (한도 0원이면 None)
```

🔴 **채권은 기준일 시점으로 복원해 읽는다** (`receivable_history`). 저장된 수금 칸을
   그대로 읽으면 과거 화면에 미래 수금이 실려 여신이 실제보다 넉넉해 보인다.

🔴 **수금 예정은 예정이다.** 여기서 채권을 줄이거나 현금을 늘리지 않는다. 실제 감소는
   수금 사건으로만 일어난다. 그리고 **한도를 되돌려 주는 로직은 없다** — 여신은
   미수금이 실제로 줄어야만 풀린다.

★ 2026-09-29 재구성 BL-014: 응답 모델은 `schemas/console_credit.py`, SQL 은
  `repository/console_credit.py` 로
  갈랐다. 조회 연결을 한 번 빌려 같은 순서로 읽는다(종전에는 조회마다 빌렸다).
"""

from datetime import date
from decimal import Decimal
from typing import Any

from app.contracts.receivable_history import projected_status
from app.core import db as core_db
from app.finance.domain.tools import (
    calculate_available_credit,
    calculate_credit_utilization_rate,
    summarize_partner_receivables,
)
from app.finance.readmodel.partner_credit import partner_credit_limit_on
from app.finance.repository.console_credit import (
    load_credit_partner_rows,
    select_partner_receivables_as_of,
)
from app.finance.schemas.console_credit import (
    ConsoleCreditCollection,
    ConsoleCreditResponse,
    ConsolePartnerCredit,
)
from app.finance.schemas.sales_validation import PartnerReceivable

#: 화면에 펼칠 수금 예정 수. 전부가 아니라 «다음 몇 번» 이 궁금한 자리다.
UPCOMING_COLLECTION_LIMIT = 5


def load_partner_receivables_as_of(
    conn: Any, *, sim_run_id: str, as_of: date, partner_id: str
) -> list[PartnerReceivable]:
    """거래처 채권을 **기준일 시점으로 복원해** Finance 사실로 옮긴다.

    ★ 판매일과 발행일을 둘 다 기준일로 막는다 (`repository/partner_credit.py` 와 같은
      이유). 상태는 저장값이 아니라 복원한 금액에서 다시 읽는다.
    """
    receivables: list[PartnerReceivable] = []
    for raw in select_partner_receivables_as_of(
        conn, sim_run_id=sim_run_id, as_of=as_of, partner_id=partner_id
    ):
        original = Decimal(str(raw["original_amount_krw"]))
        received = Decimal(str(raw["received_amount_krw"]))
        outstanding = Decimal(str(raw["outstanding_amount_krw"]))
        receivables.append(
            PartnerReceivable(
                receivable_id=str(raw["receivable_id"]),
                due_date=raw["due_date"],
                # 🔴 초과 수금이 기록돼도 미수를 음수로 만들지 않는다 — 상태가 COLLECTED 다.
                outstanding_amount_krw=max(outstanding, Decimal(0)),
                status=projected_status(original_amount_krw=original, received_amount_krw=received),
                source_ref=str(raw["receivable_id"]),
            )
        )
    return receivables


def get_console_credit(*, sim_run_id: str, as_of: date) -> ConsoleCreditResponse:
    """거래처별 여신 현황. **판정하지 않는다 — 판매 판정은 재무 검증이 한다.**

    ★ 2026-09-29 재구성 BL-014: 조회 연결을 한 번 빌려 같은 순서로 읽는다 (종전에는 조회마다
      빌렸다). 여신한도는 판정 경로와 같은 조회 · 같은 규칙(`credit_limit_from_rows`)이다.
    """
    partners: list[ConsolePartnerCredit] = []
    with core_db.read_connection() as conn:
        for raw in load_credit_partner_rows(conn, sim_run_id=sim_run_id, as_of=as_of):
            partner_id = str(raw["partner_id"])
            facts = summarize_partner_receivables(
                partner_id=partner_id,
                as_of=as_of,
                receivables=load_partner_receivables_as_of(
                    conn, sim_run_id=sim_run_id, as_of=as_of, partner_id=partner_id
                ),
            )
            limit = partner_credit_limit_on(conn, as_of=as_of, partner_id=partner_id)
            available = (
                None
                if limit is None
                else calculate_available_credit(
                    credit_limit_krw=limit, current_partner_ar_krw=facts.current_ar_krw
                )
            )
            utilization = (
                None
                if limit is None
                else calculate_credit_utilization_rate(
                    current_partner_ar_krw=facts.current_ar_krw, credit_limit_krw=limit
                )
            )
            upcoming: list[ConsoleCreditCollection] = []
            freed = Decimal(0)
            for due in facts.open_receivable_schedule[:UPCOMING_COLLECTION_LIMIT]:
                freed += due.outstanding_amount_krw
                upcoming.append(
                    ConsoleCreditCollection(
                        due_date=due.due_date,
                        amount_krw=due.outstanding_amount_krw,
                        overdue=due.due_date < as_of,
                        available_credit_after_krw=None if available is None else available + freed,
                    )
                )
            days = raw.get("sales_collection_days")
            partners.append(
                ConsolePartnerCredit(
                    partner_id=partner_id,
                    partner_name=(
                        None if raw.get("partner_name") is None else str(raw["partner_name"])
                    ),
                    payment_days=(
                        days if isinstance(days, int) and not isinstance(days, bool) else None
                    ),
                    credit_limit_krw=limit,
                    credit_limit_evidence_grade=(
                        None
                        if raw.get("credit_limit_evidence_grade") is None
                        else str(raw["credit_limit_evidence_grade"])
                    ),
                    current_ar_krw=facts.current_ar_krw,
                    overdue_ar_krw=facts.overdue_ar_krw,
                    open_receivable_count=facts.open_receivable_count,
                    available_credit_krw=available,
                    credit_utilization_rate=utilization,
                    upcoming_collections=upcoming,
                )
            )
    return ConsoleCreditResponse(sim_run_id=sim_run_id, as_of=as_of, partners=partners)
