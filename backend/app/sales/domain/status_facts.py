"""판매 진행 상황을 사람이 읽는 사실로 — 마스터 STATUS_QUERY 회신의 본문.

★ 2026-09-29 BL-013: `sales/adapter.py` 에서 옮겼다. 사실을 읽는 조회는
  `readmodel/status.py`, 봉투 회신으로 옮기는 번역은 `adapter.py` 다.
"""

from app.sales.schemas.console_proposals import ConsoleSalesProposalsResponse

#: 안의 성격을 사람 말로. 모르는 값은 원문 대신 «판매안» 이다.
_SCENARIO_WORDS = {"CONSERVATIVE": "안정 우선", "BALANCED": "균형", "AGGRESSIVE": "판매 기회 우선"}


def status_facts(
    proposals: ConsoleSalesProposalsResponse | None, *, has_history: bool
) -> dict[str, str]:
    """판매 진행 상황을 **사람이 읽는 사실**로 만든다.

    🔴 **키가 곧 화면 글자다.** 마스터는 부서가 낸 키를 이름 그대로 사실 줄로 편다
       (`master/answer.py` · `_LABEL.get(key, key)`). 그래서 `request_id` ·
       `SCENARIOS_GENERATED` · `FINANCIAL_VALIDATION` 같은 기계용 키와 값을 여기 실으면
       그대로 사용자 말풍선에 나간다 — 실측으로 그렇게 나왔다.

    🔴 **숫자를 지어내지 않는다.** 세는 것은 금일 판매안 read model 이 돌려준 행뿐이고,
       재무 검토 상태는 판매 1차 회신의 «못 받은 검증» 이 아니라 **재무가 남긴 판정**이다.
       (1차 회신은 되먹임 전이라 늘 «재무 검토 미완» 으로 남아, 이미 판정이 난 안까지
       검토 전으로 읽혔다.)

    ★ 비교 · 선택 · 확정은 대화의 판매안 카드와 판매 화면이 한다. 여기서는 요약만 한다.
    """
    if proposals is None:
        return {"오늘 판매안": "판매안 정보를 읽지 못했습니다. 판매 화면에서 다시 확인해 주세요."}
    rows = proposals.rows
    if not rows:
        if proposals.request_count == 0:
            text = (
                "이 날짜에는 판매가 돌지 않았습니다."
                if not has_history
                else "이 날짜에 만든 판매안이 없습니다."
            )
        elif proposals.hidden_zero_quantity > 0:
            text = "팔 수 있는 물량이 없어 판매안이 서지 않았습니다."
        else:
            text = "판매가 돌았지만 판매안을 만들지 못했습니다."
        return {"오늘 판매안": text}

    per_item: dict[str, int] = {}
    for row in rows:
        name = row.item or "품목 미상"
        per_item[name] = per_item.get(name, 0) + 1
    facts: dict[str, str] = {
        "오늘 검토 중인 판매": " · ".join(
            f"{name} 판매안 {count}개" for name, count in per_item.items()
        ),
    }

    facts["재무 검토"] = review_sentence([row.finance_verdict for row in rows])

    need_collection = [
        row
        for row in rows
        if row.required_collection_before_sale_krw is not None
        and row.required_collection_before_sale_krw > 0
    ]
    if need_collection:
        facts["선회수 필요"] = (
            f"{len(need_collection)}개 안은 기존 미수금을 먼저 회수해야 현재 여신한도 안에서 "
            "판매할 수 있습니다"
        )

    recommended = [row for row in rows if row.recommended]
    if recommended:
        facts["추천 판매안"] = " · ".join(
            f"{row.item or '품목 미상'} {_SCENARIO_WORDS.get(row.scenario_type or '', '판매안')}"
            for row in recommended
        )

    confirmed = [row for row in rows if row.sale_status is not None]
    facts["확정된 판매"] = (
        " · ".join(
            f"{row.item or '품목 미상'} {_SCENARIO_WORDS.get(row.scenario_type or '', '판매안')}"
            for row in confirmed
        )
        if confirmed
        else "아직 없습니다"
    )
    return facts


def review_sentence(verdicts: list[str | None]) -> str:
    """재무 검토 상태를 **한 문장으로.** 판정 코드를 세서 고르기만 한다.

    ```text
    모두 진행 어려움            현재 조건으로 바로 진행하기 어려운 판매안이 N개 있습니다
    모두 진행 가능              현재 조건에서 진행 가능한 판매안이 준비되어 있습니다
    확인 필요 · 검토 전이 있다   재무 검토가 필요한 판매안이 있습니다 (+ 진행 가능 N개)
    진행 가능과 어려움만 섞였다  진행 가능한 판매안 N개와 … 어려운 판매안 M개가 있습니다
    ```

    🔴 **모르는 판정은 «확인 필요» 쪽으로 센다** — 통과로 뭉치지 않는다.
    """
    passed = verdicts.count("PASS")
    failed = verdicts.count("FAIL")
    pending = len(verdicts) - passed - failed
    if verdicts and failed == len(verdicts):
        return f"현재 조건으로 바로 진행하기 어려운 판매안이 {failed}개 있습니다"
    if verdicts and passed == len(verdicts):
        return "현재 조건에서 진행 가능한 판매안이 준비되어 있습니다"
    if pending:
        extra = f" (진행 가능한 판매안 {passed}개)" if passed else ""
        return f"재무 검토가 필요한 판매안이 있습니다{extra}"
    return (
        f"진행 가능한 판매안 {passed}개와 "
        f"현재 조건으로 진행하기 어려운 판매안 {failed}개가 있습니다"
    )
