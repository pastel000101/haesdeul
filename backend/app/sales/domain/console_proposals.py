"""그날 판매안의 자리 판정 — 안 하나가, 그리고 그날 화면 전체가 어디에 서는가.

★ 2026-09-29 BL-013: `sales/console_proposals.py` 에서 판정 둘을 옮겼다. 저장된 안을 읽어
  응답으로 펴는 조립은 `readmodel/console_proposals.py` 다.
"""

from collections import Counter

#: 판매가 스스로 «아직 판정 못 받았다» 고 적은 상태.
_SALES_UNRESOLVED_STATUSES = frozenset({"UNRESOLVED", "REVIEW_REQUIRED"})

#: 판매가 스스로 «막혔다» 고 적은 상태.
_SALES_BLOCKED_STATUSES = frozenset({"INFEASIBLE"})


def presentation_state(
    *,
    sales_status: str | None,
    finance_verdict: str | None,
    missing_capabilities: list[str],
) -> tuple[str, list[str]]:
    """이 안이 **사용자 앞에서 어느 자리에 서는가**와 그 이유.

    두 축을 함께 읽는다 — 판매가 스스로 매긴 상태와 재무가 내린 판정이다.

    ```text
    재무가 FAIL 이라고 했다            → REJECTED        판정이 났다
    판매가 INFEASIBLE 이라고 했다      → REJECTED        판정이 났다
    재무가 아무 말도 안 했다           → UNRESOLVED      판정이 안 났다
    판매가 UNRESOLVED 라고 했다        → UNRESOLVED      판정이 안 났다
    재무가 REVIEW_REQUIRED 라고 했다   → REVIEW_REQUIRED 사람이 봐야 한다
    재무가 PASS 라고 했다              → PRESENTABLE     승인으로 갈 수 있다
    ```

    🔴 **`UNRESOLVED` 를 `PASS` 로 바꾸지 않는다.** 판정을 안 받은 안을 통과로 적으면
       재무가 막았을 거래가 사용자 화면에서 승인 가능으로 보인다.

    🔴 **`UNRESOLVED` 를 `REJECTED` 로도 적지 않는다.** 아무도 탈락시키지 않았는데
       탈락이라고 적으면, 사용자는 자료를 채워야 할 날에 조건을 바꾼다.

    ★ **막힌 안이 판정 순서보다 앞선다.** 재무가 `FAIL` 을 냈으면 판매가 스스로 뭐라
      적었든 그 안은 탈락이다 — 권위 있는 판정이 이겼다.
    """
    if finance_verdict == "FAIL":
        return "REJECTED", []
    if sales_status in _SALES_BLOCKED_STATUSES:
        return "REJECTED", []
    if finance_verdict is None:
        #  ★ 왜 판정이 안 났는지를 **판매가 적어 둔 사실에서만** 읽는다. 없는 이유를
        #    지어내면 사용자가 채울 수 없는 것을 채우려 한다.
        reasons = list(missing_capabilities) or ["FINANCIAL_VALIDATION_PENDING"]
        return "UNRESOLVED", reasons
    if sales_status in _SALES_UNRESOLVED_STATUSES and finance_verdict != "PASS":
        return "UNRESOLVED", list(missing_capabilities) or [f"SALES_STATUS_{sales_status}"]
    if finance_verdict == "REVIEW_REQUIRED":
        return "REVIEW_REQUIRED", []
    if finance_verdict == "PASS":
        return "PRESENTABLE", []
    #  ⚠️ 모르는 판정 어휘를 통과로 접지 않는다.
    return "UNRESOLVED", [f"UNKNOWN_FINANCE_VERDICT_{finance_verdict}"]


def overall_state(counted: Counter[str], *, has_rows: bool) -> str:
    """그날 판매 화면 전체가 어떤 상태인가.

    ```text
    행이 하나도 없다                → EMPTY        안을 못 만들었다
    통과가 하나라도 있다            → PRESENTABLE  보여줄 것이 있다
    미판정이 하나라도 있다          → UNRESOLVED   기다릴 것이 있다
    그 밖                           → REJECTED     판정이 다 났고 다 안 된다
    ```

    🔴 **미판정이 섞여 있으면 `REJECTED` 라고 적지 않는다** — 마스터의
       `_unpassed_outcome` 이 `SL6` 과 `SL3` 을 가르는 규칙과 같은 자리다. 판정을 안
       받은 안은 탈락한 적이 없다.
    """
    if not has_rows:
        return "EMPTY"
    if counted["PRESENTABLE"]:
        return "PRESENTABLE"
    if counted["UNRESOLVED"] or counted["REVIEW_REQUIRED"]:
        return "UNRESOLVED"
    return "REJECTED"
