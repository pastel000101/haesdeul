"""기준일 시점의 채권 상태 규칙 — 복원한 금액에서 상태를 다시 세운다.

★ 2026-09-29 BL-013: `sales/receivable_history.py` 에서 상태 규칙을 옮겼다. 기준일 이하
  수금액을 붙이는 SQL 조각은 `repository/receivable_history.py` 에 있고, 왜 행을 그대로
  읽으면 안 되는지도 거기 적었다.

🔴 **재무에도 같은 규칙이 있다** (`app.finance.receivable_history`). 두 벌을 합치는 일은
   재무 계층화(BL-014 · 설계 쟁점 6)에서 한다. 그때까지 `tests/finance/test_receivable_history.py`
   가 두 벌의 규칙 본문을 대조한다.
"""

from decimal import Decimal

_ZERO = Decimal(0)


def projected_status(*, original_amount_krw: Decimal, received_amount_krw: Decimal) -> str:
    """그 시점의 채권 상태. **정본은 수금 전이 규칙(`build_collection_transition`)이다.**

    ```text
    받은 것이 없다      OPEN
    일부만 받았다       PARTIAL
    전액 받았다         COLLECTED
    ```

    🔴 **0 과 «없음» 을 가르지 않는다** — 여기 오는 값은 이미 복원된 금액이고,
       0 은 *"그날까지 한 푼도 안 들어왔다"* 는 사실이다.
    """
    #  ★ 완납을 **먼저** 본다. 원금이 0원인 채권은 받을 것이 없어 처음부터 끝난
    #    상태이고, 0 을 먼저 보면 그 채권이 영원히 OPEN 으로 남는다.
    if received_amount_krw >= original_amount_krw:
        return "COLLECTED"
    if received_amount_krw <= _ZERO:
        return "OPEN"
    return "PARTIAL"
