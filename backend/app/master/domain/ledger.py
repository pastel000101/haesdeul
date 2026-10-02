"""ledger.py — 승인 약정 → 매입 원장 (`purchases` · `purchase_items`)

승인은 재무 채무 · 물류 입고 예정과 함께 매입 원장에도 남아야 한다. 승인 약정을 원장
행으로 옮기는 계산이 여기다.

```text
승인 → ApprovedCommitment → build_purchase_rows  (순수 계산 · DB 를 안 부른다)
                          → persist_purchases    (repository/ledger.py · 주어진 conn 으로
                                                  write · commit 안 함)
```

왜 전이 모듈이 아닌가. 마스터 전이 경계(`service/transition.py`)에는 SQL 을 넣을 수
없다 — `test_전이_모듈에_SQL_이_없다` 가 원문을 읽어 막는다. 그 검사는 분담을
지키는 검사이므로 고치지 않는다. `service/transition.py` 는 언제 부를지만 정하고,
무슨 값을 어느 칸에 쓸지는 이 파일이 안다.

재무·물류 전이와 같은 모양이다. `build` 는 순수, `persist` 는 `(conn, rows)`
둘뿐이고 commit 하지 않는다. 커밋은 두 파트가 끝난 뒤 마스터가 한 번 한다.

`purchases` 는 마스터 소유가 맞다. 재무 `payables.purchase_id` 와 물류
`inbound source_ref` 가 둘 다 승인이 만든 매입 ID 를 가리키는데, 그 ID 를 짓는
자리는 승인을 쥔 마스터다 (`purchase_ids.purchase_id_for`). 부모 행이 없으면
재무 FK 가 막는다 — 그래서 원장 쓰기가 재무보다 먼저다.

다회차 원장은 회차 금액이 다 실려 있을 때만 연다. 매입이 `#265` 로 회차 금액을
싣고(seq1 6,182,450 + seq2 6,180,800 = total), 재무 `_payment_legs`
(`app/finance/domain/transition.py`)가 조건부로 지나가며, 물류가
`purchase_ids.get(leg.seq)` 로 회차별 매입 ID 를 받는다. 모든 leg 에 `amount_krw` 와
`payment_due_date` 가 다 있을 때만 열고, 하나라도 없으면 어느 seq 가 비었는지 이름을
대고 멈춘다.

매입 실측(2026-09-08) 당시 이 길로 값이 지나간 적이 없었다. `purchases` 20행 중 관통
승인분 4행은 전부 `-S1` 이고 번인 seed 16행은 회차 접미사 자체가 없었다. 분할 게이트도
`by_volume 0/31 · by_trend 0/81` 로 한 번도 열리지 않았다.

막는 문장의 주인은 `ledger_block_reason` 하나다. `service/transition.py` 의
`_ledger_blocked` 가 앞에서 그것을 부르고 `build_purchase_rows` 가 최후 방어로 다시
부른다 — 같은 사실을 두 곳이 다른 문장으로 말하지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.contracts.commitment import ApprovedCommitment, ArrivalLeg
from app.master.domain.purchase_ids import purchase_id_for

# ── 막는 갈래 ───────────────────────────────────────────────────────────
#
# 사유 문장을 키로 세면 안 된다. 문장에는 등급 이름과 회차 번호가 박혀 있어
# (`등급이 2개인데 매입 줄이 하나다 (특/상)`) 같은 갈래인데도 약정마다 다른 키가
# 된다. 세는 쪽은 이 이름으로 접는다.
#
# 이름의 주인은 여기 하나다. 문장의 주인이 `ledger_block_reason` 인 것과 같은
# 이유다 — 세는 쪽(`report/walk_summary.py`)이 자기 이름을 지으면 갈래가 둘이 된다.

#: 등급이 둘 이상이라 `purchase_items` 한 줄에 담을 자리가 없다.
BLOCK_GRADES = "등급 둘"

#: 회차가 둘 이상인데 어느 회차의 금액이 비어 있다.
BLOCK_LEG_AMOUNT = "회차금액 없음"

#: 지급일이 없다 (`purchases.payment_due_date` 는 NOT NULL).
BLOCK_PAYMENT_DUE = "지급일 없음"

#: 목표 상태일에 앞으로 올 도착분이 하나도 없다.
#:
#: 이 갈래의 문장은 여기서 안 짓는다. 판정도 문장도 주인은
#: `transition.arrival_block_reason` 이다 — 여기 있는 것은 이름뿐이고, 전이
#: (`service/transition.py`)가 이 이름을 가져다 붙인다. 이름까지 그쪽이 지으면 요약이
#: 세는 갈래가 둘이 된다.
BLOCK_NO_ARRIVAL = "도착분 없음"

#: 승인이 났는데 매입 원장에 한 행도 안 남는 갈래 전부. 순서가 뜻이다 —
#: 요약이 이 순서로 찍는다.
LEDGER_BLOCK_KINDS = (
    BLOCK_GRADES,
    BLOCK_LEG_AMOUNT,
    BLOCK_PAYMENT_DUE,
    BLOCK_NO_ARRIVAL,
)

#: 며칠을 재시도해도 같은 이유로 막히는 갈래.
#:
#: ```text
#: 등급이 둘        영영 안 된다 — 값이 오는 문제가 아니라 담을 칸이 없는 문제다
#: 그 밖 셋          매입·재무·물류가 값을 보내면 다음 날 풀린다
#: ```
#:
#: 이 구분이 없으면 「내일 되면 될 것」과 「영영 안 될 것」이 `NOT_APPLIED`
#: 한 값에 섞인다. 섞이면 재시도가 도는 것을 보고 "곧 풀리겠지" 로 읽는데,
#: 등급 둘은 몇 달을 돌아도 안 풀린다.
#:
#: 이것이 재시도를 멈추지 않는다. 여기 있는 갈래도 그대로 다시 세운다 —
#: 이 목록은 세는 쪽이 읽는 사실이지 흐름을 가르는 스위치가 아니다.
PERMANENT_BLOCK_KINDS = (BLOCK_GRADES,)


#: `numeric(18,6)` 이 담는 자릿수. 여기서 미리 맞춰 두지 않으면 DB 가 반올림한 뒤에
#: `purchase_items_check` 가 걸린다.
_SCALE = Decimal("0.000001")

#: DB CHECK: `|line_amount_krw - quantity_kg × unit_price_krw_per_kg| < 0.1`.
_LINE_TOLERANCE = Decimal("0.1")


class PurchaseLedgerNotWritable(ValueError):
    """원장을 쓸 수 없다. 틀린 행을 대신 쓰지 않는다.

    여기서 값을 맞춰 넣으면 그 순간 마스터가 남의 숫자를 만든 것이 된다.
    멈추면 `apply_approval` 이 `FAILED` 로 사유를 남긴다.
    """


def _seq_목록(seqs: Sequence[int]) -> str:
    """비어 있는 회차를 이름으로 부른다.

    "회차별 금액이 아직 없다" 처럼 뭉뚱그린 문장을 쓰지 않는다. 회차가 넷인데
    둘만 비어 있을 때 그 문장은 어느 것을 채워야 하는지 말해 주지 않는다.
    """
    return ", ".join(str(seq) for seq in seqs)


@dataclass(frozen=True)
class LedgerBlock:
    """원장을 못 쓴 한 건. 갈래와 문장을 같이 든다.

    둘을 따로 재지 않는다. 갈래를 내는 함수와 문장을 내는 함수를 따로 두면
    분기 순서가 두 곳에 적히고, 한쪽만 바뀌는 날 "등급 둘" 이라고 세면서
    회차 금액 문장을 찍는다.
    """

    #: `LEDGER_BLOCK_KINDS` 중 하나. 세는 쪽이 읽는 칸이다.
    kind: str
    #: 사람이 읽는 한 줄. 수량·등급·회차가 박혀 있어 키로 쓰면 안 된다.
    reason: str


def ledger_block(commitment: ApprovedCommitment) -> LedgerBlock | None:
    """매입 원장을 쓸 수 없으면 그 갈래와 문장. 쓸 수 있으면 `None`.

    판정의 주인은 이 함수 하나다. `ledger_block_reason` 은 여기서 문장만
    떼어 주는 겉면이고, 전이·최후 방어가 그것을 부른다.
    """
    grades = commitment.grades
    if len(grades) > 1:
        return LedgerBlock(
            kind=BLOCK_GRADES,
            reason=(
                f"등급이 {len(grades)}개인데 매입 줄이 하나다 ({' · '.join(grades)})"
                " — 아무 등급이나 고르거나 합치지 않는다"
            ),
        )
    legs = tuple(commitment.arrival_schedule)
    if len(legs) > 1:
        빈금액 = [leg.seq for leg in legs if leg.amount_krw is None]
        if 빈금액:
            return LedgerBlock(
                kind=BLOCK_LEG_AMOUNT,
                reason=(
                    f"{_seq_목록(빈금액)}회차 금액이 없어 원장을 쓸 수 없다"
                    " — 매입이 그 회차 금액을 아직 안 보냈다"
                ),
            )
    빈지급일 = [leg.seq for leg in legs if leg.payment_due_date is None]
    if 빈지급일:
        return LedgerBlock(
            kind=BLOCK_PAYMENT_DUE,
            reason=(
                f"{_seq_목록(빈지급일)}회차 지급일이 없다"
                " — 재무 purchase_payment_days(N5) 가 없어 만들 수 없다"
            ),
        )
    return None


def ledger_block_reason(commitment: ApprovedCommitment) -> str:
    """매입 원장을 쓸 수 없는 사유. 쓸 수 있으면 빈 문자열이다.

    이 문장의 주인은 여기 하나다. `service/transition.py` 의 `_ledger_blocked` 가 앞에서
    부르고 `build_purchase_rows` 가 최후 방어로 다시 부른다.

    회차가 둘 이상이면 회차마다 금액이 있어야 한다. 없는 회차를 총액이나
    수량 비율로 채우면 회차마다 단가가 다른 분할 매입에서 조용히 틀린 원장이
    생긴다. 재무 `_payment_legs`(`app/finance/domain/transition.py`)가 같은 자리를
    같은 조건으로 막는다 — 마스터가 앞에서 먼저 멈추는 것뿐이다.

    회차가 하나면 금액이 없어도 막지 않는다. 축이 하나뿐이라 총액이 곧 그
    회차 금액이고, 이것이 회차 금액을 안 싣던 옛 입력이 지나가던 길이다.
    재무가 `len(legs) > 1` 을 조건으로 붙여 같은 보존을 한다.

    지급일이 없으면 쓰지 않는다. `purchases.payment_due_date` 는 NOT NULL 이고
    그 값의 근거는 재무 N5 다. 없는 날짜를 지어내지 않는다.

    회차가 하나도 없는 경우는 여기서 가르지 않는다 — 그건 원장 이전에 재무가
    `commitment_arrival_schedule` 로 먼저 막는 상태이고, 그 사유를 여기서 다시
    쓰면 같은 사실이 두 문장으로 나간다.

    등급이 둘 이상이면 막는다. `purchase_items` 는 품목당 한 줄이고 `grade` 는
    그 한 줄에 한 칸이다 — 등급이 둘이면 담을 자리가 없다. 그때 아무 등급이나
    고르면 어느 등급이 원장에 남는지가 줄 순서에 걸리고, 합치면 없는 등급을
    마스터가 지어낸 것이 된다. 둘 다 에러 없이 틀린 원장을 만든다.

    줄을 등급별로 가르는 것은 여기가 아니다. `purchase_item_id` 규칙이 바뀌고
    `inventory_lots` · `inbound_schedules` 두 FK 가 걸린다 — 매입·물류와 함께
    정해야 하는 자리다. 마스터는 막는 데까지 한다.

    판정은 `ledger_block` 이 한다 — 갈래를 같이 내야 해서 거기 한 곳에 두고, 이 함수는
    문장만 떼어 준다.
    """
    blocked = ledger_block(commitment)
    return "" if blocked is None else blocked.reason


def sim_run_id_for(commitment: ApprovedCommitment, *, sim_run_id: str | None) -> str:
    """이 승인이 속한 시뮬레이션 실행. 받은 축을 돌려준다.

    마스터가 지어내지 않는다. `purchases.sim_run_id` 는 NOT NULL 이고
    `sim_runs` 를 참조하는 FK 다 — 없는 키를 넣으면 FK 가 막는다.

    축의 계약은 `master_agent_runs.sim_run_id` 다 (`Refs #150`). `service/decision.py` 의
    `record_decision` 이 결정이 걸린 실행 행에서 그 값을 읽어 여기까지 흘린다. 안 받으면
    터진다.

    상수로 메우지 않는다. 축을 못 받았을 때 조용히 번인으로 떨어지면 재무
    채무와 매입 원장이 서로 다른 실행에 앉는다 — 재무는 자기 축(`finance_states`)
    을 읽고 여기만 번인을 쓰기 때문이고, 그 어긋남은 아무 오류도 안 낸다.

    `commitment` 을 여전히 받는다. 지금은 안 읽지만 "이 승인의 축" 이라는
    물음이 그대로이고, 부르는 쪽이 승인마다 축을 짚는다는 사실이 인자에 남아야 한다.

    :param sim_run_id: 이 결정이 걸린 실행. 기본값이 없다.
    :raises ValueError: 축이 비었을 때. 막고 사유를 낸다.
    """
    if not sim_run_id or not sim_run_id.strip():
        raise ValueError(
            "sim_run_id 없이 매입 원장을 쓸 수 없다 — 어느 실행의 장부인지가 없으면"
            " 재무 채무와 다른 실행에 앉는다. 상수로 메우지 않는다"
        )
    return sim_run_id


@dataclass(frozen=True)
class PurchaseWrite:
    """승인 회차 하나가 만드는 매입 Header 한 행과 그 아래 품목 한 줄.

    한 덩어리로 두는 이유는 둘이 같은 사실의 앞뒤이기 때문이다 — header 만 쓰고
    품목을 빠뜨리면 `purchases.total_amount_krw` 가 `purchase_items` 합계와 갈린다.

    `item_id` 가 여기 없다. 그것은 `items` 표가 주인이고, 표를 읽으려면
    커넥션이 필요하다 — 순수 계산 자리에서 한글 품목명을 `ITEM-…` 로 바꾸는
    하드코딩 맵을 만들면 그 맵이 또 하나의 어휘가 되어 `items` 와 갈린다.
    여기는 품목명을 그대로 들고 있고, 조회는 `persist_purchases` 가 한다.
    """

    purchase_id: str
    sim_run_id: str
    purchase_date: date
    payment_due_date: date
    total_amount_krw: Decimal
    proposal_id: str
    scenario_id: str
    #: 계약 품목명(`배추` 등). `items.item_name` 으로 조회할 열쇠다.
    item_name: str
    quantity_kg: Decimal
    unit_price_krw_per_kg: Decimal
    line_amount_krw: Decimal

    #: 등급(`특·상·중·하`). 약정이 실어 온 값 그대로이고, 안 오면 `None` 이다.
    #:
    #: 등급이 둘 이상인 약정은 여기까지 오지 않는다 — `ledger_block_reason` 이
    #: 앞에서 막는다. 그래서 여기 담기는 등급은 늘 하나이거나 없다.
    #:
    #: 번인의 `상품` 과 새 `특/상/중/하` 가 한 칸에 섞이는 것을 아는 채로 둔다.
    #: 매입이 "매핑표를 만들 수 없다" 고 했고 그 판단이 맞다 — 여기서 어휘를
    #: 변환하면 근거 없는 대응표가 원장의 사실이 된다.
    grade: str | None = None


def build_purchase_rows(
    commitment: ApprovedCommitment, *, purchase_ids: Mapping[int, str], sim_run_id: str | None
) -> tuple[PurchaseWrite, ...]:
    """승인 약정을 매입 원장 행으로 옮긴다. 계산만 한다 — DB 를 부르지 않는다.

    회차마다 한 행이다. `purchases.purchase_date` 가 header 에 하나뿐이라
    매입일이 다른 회차를 한 header 에 담을 수 없다 (`purchase_ids.purchase_id_for`).

    :param purchase_ids: 회차(`seq`) → `purchase_id` 매핑. 재무에 넘기는 것과 같은
        매핑이다 — 여기서 따로 지으면 `payables.purchase_id` 가 가리키는 부모 행과
        이름이 갈린다.
    :param sim_run_id: 어느 실행의 장부인가. 받아서 흘린다 — 여기서 상수를
        읽지 않는다 (`sim_run_id_for` 의 근거).
    :raises PurchaseLedgerNotWritable: 회차 금액이나 지급일이 없거나, 단가가
        DB CHECK 를 못 지킬 때.
    :raises ValueError: 축을 못 받았을 때.
    """
    legs = tuple(commitment.arrival_schedule)
    if not legs:
        # 빈 것은 예외가 아니다. 회차 일정을 못 만든 약정도 승인은 살아 있고
        # (`commitment.notes` 가 왜 못 만들었는지 적는다), 그때 원장에 쓸 매입이
        # 없다는 것은 정상 상태다 — 물류 `build_next_inventory` 가 빈 목록을
        # 정상으로 보는 것과 같다. 매입일도 지급일도 여기서 지어내지 않는다.
        return ()
    # 최후 방어다. `service/transition.py` 의 `_ledger_blocked` 가 같은 함수를 앞에서 이미
    # 불렀다. 그래도 여기서 다시 부르는 이유는 원장 계산이 전이 밖에서도 불릴 수
    # 있기 때문이고, 두 자리가 같은 문장을 쓰므로 판정이 갈리지 않는다.
    blocked = ledger_block_reason(commitment)
    if blocked:
        raise PurchaseLedgerNotWritable(blocked)

    축 = sim_run_id_for(commitment, sim_run_id=sim_run_id)
    return tuple(
        _row_for_leg(commitment, leg, purchase_ids=purchase_ids, sim_run_id=축) for leg in legs
    )


def _row_for_leg(
    commitment: ApprovedCommitment,
    leg: ArrivalLeg,
    *,
    purchase_ids: Mapping[int, str],
    sim_run_id: str,
) -> PurchaseWrite:
    """회차 하나가 만드는 매입 Header 한 행과 품목 한 줄."""
    if leg.seq not in purchase_ids:
        # 매핑에 값이 하나뿐이라고 그것을 집지 않는다 (재무 `_purchase_id_for_leg`
        # 와 같은 규율). 엉뚱한 매입에 품목이 붙어도 에러가 안 난다.
        raise PurchaseLedgerNotWritable(f"{leg.seq}회차의 purchase_id 가 없다.")
    purchase_id = purchase_ids[leg.seq]
    if purchase_id != purchase_id_for(commitment, leg.seq):
        raise PurchaseLedgerNotWritable(
            f"{leg.seq}회차 purchase_id 가 승인이 짓는 값과 다르다: {purchase_id!r}"
        )

    quantity = _scaled(leg.qty_kg)
    if leg.amount_krw is None:
        # 회차가 하나뿐인 옛 입력만 여기로 온다 (`ledger_block_reason` 이 다회차의
        # 빈 금액을 위에서 이미 막았다). 축이 하나뿐이라 총액이 곧 그 회차 금액이고,
        # 재무 `_payment_legs`(`app/finance/domain/transition.py`)가 같은 보존을 한다.
        amount = _scaled(commitment.total_amount_krw)
        나눌_수량 = _scaled(commitment.total_qty_kg)
    else:
        # 다회차에서 총액을 쓰지 않는다. 회차마다 총액이 실리면 원장이 승인
        # 총액의 회차 수만큼 부풀어 오르고, 재무 채무 합과도 갈린다. 금액과 수량을
        # 같은 축에서 집는다 — 회차 금액 ÷ 회차 수량이다.
        amount = _scaled(leg.amount_krw)
        나눌_수량 = quantity
    if 나눌_수량 <= 0:
        raise PurchaseLedgerNotWritable(f"{leg.seq}회차 수량이 0 이하라 단가를 만들 수 없다.")

    # 회차 금액 합 = 총액은 여기서 다시 세지 않는다. `commitment.__post_init__`
    # 이 이미 본다 (`app/contracts/commitment.py`). 두 곳이 세면 허용 오차가 갈리는 날
    # 같은 약정을 한 곳은 통과시키고 한 곳은 막는다.
    unit_price = _scaled(amount / 나눌_수량)
    line_amount = amount
    drift = abs(line_amount - quantity * unit_price)
    if drift >= _LINE_TOLERANCE:
        # 단가를 억지로 맞추지 않는다. `numeric(18,6)` 으로 잘린 단가로는
        # `quantity × unit_price` 가 총액을 못 맞추는 날이 있고, 그때 line 금액을
        # 곱셈 결과로 바꾸면 원장 총액이 승인 총액과 갈린다.
        raise PurchaseLedgerNotWritable(
            f"단가 자릿수로 Line 금액을 맞출 수 없다 (차 {drift}원, DB 허용 0.1 미만)."
            " 총액을 고쳐 맞추지 않는다."
        )

    if leg.payment_due_date is None:
        # 여기까지 오면 `build_purchase_rows` 의 최후 방어를 지나쳐 들어온 것이다.
        # 사유 문장은 지어내지 않고 주인에게 다시 묻는다.
        raise PurchaseLedgerNotWritable(ledger_block_reason(commitment))

    return PurchaseWrite(
        purchase_id=purchase_id,
        sim_run_id=sim_run_id,
        purchase_date=leg.purchase_date,
        payment_due_date=leg.payment_due_date,
        total_amount_krw=amount,
        proposal_id=f"PROP-{commitment.request_id}",
        scenario_id=f"SCN-{commitment.request_id}-{commitment.scenario_label}",
        item_name=leg.item,
        quantity_kg=quantity,
        unit_price_krw_per_kg=unit_price,
        line_amount_krw=line_amount,
        # 등급은 회차 축이 아니라 약정 축에서 집는다. 등급이 둘 이상인 약정은
        # `ledger_block_reason` 이 이미 막았으므로 남은 것은 하나이거나 없다.
        # `leg` 에서 읽지 않는 이유가 그것이다 — 회차에 등급이 붙어 있지 않다.
        grade=commitment.grades[0] if commitment.grades else None,
    )


def _scaled(value: Any) -> Decimal:
    """`numeric(18,6)` 자릿수로 맞춘다.

    `Decimal(str(x))` 를 쓴다. `Decimal(float)` 은 0.1 이 갖고 있는 이진 오차를
    그대로 들여와 금액에 안 보이는 꼬리를 남긴다 (`logistics/domain/transition.py` 와 같은 이유).
    """
    raw = value if isinstance(value, Decimal) else Decimal(str(value))
    return raw.quantize(_SCALE, rounding=ROUND_HALF_UP)
