"""매입 원장 ID 규칙 — 승인 회차 · 결정 회차에서 purchase_id · purchase_item_id 를 짓는다."""

from __future__ import annotations

from app.contracts.commitment import ApprovedCommitment

# ── purchase_id 짓기 ────────────────────────────────────────────────────
#
# 순수 함수다 — DB 를 부르지 않는다. ID 를 시퀀스나 채번 표에서 받아 오면 같은 승인을
# 두 번 반영할 때 다른 ID 가 나오고, 그 순간 UPSERT 가 겹쳐 쓰는 대신 행을 하나 더
# 만든다.
#
# 멱등: 결정론이어야 한다. 같은 승인이면 언제 몇 번을 불러도 같은 ID 가 나온다. 난수도,
#   순번 카운터도, 시각도 쓰지 않는다. 물류 `inbound_id` 가 `INB-{approval_id}-{seq}` 인
#   것이 정확히 같은 이유다 (`logistics/domain/transition.py`: "순번 카운터나 난수를 쓰면
#   두 번째 반영이 같은 물건을 다른 건으로 만들어 `in_transit` 이 부풀고 … 대조할
#   열쇠(B-1)도 사라진다").
#
# 접두사는 번인 데이터를 따른다. 번인에 이미 `PUR-KIMCHI-001` 과
#   `PITEM-SAFETY-001-BAECHU` 가 있다 — 새 규칙을 짓지 않고 그 모양에 맞춘다.
#
#   ```text
#   purchase_id       PUR-{request_id}-D{decision_seq}-S{seq}
#   purchase_item_id  PITEM-{purchase_id 에서 앞의 "PUR-" 를 뗀 나머지}-{item_code}
#   ```


def _decision_seq_of(commitment: ApprovedCommitment) -> int:
    """`approval_id` 에서 결정 회차를 꺼낸다.

    형식에 기대는 자리다. `decision_seq` 는 `ApprovedCommitment` 에 직접 없고,
    `commitment.py` 가 `approval_id = f"H1-{request_id}-{decision_seq}"` 로 지어 넣은 것을
    되읽는 수밖에 없다.

    기대는 이상 어긋나면 조용히 넘기지 않는다. 여기서 0 이나 1 로 대신 채우면 서로 다른
    결정이 같은 `purchase_id` 를 갖게 되고, UPSERT 가 앞선 결정의 매입을 덮어쓴다. 틀린
    ID 로 계속 가는 것보다 멈추는 편이 낫다.
    """
    prefix = f"H1-{commitment.request_id}-"
    approval_id = commitment.approval_id
    tail = approval_id[len(prefix) :] if approval_id.startswith(prefix) else ""
    if not (tail.isascii() and tail.isdigit()):
        raise ValueError(
            f"approval_id 가 'H1-{{request_id}}-{{decision_seq}}' 형식이 아니다:"
            f" {approval_id!r} (request_id={commitment.request_id!r})."
            " 결정 회차를 지어내지 않는다."
        )
    return int(tail)


def purchase_id_prefix_for(request_id: str, decision_seq: int) -> str:
    """승인 하나가 만드는 매입 Header ID 들의 공통 앞머리.

    ```text
    PUR-{request_id}-D{decision_seq}-S      ← 여기까지가 승인 하나를 가리킨다
    PUR-{request_id}-D{decision_seq}-S1     ← 회차가 붙으면 행 하나다
    ```

    왜 앞머리를 따로 내주나. "이 승인이 원장에 닿았나" 를 묻는 자리
    (`pending_transition`)는 회차 번호를 모른다 — 회차는 약정을 조립해야 나오고, 조립하기
    전에 먼저 걸러야 하기 때문이다.

    그 자리가 문자열을 다시 짓게 두지 않는다. `f"PUR-{request_id}-D{seq}-S"` 를 거기 한 줄
    복사하면 ID 규칙의 주인이 둘이 되고, 한쪽만 바뀌는 날 미적용을 찾는 식이 조용히 늘
    0건을 돌려준다 (에러는 안 난다).

    `purchase_id_for` 가 이 함수를 쓴다. 그래서 둘이 갈릴 수가 없다.
    """
    return f"PUR-{request_id}-D{decision_seq}-S"


def purchase_id_for(commitment: ApprovedCommitment, seq: int) -> str:
    """이 승인의 회차 하나가 만드는 매입 Header ID.

    ```text
    PUR-{request_id}-D{decision_seq}-S{seq}
    ```

    회차마다 하나인 이유는 `purchases.purchase_date` 가 header 에 하나뿐이기 때문이다.
    회차마다 매입일이 다른 분할 매입을 한 header 에 담으면 그중 하나의 날짜만 남는다.

    앞머리를 여기서 다시 짓지 않는다 — 주인은 `purchase_id_prefix_for` 다.
    """
    return f"{purchase_id_prefix_for(commitment.request_id, _decision_seq_of(commitment))}{seq}"


def purchase_item_id_for(purchase_id: str, item_code: str) -> str:
    """매입 Header 아래 품목 한 줄의 ID.

    `PUR-` 를 떼고 `PITEM-` 을 붙인다 — 접두사가 둘 겹치면
      `PITEM-PUR-…` 이 되어 번인의 `PITEM-SAFETY-001-BAECHU` 와 모양이 갈린다.
    """
    if not purchase_id.startswith("PUR-"):
        raise ValueError(f"purchase_id 가 'PUR-' 로 시작하지 않는다: {purchase_id!r}")
    return f"PITEM-{purchase_id[len('PUR-') :]}-{item_code}"


# ── 경계 ────────────────────────────────────────────────────────────────


def purchase_ids_of(commitment: ApprovedCommitment) -> dict[int, str]:
    """이 승인이 만든 회차별 매입 header ID. 승인 때와 같은 매핑이다.

    새로 조회하지 않는다. `purchase_id_for` 가 결정론이라 취소 시점에 다시 조립해도 같은
    값이 나온다 — 표를 읽으면 "원장이 말하는 것" 과 "약정이 말하는 것" 이 갈릴 자리가
    하나 더 생긴다.

    재무가 요구한 계약이다 (`#302 §3`) — "payable_id 문자열 파싱도, approval_id 에서의
    추론도, 첫 회차 ID 임의 선택도 하지 않겠습니다."
    """
    return {leg.seq: purchase_id_for(commitment, leg.seq) for leg in commitment.arrival_schedule}
