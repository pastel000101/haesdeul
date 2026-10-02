"""승인 전이 날짜 판정 — 목표 상태일, 앞으로 올 도착분, 도착분 없음 사유.

전이 실행(트랜잭션 · 부서 기록)은 `service/transition.py` 다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from app.contracts.commitment import ApprovedCommitment


def still_incoming_on(
    commitment: ApprovedCommitment, state_date: date
) -> ApprovedCommitment | None:
    """`state_date` 시점에 아직 안 온 도착분만 남긴 약정 사본. 없으면 `None`.

    `confirmed_inbound` 의 뜻이 그것이다 (`#381`). 그 칸은 "D 시점에 아직 안 온, 앞으로 올
    도착분" 이고, 도착일이 `D` 보다 이르면 이미 왔거나(로트가 됐거나) 안 온 사고다 — 둘 다
    「앞으로 올 도착분」이 아니다.

    이 함수는 `arrival_block_reason` 이 «목표 상태일에 앞으로 올 도착분이 있나» 를 보는
    자리에 쓰인다. 승인분을 이미 열린 다음 날들로 싣는 전파는 없다 (물류 W3-3 ·
    `service/transition.py`). 이 함수는 넘겨 주는 값을 좁힐 뿐, DB 에 이미 있는 행을
    지우지 않는다.

    물류 코드도 Protocol 도 안 고친다. 좁힌 것은 넘겨 주는 값뿐이다.

    회차 일정이 비어 있는 약정은 좁힐 것이 없다 — 그대로 통과시킨다.
    """
    if not commitment.arrival_schedule:
        return commitment
    legs = tuple(leg for leg in commitment.arrival_schedule if leg.arrival_date >= state_date)
    if not legs:
        return None
    if len(legs) == len(commitment.arrival_schedule):
        return commitment
    # 사본도 `__post_init__` 검증을 지난다 — 총량은 남긴 회차 합으로 맞춘다.
    amounts = [leg.amount_krw for leg in legs]
    # 금액은 전 회차에 실려 있을 때만 다시 센다. 하나라도 `None` 이면 검증이 금액을 안
    # 보고, 그때 총액을 건드리면 없는 근거로 값을 지어내는 것이 된다.
    total_amount_krw = (
        sum(amount for amount in amounts if amount is not None)
        if all(amount is not None for amount in amounts)
        else commitment.total_amount_krw
    )
    return replace(
        commitment,
        total_qty_kg=sum(leg.qty_kg for leg in legs),
        total_amount_krw=total_amount_krw,
        arrival_schedule=legs,
    )


def target_state_date_of(commitment: ApprovedCommitment) -> date:
    """이 승인으로 상태가 설 날.

    달력 다음 날이다. 실행일 달력(평일만 도는 그것)을 쓰지 않는다. 금요일 승인이면
    토요일이다. 주말에도 판매 시나리오로 물류·재무가 움직여 장부는 날마다 흐른다 — 다음
    평일까지 상태를 미루면 토·일 이틀치 사실이 장부에 없는 채로 월요일 상태가 선다.
    `#240` 이 정한 "실행일은 평일만, 경과일수는 달력일" 과 같은 결이다. 여기서 세는 것은
    상태가 설 날이지 "다음에 언제 판단을 도는가" 가 아니다.

    함수로 뺀 이유는 가드와 본문이 같은 날을 봐야 하기 때문이다. 두 자리에
      `as_of + 1` 을 각각 적으면 한쪽만 바뀌는 날이 온다.
    """
    return commitment.as_of + timedelta(days=1)


def arrival_block_reason(commitment: ApprovedCommitment, target_state_date: date) -> str:
    """목표 상태일에 「앞으로 올 도착분」이 하나도 없는 상태의 사유. 없으면 빈 문자열.

    이것은 버그를 고치는 가드가 아니라 미정 상태를 드러내는 가드다.

       리드타임 0 은 계약상 허용되는 값이다 (`app/logistics/schemas/snapshot.py` 의
       `inbound_lead_days: int = Field(ge=0)`). 그런데 리드타임이 0 이면
       `arrival_date == commitment.as_of` 이고 목표 상태일은 그 다음 날이라
       `still_incoming_on` 이 `None` 을 돌려준다 — 가드가 없으면 물류 `build` 를 한 번도
       안 부르고 `logistics.persist(conn, ())` 로 아무것도 안 쓴다.

       그런데 `purchases` 와 재무 행은 써지고 `APPLIED` 가 나간다. 물류만 조용히 빠진다.
       조용히 빠지는 것이 문제다.

    그래서 "틀렸다" 고 단정하지 않는다. 리드타임 0 일 때 이 경로가 무엇을 해야 하는지가
    정해진 적이 없다는 사실을 소리 나게 만드는 것이 여기서 하는 전부다. 막는 자리도
    방식도 `service/transition.py` 의 `_ledger_blocked` 와 같다 — 트랜잭션 밖에서
    `NOT_APPLIED` 로 돌아서서 `purchases` 도 재무 행도 안 쓴다.

    정할 자리는 물류·매입이다. 도착일이 목표 상태일보다 이른 승인을 (ㄱ) 승인일 당일
    상태에 싣는지 (ㄴ) 도착분 없이 매입·재무만 세우는지 (ㄷ) 애초에 리드타임 0 을
    매입안이 못 내게 막는지 — 셋 다 마스터가 혼자 고를 사실이 아니다.

    이 가드는 `target_state_date` 한 날에만 건다.

    회차 일정이 비어 있는 약정은 여기서 안 가른다 — 좁힐 것이 없는 상태이고,
      그건 재무가 `commitment_arrival_schedule` 로 먼저 막는 자리다.
    """
    if not commitment.arrival_schedule:
        return ""
    if still_incoming_on(commitment, target_state_date) is not None:
        return ""
    # 숫자로 적는다. "도착일이 목표 상태일보다 이르다" 를 사람이 바로 알아보게.
    도착일들 = ", ".join(
        f"{leg.seq}회차 {leg.arrival_date.isoformat()}" for leg in commitment.arrival_schedule
    )
    return (
        f"목표 상태일 {target_state_date.isoformat()} 에 앞으로 올 도착분이 없다:"
        f" 회차 도착일 {도착일들} (승인일 {commitment.as_of.isoformat()},"
        f" 리드타임 {commitment.inbound_lead_days}). 리드타임 0 은 계약상 허용되는데"
        " (app/logistics/schemas/snapshot.py inbound_lead_days ge=0)"
        " 그때 이 경로가 무엇을 해야 하는지가 정해진 적이 없다 — 물류·매입과 정할 자리다."
    )
