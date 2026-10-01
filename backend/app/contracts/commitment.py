"""
commitment.py — 승인 매입의 **확정 입고 약정** 계약 (H1)

사람이 매입안을 승인하면 마스터가 이 약정을 만들고, 물류(전이 · 취소 · 미래 점유)와
재무(전이) · 마스터 원장이 읽는다.

🟢 **자리: `app/contracts/commitment.py`** (2026-09-29 재구성 BL-011 — 전에는
  `app/master/commitment.py` 한 파일에 타입과 조립 함수가 같이 있었다). 물류가 약정을
  받으려고 마스터를 import 하던 역방향 의존을 끊으려고 **타입만** 여기로 옮겼다.
  약정을 **만드는** 함수(`build_commitment` · `with_purchase_record`)와 사람이 적은
  실매입 회차(`RecordedLeg`)는 마스터의 변환이라 마스터(지금
  `app/master/domain/commitment.py`)에 남는다.

★ `__post_init__` 의 검사(계약 품목 · 회차 합 = 총량 · 회차 품목 · 회차 금액 합 = 총액)는
  **계약의 일부**라 타입과 함께 왔다 — 누가 만들든 성립하지 않는 약정은 서지 않는다.
  필드 · 기본값 · 검사 · 주석은 옮기기 전과 같다.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import date

from app.contracts.core import ITEMS

__all__ = [
    "ITEM_CODES",
    "ApprovedCommitment",
    "ArrivalLeg",
    "CommitmentNotBuildable",
    "SourcingLine",
]

#: 🔴 `contracts/core.py` 의 `ItemCode` 는 `str` 별칭이고 품목 목록은 **주석**이다.
#:   그래서 `InventoryLot(item="(승인분)")` 같은 값이 품목 자리에 들어가도 아무도
#:   안 막았다 (2026-09-01 실측). 여기서는 값으로 막는다.
#:
#: ★ 다만 **목록을 여기서 다시 세지 않는다.** 2026-09-03 에 피마늘을 뺄 때, 계약은
#:   셋인데 여기만 넷으로 남는 것이 정확히 이 파일이 만들 수 있는 사고였다.
#:   막는 자리는 여기지만 무엇을 막을지는 계약이 정한다.
ITEM_CODES: frozenset[str] = frozenset(ITEMS)


class CommitmentNotBuildable(ValueError):
    """약정을 만들 수 없다. **비어 있는 약정을 대신 만들지 않는다.**

    ★ 여기서 조용히 0 이나 빈 값을 채우면, 물류가 *"입고 예정이 없다"* 로 읽는다.
      없는 것과 못 만든 것은 다르다 (§1.2-10).
    """


@dataclass(frozen=True)
class SourcingLine:
    """등급 조달 1줄. **매입이 보낸 모양 그대로다** (`sourcing_plan[]`).

    ★ 이름도 값도 안 바꾼다. `grade_unit_price` 를 `unit_price_krw_per_kg` 로 고쳐
      부르지 않고, 수량을 다시 세지도 않는다 — `amount_krw` 를 마스터가 안 만드는
      것과 같은 규율이다 (§3.2.2). 여기는 **옮기는 자리**다.

    ⚠️ `contracts/core.py` 의 `SourcingLot` 을 쓰지 않는다. 그쪽은 단가 이름이
      `unit_price_krw_per_kg` 라 매입이 보낸 `grade_unit_price` 를 옮기려면 이름을
      바꿔야 하고, 그 순간 **한 사실이 두 이름**이 된다.

    🔴 **회차(`ArrivalLeg`)와 다른 축이다.** 한 회차가 여러 등급을 담을 수 있고 한
       등급이 여러 회차에 걸칠 수 있다. 오늘은 둘 다 하나뿐이지만(#308 이 분할을
       못 세우고 있다) 그것이 계약이 아니다 — 그래서 등급을 회차에 붙이지 않고
       **목록으로 따로 나른다.** 분할이 서는 날 축이 갈려도 재료가 이미 여기 있다.
    """

    grade: str
    market: str | None = None
    qty_kg: float | None = None
    grade_unit_price: float | None = None


@dataclass(frozen=True)
class ArrivalLeg:
    """입고 1회분. **품목이 붙어 있다.**

    🔴 **`grade` 가 여기 없다.** 넣으면 *"회차 하나 = 등급 하나"* 가 계약으로 굳고,
       `#308` 이 풀려 2·3회차가 서는 날 **말없이 틀린다.** 등급은
       `ApprovedCommitment.sourcing_plan` 이 목록으로 나른다.
    """

    item: str
    qty_kg: float
    arrival_date: date
    purchase_date: date
    seq: int

    amount_krw: float | None = None
    """회차 금액. **매입이 보낸다** — 마스터가 총액을 회차 수로 나눠 만들지 않는다.

    ★ 여기는 스칼라다. 약정 하나가 품목 하나라 회차마다 품목이 하나뿐이다
      (`__post_init__` 이 `leg.item != self.item` 을 이미 막는다).
      `SplitLeg.amount_krw` 가 품목별 매핑인 것과 모순이 아니라, 축이 이미 좁혀진 자리다.

    ★ `None` 이 기본이고 매입이 값을 보내기 전까지는 늘 None 이다.
      0.0 으로 채우지 않는다 — 없는 것과 0 원은 다르다 (§1.2-10).
    """

    payment_due_date: date | None = None
    """매입대금 지급예정일. **매입일 + N5(재무 `purchase_payment_days`)** 다.

    ★ **`arrival_date` 와 완전히 같은 모양이다.** 물류가 봉투로 N4 를 주고 마스터가
      도착일을 만들듯, 재무가 봉투로 N5 를 주고 마스터가 지급일을 만든다 —
      **값은 부서가 공급하고 계산은 쓰는 쪽이 한다.** 마스터가 재무 DB 를 다시 읽으면
      같은 사실의 주인이 둘이 된다.

    🔴 **N5 가 없으면 `None` 이다 — 0 으로 대체하지 않는다.** N4 를 0 으로 못 쓰게 한
       것과 같은 이유다. 0 이면 *"오늘 승인분이 오늘 지급"* 이 되어 지급일이라는
       사실 자체가 사라진다. 없으면 없는 채로 두고, `purchases.payment_due_date` 가
       NOT NULL 이므로 **원장 쓰기가 그때 멈춘다** (`master/service/transition.py`).
    """


@dataclass(frozen=True)
class ApprovedCommitment:
    """승인 1건이 만드는 확정 입고 약정.

    ★ `total_qty_kg` 는 **안이 적은 값**이고, `sum(leg.qty_kg)` 와 어긋나면
      `__post_init__` 이 막는다. 마스터가 둘 중 하나를 고쳐 맞추지 않는다 —
      고친 값이 근거가 되면 그건 검증이 아니라 창작이다.
    """

    approval_id: str
    request_id: str
    as_of: date
    item: str
    scenario_label: str
    total_qty_kg: float
    total_amount_krw: float
    arrival_schedule: tuple[ArrivalLeg, ...] = ()
    sourcing_plan: tuple[SourcingLine, ...] = ()
    """매입이 짠 **등급별 조달**. 안이 적은 목록 그대로다.

    ★ **회차와 다른 축이라 목록이다.** 한 줄뿐인 오늘도 목록이고, 여럿이 되는 날
      모양이 안 바뀐다 — 스칼라로 접었다가 나중에 펴면 그 사이 쓰인 코드가 전부
      *"등급은 하나"* 를 가정하고 있다.

    ★ **비어 있을 수 있다.** 매입이 안 실어 보내면 빈 목록이고, 그러면 원장
      `purchase_items.grade` 도 `NULL` 로 남는다 — 지어내지 않는다 (§1.2-10).
    """
    inbound_lead_days: float | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.item not in ITEM_CODES:
            raise CommitmentNotBuildable(
                f"계약 품목이 아니다: {self.item!r}. 가능: {', '.join(sorted(ITEM_CODES))}"
            )
        if not self.arrival_schedule:
            return
        drift = self.total_qty_kg - sum(leg.qty_kg for leg in self.arrival_schedule)
        if abs(drift) > 1e-6:
            raise CommitmentNotBuildable(
                f"회차 합이 총량과 어긋난다 (차 {drift:g}kg) — 마스터가 맞춰 주지 않는다."
            )
        if any(leg.item != self.item for leg in self.arrival_schedule):
            raise CommitmentNotBuildable("회차 품목이 약정 품목과 다르다.")
        # ★ 금액도 수량과 같다 — 전 회차에 실려 있을 때만 본다. 하나도 없으면 오늘
        #   상태이므로 검사하지 않는다. 허용 오차를 수량과 같은 1e-6 으로 둔 것은
        #   같은 자리에서 다른 상수를 쓰면 왜 다른지를 아무도 모르기 때문이다.
        loaded = [leg.amount_krw for leg in self.arrival_schedule]
        if all(a is not None for a in loaded):
            drift = self.total_amount_krw - sum(a for a in loaded if a is not None)
            if abs(drift) > 1e-6:
                raise CommitmentNotBuildable(
                    f"회차 금액 합이 총액과 어긋난다 (차 {drift:g}원) — 마스터가 맞춰 주지 않는다."
                )

    @property
    def first_arrival(self) -> date | None:
        return min((leg.arrival_date for leg in self.arrival_schedule), default=None)

    @property
    def grades(self) -> tuple[str, ...]:
        """실려 온 등급. **중복 없이 · 온 순서대로 · 값은 그대로.**

        ★ **같은 등급이 두 줄이면 등급은 하나다.** 시장이 달라 줄이 갈린 것뿐이고
          등급 칸에 담길 값은 여전히 하나다 — 그때까지 막으면 담을 수 있는 것을
          못 담는다.

        🔴 **겹침 판정만 NFC 로 하고, 돌려주는 값은 받은 그대로다.** 조합형 `특` 과
           분해형 `특` 을 다른 등급으로 세면 등급이 하나인 안이 *"둘"* 로 읽혀 원장이
           엉뚱하게 멈춘다. 값 자체를 정규화해 내보내면 그건 매입이 보낸 문자열을
           마스터가 고쳐 쓴 것이다 — **비교만 접고 값은 안 만진다.**
        """
        seen: set[str] = set()
        표: list[str] = []
        for line in self.sourcing_plan:
            열쇠 = unicodedata.normalize("NFC", line.grade)
            if 열쇠 in seen:
                continue
            seen.add(열쇠)
            표.append(line.grade)
        return tuple(표)
