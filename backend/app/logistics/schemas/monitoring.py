"""Agent 층의 어휘와 순수 규칙. **DB 도 LLM 도 여기 없다.**

```text
관측   WarehouseObservation ← ObservedLot · ObservedCapacity
근거   ExceptionEvidence     fact · value · unit · source · source_id · observed_as_of
판정   DetectedCondition     탐지기(순수 함수)가 내는 "지금 참인 조건" 한 줄
행     ExceptionRow          logistics_exceptions 한 행
결과   DetectOut             한 번의 탐지가 연 것 · 갱신한 것 · 닫은 것
```

🔴 **관측일 규칙(§18)의 주인이 이 파일이다.** `derive_observed_as_of` 하나가
   *"이 값을 언제부터 알 수 있었나"* 를 정하고, 4 Mode 회신(`service/` mode 조립)과
   Exception 근거가 **같은 함수**를 지난다. 두 벌로 적으면 같은 사실이 어느 자리를
   지나느냐에 따라 «쟀다» 와 «안 쟀다» 로 갈린다.

🔴 **관측일은 Lot 이 아니라 «사실» 에 붙는다.** 한 Lot 안에서도 축마다 다르다.

```text
received_at        입고일        불변    ← 입고 그날 이후 안 바뀐다
remaining_qty_kg   마지막 이동일  가변    ← D1 입고 · D5 출고 · D8 출고면 D8 이다
status             아래 규칙      가변
uncommitted_kg     둘의 늦은 쪽   가변    ← 지금은 예약 축을 못 대서 None
정책값             없음                  ← 유효일 칸이 없다
```

   ⚠️ **Lot 하나에 관측일 하나**로 두면 저 다섯이 한 날짜로 뭉개진다. 그러면 D8 까지
      출고된 500kg 이 *"D1 부터 알던 사실"* 로 적히고, 그 거짓은 값이 아니라 **날짜**에
      남아 아무도 못 본다.

★ 2026-09-30 재구성 BL-015: `logistics/monitoring/schemas.py` 에서 옮겼다. 관측일
  규칙 둘(`derive_observed_as_of` · `snapshot_observed_as_of`)은 모델이 쓰므로 이 파일에 남았다.
  문제 장부 읽기 결과(`LiveExceptionsAt`) · 빈 근거 실패 · 닫힌 날 미확인 어휘는
  `logistics/monitoring/exceptions.py` 에서, 관측의 미확인 어휘와 `ObservationNotReady` 는
  `logistics/monitoring/observe.py` 에서 이리로 왔다(탐지기 · 관측일 도우미 · 관측이 같이 읽는다).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from app.logistics.schemas.snapshot import InventoryLogisticsSnapshot

# ---------------------------------------------------------------------------
# 어휘 — DB CHECK 와 **글자 그대로 같아야 한다**
# ---------------------------------------------------------------------------

#: 신선도 압박. Core 메인 탐지기 (상세설계 §6.2 Core ①).
FRESHNESS_PRESSURE = "FRESHNESS_PRESSURE"
#: 창고 용량 압박. Core 보조 탐지기 (§6.2 Core ②).
CAPACITY_PRESSURE = "CAPACITY_PRESSURE"
#: 🔴 **예약 어휘다 — Commit 2 는 이 코드를 만들지 않는다.** 자동 유지보수가 켜진
#: 실행에서는 개장 때 폐기되어 열릴 틈이 없고, 꺼진 실행에서는 압박 Exception 이
#: `ESCALATED:FRESHNESS_EXPIRED` 로 닫히며 그 사실만 남는다 (§7.1 E).
FRESHNESS_EXPIRED = "FRESHNESS_EXPIRED"

ExceptionCode = Literal["FRESHNESS_PRESSURE", "CAPACITY_PRESSURE", "FRESHNESS_EXPIRED"]
SubjectType = Literal["LOT", "WAREHOUSE"]
Severity = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
ExceptionStatus = Literal["OPEN", "PROPOSED", "RESOLVED", "DISMISSED"]
DetectPhase = Literal["AFTER_INBOUND", "AFTER_OUTBOUND"]

#: 살아 있는 Exception 의 상태들. 🔴 **중복 방지 축이 이 둘이다** — DB 의 부분 유일
#: 인덱스(`uq_logistics_exceptions_live`)와 같은 집합이어야 한다.
LIVE_STATUSES: tuple[str, ...] = ("OPEN", "PROPOSED")

#: `subject_type='WAREHOUSE'` 인 Exception 의 대상 식별자. MVP 창고는 하나다
#: (`warehouses.network_type = 'SINGLE_HUB_MVP'`). 🔴 **창고 ID 를 지어내지 않는다** —
#: 스냅샷이 창고를 식별하지 않으므로 없는 값을 적는 대신 축 이름을 그대로 쓴다.
WAREHOUSE_SUBJECT_ID = "WAREHOUSE"


# ---------------------------------------------------------------------------
# 관측일 (§18) — **하나의 규칙, 하나의 함수**
# ---------------------------------------------------------------------------

#: 정책 표의 관측일. 🔴 **`None` 이고, 그것이 사실이다.**
#:
#: `agent_policy_config` · `item_storage_policies` · `item_turnover_policies` 에는
#: 유효일 칸이 없어 *"그날 그 정책이었나"* 를 알 수 없다
#: (`schemas/historical.CAPACITY_BASIS_CURRENT_ACTIVE_POLICY` 가 같은 한계를
#: 응답에 적는다). 그래서 정책값이 계산에 들어간 결과의 관측일은 `None` 이다 —
#: `as_of` 를 편의상 대입하면 **안 잰 것이 잰 것으로 세어진다.**
#:
#: ★ 유효일 칸이 생기는 날 바꿀 자리는 **여기 하나**다.
POLICY_OBSERVED_AS_OF: date | None = None


#: 예약·할당 축의 관측일. 🔴 **`None` 이고, 그것이 사실이다.**
#:
#: `uncommitted_kg` 는 *"지금 이 Lot 에 살아 있는 할당"* 을 뺀 값인데, **그 그림이
#: 언제부터 참이었나**를 댈 근거가 DB 에 없다.
#:
#: ```text
#: 붙은 날   inventory_allocations.decided_at   ✅ 시뮬레이션 시각이다 (outbound._sim_day)
#: 빠진 날   release_reservation                ✅ released_as_of 를 같은 UPDATE 에 적는다
#:           cancel_allocation                  🔴 **날짜를 하나도 안 적는다**
#: ```
#:
#: 🔴 **빠진 날 하나를 못 대면 전체를 못 댄다.** `outbound.cancel_allocation` 은
#:    `status = 'CANCELLED'` 만 쓰고 업무 날짜를 남기지 않는다 — 그 한 번으로 그 Lot 의
#:    미확정 물량이 늘어나는데, 늘어난 날을 아무도 모른다. 붙은 날들만 모아 max 를
#:    내면 **실제보다 이른 날**이 적히고, 그것은 «안 쟀다» 가 «쟀다» 로 둔갑하는 것과
#:    같은 종류의 거짓이다.
#:
#: ⚠️ **스냅샷 쪽에도 날짜가 없다.** `OutboundCommitment` 는 (품목 · Lot · 수량) 셋뿐이고
#:    (`logistics.schemas`), `repository/current.get_outbound_commitments` 는 `as_of` 축 없이
#:    **지금 status** 를 읽는다.
#:
#: ★ 이 값이 날짜를 내기 시작하려면 **`cancel_allocation` 이 날짜를 적어야 한다**
#:   (`released_as_of` 가 선 것과 같은 판). 그때 바꿀 자리는 **여기 하나**다.
COMMITMENT_OBSERVED_AS_OF: date | None = None


def derive_observed_as_of(values: Iterable[date | None]) -> date | None:
    """파생값의 관측 기준일. **가장 늦은 것, 하나라도 모르면 `None`.**

    ```text
    전부 날짜      max()    — 늦게 온 입력이 조용히 숨지 않는다
    하나라도 None  None     — «안 쟀다»
    입력이 없음    None     — 잴 것이 없었다
    ```

    🔴 **`as_of` · 오늘 · `created_at` 으로 메우지 않는다** (`AgentReply.observed_at`
       의 규칙 그대로 · `scheduler._observed_ats`). 메우는 순간 진도가 거짓이 된다.

    ★ **가장 늦은 것을 고른다.** 이 값은 안전을 재는 칸이 아니라 **위험을 드러내는**
      칸이다 — 가장 이른 것을 고르면 늦게 온 입력이 안 보인다.
    """
    latest: date | None = None
    empty = True
    for value in values:
        empty = False
        if value is None:
            return None
        if latest is None or value > latest:
            latest = value
    return None if empty else latest


#: 스냅샷만 보고 잰 **물리 잔량 축**의 관측일. 🔴 `None` 이다 — 스냅샷은 원장을
#: 싣지 않는다 (`InventoryLotSnapshot` 의 날짜는 `received_at` 하나뿐이다).
#:
#: 🔴 **`received_at` 으로 메우지 않는다.** 입고일이 재는 것은 *"이 Lot 이 있다"* 이지
#:    *"지금 500kg 이다"* 가 아니다. 잔량의 관측일은 원장의 마지막 이동일이고
#:    (`readmodel/historical.ledger_state_by_lot`), 스냅샷 경로에는 그 원장이 없다.
#:
#: ★ 탐지기 쪽은 원장을 읽으므로 Lot 별 잔량에 **진짜 날짜**가 붙는다
#:   (`ObservedLot.remaining_qty_observed_as_of`). 이 상수는 **회신 경로만**의 한계다.
SNAPSHOT_QUANTITY_OBSERVED_AS_OF: date | None = None


def snapshot_observed_as_of(snapshot: InventoryLogisticsSnapshot | None) -> date | None:
    """물류 회신 하나의 관측 기준일 (`AgentReply.observed_at`).

    🔴 **지금은 언제나 `None` 이고, 그것이 정직한 값이다.** 회신 하나가 싣는 사실은
       세 축에 걸쳐 있고 **세 축 모두** 관측일을 못 댄다.

    ```text
    정책 축      용량 · 리드타임 · 임계 비율 · 보관한계   유효일 칸이 없다
    예약·할당 축  inventory_by_item 에서 차감한 몫        빠진 날을 못 댄다
    물리 잔량 축  lots[].available_qty_kg                스냅샷에 원장이 없다
    ```

    🔴 **`received_at` 을 이 셈에 넣지 않는다.** 넣어 두면 정책 표에 유효일 칸이
       생기는 날 이 함수가 **입고일을 회신의 관측일로 내기 시작한다** — 그런데 회신의
       주된 사실은 가변 잔량이라, 그 날짜는 «가장 늦은 것» 이 아니라 **가장 이른 것**에
       가깝다. 지금 값이 같다고 틀린 셈을 남겨 두지 않는다.

    ★ **그래도 계산해서 낸다.** 기본값을 그대로 두는 것과 «재 봤더니 못 잰다» 는 다른
      사실이고, 세 축이 차례로 날짜를 얻는 날 이 함수가 **저절로** 따라온다.
    """
    if snapshot is None:
        return None
    return derive_observed_as_of(
        [
            POLICY_OBSERVED_AS_OF,
            COMMITMENT_OBSERVED_AS_OF,
            SNAPSHOT_QUANTITY_OBSERVED_AS_OF,
        ]
    )


# ---------------------------------------------------------------------------
# 근거
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExceptionEvidence:
    """Exception 한 줄을 세운 사실 하나. **근거 없는 Exception 은 만들지 않는다.**

    ★ **봉투의 `contracts.core.Evidence` 를 쓰지 않는다.** 저쪽은 마스터에게 내보내는
      회신의 근거 모양이고 `value: float` 이라 Decimal 이 조용히 흔들리며, 무엇보다
      **관측일 칸이 없다.** 이쪽은 DB 에 남아 며칠 뒤에도 읽히는 행이라 그 셋이 다르다.

    ⚠️ `value` 는 `Decimal` 이다 — 저장할 때 **문자열로** 적는다. JSON 수치로 적으면
       읽을 때 float 을 지나 `0.30` 이 `0.30000000000000004` 로 돌아온다.
    """

    fact: str
    value: Decimal
    unit: str
    #: 어느 표에서 왔나. 계산 결과는 `tool_calc:{함수}` 로 적는다 (봉투 어휘와 같은 결).
    source: str
    source_id: str
    #: 🔴 그 원천이 **알 수 있었던 날**. 유효일이 없는 표는 `None` 이다.
    observed_as_of: date | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "fact": self.fact,
            "value": format(self.value, "f"),
            "unit": self.unit,
            "source": self.source,
            "source_id": self.source_id,
            "observed_as_of": (
                None if self.observed_as_of is None else self.observed_as_of.isoformat()
            ),
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> ExceptionEvidence:
        observed_raw = raw.get("observed_as_of")
        return cls(
            fact=str(raw["fact"]),
            value=Decimal(str(raw["value"])),
            unit=str(raw["unit"]),
            source=str(raw["source"]),
            source_id=str(raw["source_id"]),
            observed_as_of=None if observed_raw is None else date.fromisoformat(str(observed_raw)),
        )


# ---------------------------------------------------------------------------
# 관측
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ObservedLot:
    """관측 시점의 Lot 하나. **여기서 아무것도 판정하지 않는다.**

    ★ 값의 주인은 전부 기존 코드다 — 잔량·상태·신선도는 스냅샷
      (`readmodel/current.get_current_logistics_read`)이 낸 그대로이고, `uncommitted_kg` 는
      `tools._sellable_lot_contributions` 가 품목 합계를 셀 때 쓰는 **그 식**이다.
    """

    lot_id: str
    #: 스냅샷 축은 품목 **이름**이다(`InventoryLotSnapshot.item`).
    item: str
    #: 🔴 원장 축의 품목 ID. 스냅샷에 없어서 `turnover.load_lot_turnover` 에서 온다.
    #: 그 조회가 Lot 을 못 찾으면 `None` — 지어내지 않는다.
    item_id: str | None
    status: str
    received_at: date | None
    #: 물리 잔량. 🔴 **상태와 무관하다** — HOLD·만료 재고도 자리를 차지한다.
    remaining_qty_kg: Decimal
    #: 아직 아무도 안 잡은 몫 = 잔량 − 살아 있는 할당(ALLOCATED · PICKED).
    #: 🔴 예약·할당 축을 **못 읽었으면 `None`** 이다 — 0 으로 놓으면 이미 팔린
    #: 재고를 «아무도 안 잡았다» 로 읽는다.
    uncommitted_kg: Decimal | None
    remaining_freshness_days: int | None
    effective_freshness_limit_days: int | None
    #: 회전 정책의 판매우선 경계. 정책이 없는 품목이면 `None` (실측 3/5 품목만 있다).
    sell_priority_remaining_days: int | None
    storage_zone: str | None
    #: 🔴 `remaining_qty_kg` 의 관측일 = **그 Lot 의 마지막 원장 이동일**
    #: (`readmodel/historical.ledger_state_by_lot`). 원장에 이동이 없으면 `None` 이다.
    #:
    #: ⚠️ **`received_at` 이 아니다.** 잔량은 입고 뒤에도 출고·폐기로 계속 바뀌고
    #:    (`ledger._update_remaining` 이 유일한 writer 이며 늘 같은 판에 `moved_at` 을
    #:    남긴다), 입고일을 적으면 D8 까지 나간 재고를 D1 부터 알던 것으로 적게 된다.
    remaining_qty_observed_as_of: date | None
    #: 🔴 `status` 의 관측일. **어휘마다 근거가 다르다.**
    #:
    #: ```text
    #: ACTIVE    received_at        Lot INSERT 가 적은 값이고 되돌리는 writer 가 없다
    #: DISPOSED  마지막 이동일       잔량을 0 으로 만든 DISPOSE 의 날
    #: 그 밖      None               DEPLETED · HOLD 는 production writer 가 없다
    #: ```
    status_observed_as_of: date | None

    @property
    def uncommitted_observed_as_of(self) -> date | None:
        """`uncommitted_kg` 의 관측일 = **잔량 축과 예약·할당 축 중 늦은 쪽.**

        🔴 **지금은 언제나 `None` 이다** — 예약·할당 축을 못 댄다
           (`COMMITMENT_OBSERVED_AS_OF`). 그 축이 날짜를 얻는 날 이 값이 저절로 선다.
        """
        return derive_observed_as_of(
            [self.remaining_qty_observed_as_of, COMMITMENT_OBSERVED_AS_OF]
        )

    @property
    def freshness_observed_as_of(self) -> date | None:
        """`remaining_freshness_days` 의 관측일 = **입고일과 보관 정책 중 늦은 쪽.**

        🔴 **`received_at` 하나로 적지 않는다.** 잔여 신선도는 «한계 − 경과» 이고 그
           한계가 `item_storage_policies` 에서 온다 — 정책이 언제부터 그 값이었는지를
           모르면 잔여 일수도 언제부터 그 값이었는지 모른다. 그래서 지금은 `None` 이다.
        """
        return derive_observed_as_of([self.received_at, POLICY_OBSERVED_AS_OF])

    @property
    def freshness_remaining_ratio(self) -> Decimal | None:
        """잔여 ÷ 유효 한계. 🔴 **분모는 원값이 아니라 유효 한계다** (중 등급 왜곡 방지).

        ★ `tools.collect_freshness_lot_census` 가 스냅샷에서 재는 비율과 **같은 식**이다.
        """
        if self.remaining_freshness_days is None or self.effective_freshness_limit_days is None:
            return None
        if self.effective_freshness_limit_days <= 0:
            return None
        return Decimal(self.remaining_freshness_days) / Decimal(self.effective_freshness_limit_days)


@dataclass(frozen=True)
class ObservedCapacity:
    """관측 시점의 창고 용량 **측정값**. 임계는 여기 없다 (`ObservedPolicy`)."""

    used_kg: Decimal
    guaranteed_kg: Decimal | None
    burst_kg: Decimal | None
    #: `tools.calculate_window_capacity_usage` 결과. 입력이 모자라면 `None` 이고,
    #: 그때 용량 판정은 **건너뛴다** — 0 으로 놓으면 «확인했고 여유 있음» 이 된다.
    window_usage_ratio: Decimal | None


@dataclass(frozen=True)
class ObservedPolicy:
    """판정에 쓰는 임계 둘. **측정값과 한 칸에 담지 않는다.**

    🔴 **`None` 은 «0» 이 아니라 «기준이 없다» 이고, 그때 그 탐지기는 돌지 않는다.**
       기준 없이 낸 0 건은 *"확인했고 문제 없음"* 으로 읽혀 정책 미등재를 안전 신호로
       둔갑시킨다 (`rules.evaluate_*_business_signals` 와 같은 태도).

    ⚠️ **관측일이 없다.** 정책 표에 유효일 칸이 없어서다 — `POLICY_OBSERVED_AS_OF`.
    """

    freshness_pressure_ratio: Decimal | None
    capacity_tight_ratio: Decimal | None


@dataclass(frozen=True)
class WarehouseObservation:
    """한 번의 관측. **읽기만 한 결과이고 아무 판정도 안 들었다.**

    ⚠️ **`reservations` 를 따로 싣지 않는다.** 예약·할당 축은 이미 Lot 별
       `uncommitted_kg` 로 접혀 들어왔고(`tools._sellable_lot_contributions` 와 같은 식),
       원본 목록을 한 벌 더 들면 **같은 사실의 주인이 둘**이 된다. 조사 단계가 예약
       원본을 볼 자리는 공용 Read-only Tool(`get_sales_commitments` · Commit 3)이다.
    """

    sim_run_id: str
    as_of: date
    #: **물리 재고 축**의 관측일 = 관측에 든 Lot 들의 `remaining_qty_observed_as_of`
    #: 중 가장 늦은 것, **하나라도 못 대면 `None`**.
    #:
    #: ★ `used_capacity_kg` 가 바로 이 Lot 들의 잔량 합이라(`repository` 가 그렇게
    #:   셈한다) 그 사실의 관측일이 곧 이 값이다.
    #:
    #: 🔴 **«창고 상태 전체» 의 관측일이 아니다.** 그것은 정책 축과 예약·할당 축까지
    #:    합친 값이라 지금은 언제나 `None` 이다 (`POLICY_OBSERVED_AS_OF` ·
    #:    `COMMITMENT_OBSERVED_AS_OF`). 이름을 «전체» 로 두면 재고 축 하나를 재 놓고
    #:    창고를 다 잰 것처럼 읽힌다.
    inventory_observed_as_of: date | None
    lots: tuple[ObservedLot, ...]
    capacity: ObservedCapacity
    policy: ObservedPolicy
    #: 지금 살아 있는(OPEN · PROPOSED) Exception 들.
    open_exceptions: tuple[ExceptionRow, ...]
    #: 🔴 **못 본 것 · 안 맞은 것을 삼키지 않는다.** 탐지를 막지는 않는다.
    uncertainties: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# 판정 · 행
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DetectedCondition:
    """탐지기 하나가 낸 *"지금 참인 조건"* 한 줄. **행이 아니다.**

    행으로 만들지(INSERT) 갱신할지(UPDATE)는 `detect.py` 가 dedupe 축으로 정한다 —
    탐지기는 순수 함수라 DB 도 이전 상태도 모른다.
    """

    code: ExceptionCode
    subject_type: SubjectType
    subject_id: str
    severity: Severity
    detector_version: str
    evidence: tuple[ExceptionEvidence, ...]
    observed_as_of: date | None
    note: str = ""

    @property
    def dedupe_key(self) -> tuple[str, str, str]:
        """`(code, subject_type, subject_id)`. 실행 축(`sim_run_id`)은 부르는 쪽이 안다."""
        return (self.code, self.subject_type, self.subject_id)


@dataclass(frozen=True)
class DetectionRecord:
    """감지 한 번의 «그날 severity» (LOG-AGENT-005). `detection_history_json` 한 원소.

    🔴 **날짜별 이력이지 상태머신이 아니다.** Historical 은 `as_of <= 기준일` 중
       `max(as_of)` 원소의 severity 로 그날 우선도를 복원한다 — 배열 순서를 믿지 않는다.
    """

    as_of: date
    severity: str

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> DetectionRecord:
        return cls(as_of=date.fromisoformat(raw["as_of"]), severity=raw["severity"])


@dataclass(frozen=True)
class ExceptionRow:
    """`logistics_exceptions` 한 행. **칸 이름이 DB 와 같다.**"""

    exception_id: str
    sim_run_id: str
    code: str
    subject_type: str
    subject_id: str
    severity: str
    status: str
    opened_as_of: date
    last_detected_as_of: date
    observed_as_of: date | None
    evidence: tuple[ExceptionEvidence, ...]
    detector_version: str
    resolved_as_of: date | None = None
    resolved_by: str | None = None
    risk_accepted_as_of: date | None = None
    previous_exception_id: str | None = None
    note: str | None = None
    #: 감지별 «그날 severity» 이력 (LOG-AGENT-005). 옛 행·기록 없음이면 비어 있다.
    detection_history: tuple[DetectionRecord, ...] = ()

    @property
    def dedupe_key(self) -> tuple[str, str, str]:
        return (self.code, self.subject_type, self.subject_id)


@dataclass(frozen=True)
class DetectOut:
    """탐지 한 번의 결과. 🔴 **예외를 값으로 옮긴 것이 아니다** — 터지면 올린다.

    ★ 트랜잭션 주인(`master/inspection.py`)이 `FAILED` 를 든다. `service/maintenance` ↔
      `master/maintenance.py` 와 **같은 나눔**이다: 여기는 업무 판단만, 저기는
      커넥션·커밋·예외 어휘.

    ```text
    RAN          무엇인가 열렸거나 갱신됐거나 닫혔다
    NOTHING_DUE  🟢 확인했고 손댈 것이 없었다 — 정상이다
    ```
    """

    as_of: date
    phase: DetectPhase
    status: Literal["RAN", "NOTHING_DUE"]
    opened: tuple[str, ...] = ()
    updated: tuple[str, ...] = ()
    resolved: tuple[str, ...] = ()
    reason: str = ""
    uncertainties: tuple[str, ...] = field(default_factory=tuple)

    @property
    def counts(self) -> dict[str, int]:
        """요약이 그대로 싣는 세 수. **여기서 다시 세지 않게 한 벌로 낸다.**"""
        return {
            "opened": len(self.opened),
            "updated": len(self.updated),
            "resolved": len(self.resolved),
        }


class EmptyEvidence(ValueError):
    """근거 0 건으로 Exception 을 만들려 했다. 🔴 **DB CHECK 보다 먼저 막는다.**

    ★ 제약이 이미 막지만 여기서 한 번 더 막는 이유는 **어느 탐지기가** 근거를 안
      냈는지를 사유에 적기 위해서다 — DB 오류 문자열에는 그것이 없다.
    """


#: 닫힌 행인데 **닫은 날이 없다.** 그날 살아 있었는지 증명할 수 없어 과거 조회에서
#: 뺀다 — 넣으면 이미 끝난 문제를 조사하라고 올리는 것이 된다.
#: 🔴 지금 production 에서는 안 난다 (`resolve_exception` 이 `resolved_as_of` 를 함께
#:    적는다). 사람이 손으로 닫은 행을 대비해 규칙만 세워 둔다.
EXCEPTION_CLOSE_DATE_UNRESOLVED = "EXCEPTION_CLOSE_DATE_UNRESOLVED"


@dataclass(frozen=True)
class LiveExceptionsAt:
    """그날 살아 있던 행 + **그 목록을 그렇게 만든 날들.**

    ★ `membership_dates` 가 따로 있는 이유: 목록은 열린 날로만 정해지지 않는다.
      *"D7 에 하나가 닫혀서 D8 목록이 이렇다"* 는 사실의 관측일은 **D7** 이다 —
      살아남은 행들의 `opened_as_of` 만 모으면 그 D7 이 통째로 사라진다.

    🔴 **`uncertainties` 가 비지 않으면 목록이 확정된 것이 아니다.** 닫힌 날을 못 댄
       행이 하나라도 있으면 그날 목록을 증명할 수 없다.
    """

    rows: tuple[ExceptionRow, ...]
    #: 그날까지 목록을 바꾼 모든 날 (열린 날 · 닫힌 날).
    membership_dates: tuple[date, ...]
    uncertainties: tuple[str, ...]


#: 예약·할당 축을 못 읽었다. 🔴 **0 건 확인과 다르다** — 못 읽은 축을 0 으로 놓으면
#: 이미 팔린 재고가 *"아무도 안 잡았다"* 로 보이고, 신선도 압박이 **없는 문제를
#: 만들어 낸다.** 그래서 이 사실이 있으면 Lot 별 미확정 물량이 전부 `None` 이다.
OUTBOUND_COMMITMENTS_UNRESOLVED = "OUTBOUND_COMMITMENTS_UNRESOLVED"
#: 창 사용률을 못 셈했다 (리드타임 · 보장 용량 · 입고 예정 미확정).
CAPACITY_WINDOW_USAGE_UNRESOLVED = "CAPACITY_WINDOW_USAGE_UNRESOLVED"
#: 캐시 잔량(`inventory_lots.remaining_qty_kg`)과 원장 누계가 다르다 (상세설계 §5.1).
#: 🔴 **예외를 내지 않는다** — 탐지를 세우는 대신 사실만 적는다.
OBSERVATION_INCONSISTENT = "OBSERVATION_INCONSISTENT"
#: 방향을 모르는 `ADJUST` 이동이 있어 원장 대조를 건너뛰었다. 🔴 그날은 **모든 Lot 의
#: 잔량 관측일이 `None`** 이다 — 대조도 날짜도 같은 원장에서 나온다.
LEDGER_ADJUST_UNSUPPORTED = "LEDGER_ADJUST_UNSUPPORTED"
#: 스냅샷에는 잔량이 있는데 원장에 그 Lot 의 이동이 하나도 없다.
#: 🔴 **잔량의 관측일을 못 댄다** — production 경로로 선 Lot 이면 날 수 없는 일이다
#: (`inbound_stock._insert_lot` 이 0 으로 세우고 `ledger` 만 잔량을 올린다).
LEDGER_MOVE_UNRESOLVED = "LEDGER_MOVE_UNRESOLVED"
#: 스냅샷에는 있는데 회전 조회에 없는 Lot — 품목 ID·판매우선 경계를 못 붙였다.
TURNOVER_LOT_UNRESOLVED = "TURNOVER_LOT_UNRESOLVED"
#: 스냅샷 기준일이 요청 기준일과 다르다.
SNAPSHOT_AS_OF_MISMATCH = "SNAPSHOT_AS_OF_MISMATCH"


class ObservationNotReady(RuntimeError):
    """관측을 세울 수 없다. 🔴 **여기서 삼키지 않는다** — 트랜잭션 주인이 값으로 옮긴다.

    ★ 부재(그날 fixture 가 없다)는 `repository` 가 이미 `LookupError` 로 내고, 이
      예외는 **받은 것이 요청과 다를 때**다. 둘 다 `master/inspection.py` 에서
      `FAILED` 한 줄이 된다 — 하루는 계속 간다.
    """
