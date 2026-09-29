# ─────────────────────────────────────────────────────────────────────────────
# STATUS: 공용 계약 — 기준일 시점 매출채권 상태의 **한 벌짜리 규칙**
#
#   누가 쓰나
#     재무   `app/finance/readmodel/console_receivables.py`   AR Aging 화면
#            `app/finance/readmodel/console_credit.py`        거래처 여신 화면
#     판매   `app/sales/readmodel/console_collections.py`     수금 화면
#            `app/sales/readmodel/console_partners.py`        거래처 상세의 채권
#            `app/sales/readmodel/dashboard.py`               판매 현황의 채권
#
# ★ 2026-09-29 재구성 BL-014: 재무(`finance/receivable_history.py`)와 판매
#   (`sales/domain/receivable_history.py`)에 글자까지 같은 규칙이 한 벌씩 있었다
#   (`tests/finance/test_receivable_history.py` 가 두 벌을 대조했다). 입력 · 반환 · 상태 판단이
#   같고 DB 를 모르는 순수 규칙이라 이 한 벌로 합쳤다 — `aging.py` 와 같은 이유로 재무 파일이
#   아니라 여기다(판매가 재무를 import 하지 않는다 · 설계서 쟁점 6).
#
# 🔴 **기준일 이하 수금액을 붙이는 SQL 조각은 여기 두지 않는다.** 계약은 DB 를 모른다. 그 조각은
#    재무 `repository/receivable_history.py` 와 판매 `repository/receivable_history.py` 에 같은
#    글자로 있고, 두 벌이 갈리는 순간 검사가 빨간불을 켠다.
# ─────────────────────────────────────────────────────────────────────────────

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
