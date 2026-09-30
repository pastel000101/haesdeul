"""
procurement_boundary.py — **그날 매입 판단이 받아 둔 경계를 읽어 온다** (2026-09-09).

판매 후보가 물건이 모자랄 때 매입에 *"얼마를 언제 얼마에 댈 수 있나"* 를 묻기로
계약이 섰다 (매입 확정 2026-09-09 · `batch` · `SUPPLY_CAPACITY_QUERY`). 그런데
매입이 알려 온 것이 하나 더 있다.

```text
procurable_quantity_kg = min( 창고 여유(물류) , 매입 가능액(재무) ÷ 단가(매입) )
```

🔴 **「가능량」이 매입 값이 아니다.** 재료가 물류·재무 봉투다. 그래서 마스터가 그
  둘을 실어 줘야 한다.

★★ **아무도 새로 부르지 않는다.** 값은 **이미 표에 있다** — 매입 판단이 `PRE_PURCHASE`
  로 받은 경계를 실행 이력에 통째로 적어 둔다.

```text
master_agent_runs · cycle='PROCUREMENT' · response_payload.constraints
    inventory.warehouse_free_kg · inventory.rental_cap_kg
    inventory.inbound_lead_days · finance.finance_cap_amount_krw
```

  물류·재무 호출이 **0회**라 판매 예산이 안 늘어난다.

---

🔴 **`procurable_quantity_kg` 를 계산하지 않는다.** 나눗셈에 쓰는 단가가 **매입
  것**이다. 우리가 계산하면 단가가 바뀌는 날 두 값이 갈리고, 그때 어느 쪽이 참인지
  아무도 말해 주지 않는다. **재료만 준다.**

🔴 **없는 값을 `0` 으로 채우지 않는다.** `None` 이 *"못 읽었다"* 이고 `0.0` 은
  *"자리가 없다"* 다 — 매입이 `null ≠ 0` 을 계약으로 청했다.

  ⚠️ `rental_cap_kg` 는 실측이 실제로 `0.0` 이다 (물류 확정값). **그건 읽은 값이라
    `0.0` 이 맞다.** 못 읽은 것과 반드시 구별되어야 한다.

---

🔴 **`CAPABILITY_ROUTING["ADDITIONAL_SUPPLY_CONTEXT"]` 은 이 판에서 안 채운다.**

  회신은 왔지만 **매입 어댑터가 아직 그 mode 를 모른다** — `purchase_port` 가
  `STATUS_QUERY` 말고는 전부 `_generate_scenarios` 로 떨어뜨린다. 지금 채우면 판매
  사이클이 매입을 불러 **조용히 매입안을 만든다.** 근거는 `envelope.py` 의 그 자리에
  적혀 있다. 이 모듈은 **경계를 읽어 오는 자리**와 **못 읽을 때의 사유 어휘**까지다.

★ **봉투에 싣는 배선(`sales_flow.py`)도 이 판이 아니다.** 라우팅이 열리는 날 같이
  한다 — 지금 실어도 아무도 안 읽는다.

★ 2026-09-30 재구성 BL-018: `master/procurement_boundary.py` 에서 옮겼다. 역할이 다른 부분은 갈랐다
  — `domain/procurement_boundary.py`; `schemas/procurement_boundary.py`. 무엇이 어디로 갔는지는
  설계서 대응표 `master/` 절.
"""

from __future__ import annotations

from datetime import date

from app.master.domain.execution_day import HolidayCalendar, is_execution_day
from app.master.domain.procurement_boundary import boundary_from, constraints_of, is_ledger_gap_row
from app.master.readmodel.runs import list_runs
from app.master.schemas.procurement_boundary import ProcurementBoundary

#: 경계를 읽어 오는 축. 매입 판단 행이 앉는 사이클이다 (`persistence._CYCLE`).
_CYCLE = "PROCUREMENT"

#: 하루치 행을 읽는 상한. 실측(2026-09-09)으로 한 축·하루 최대가 21행이다
#: (같은 날을 여러 번 다시 걸으면 품목 셋이 그만큼 쌓인다).
#:
#: ⚠️ `list_runs` 의 기본값 50 에 기대지 않는다. 그 기본값은 저쪽 사정이라 바뀔 수
#:   있고, 바뀌는 날 이 모듈은 **오류 없이 옛 행을 최신으로 읽는다.**
_DAY_ROW_LIMIT = 200


def read_procurement_boundary(
    *,
    as_of: date,
    sim_run_id: str,
    calendar: HolidayCalendar | None = None,
) -> ProcurementBoundary:
    """그날 그 축의 매입 경계를 읽어 온다. **못 읽으면 사유를 낸다.**

    판정 순서 — **이 순서가 계약이다.**

    ```text
    ① 그날 그 축에 constraints 를 든 PROCUREMENT 행이 있나  → 있으면 읽고 끝
    ② 관문 행이 있나                                        → LEDGER_GAP
    ③ is_execution_day 가 거짓인가                          → NOT_EXECUTION_DAY
    ④ 그 밖                                                 → NO_PROCUREMENT_RUN
    ```

    🔴 **`①` 이 맨 앞이다.** 행이 있으면 왜 없는지 물을 필요가 없다. 뒤로 밀면
      토요일에 손으로 돌린 판단이 *"실행일이 아니라 못 읽었다"* 로 접힌다.

    ⚠️ **`③` 은 `execution_day.is_execution_day` 를 부른다.** 요일 판정을 여기서
      다시 짓지 않는다 — 같은 사실의 주인이 둘이 되면 갈린 날 아무도 어느 쪽이
      맞는지 말해 주지 않는다.

    ★ **`calendar` 를 안 주면 주말만 가른다** (`is_execution_day` 의 태도 그대로).
      공휴일에 물으면 그날은 `NO_PROCUREMENT_RUN` 으로 나온다 — 달력을 준 호출만
      `NOT_EXECUTION_DAY` 를 받는다.

    ★ **그 경계는 「그날 매입 판단 시점」의 것이다.** 하루 순서가 … → 판단(매입) →
      출고 라, 판단 뒤 출고가 나가면 창고가 바뀐다. 낮에 판매가 물으면 **아침
      값**이고, 실제 매입은 다음 실행일이다. 그래서 `source_ref` 를 반드시 채운다.

    :raises ValueError: `sim_run_id` 가 비었을 때. **빈 축을 조용히 전체로 바꾸지
        않는다** — 그러면 남의 걷기 경계가 이 판매 후보의 답으로 실린다
        (`run_repository.check_walk_scope` 와 같은 태도).
    :raises CalendarNotCovered: `calendar` 를 줬는데 그 날을 안 덮을 때. **평일로
        단정하지 않는다** — 잡아서 넘기면 달력이 끊긴 것과 실행일인 것이 같아진다.
        `execution_day` 가 *"부르는 쪽이 정한다"* 로 남긴 자리이고, `①` `②` 가 앞에
        있으므로 이 예외는 **답이 정말 달력에 걸릴 때만** 난다.
    """
    if not sim_run_id.strip():
        raise ValueError(
            "sim_run_id 없이 경계를 읽을 수 없다 — 어느 실행의 경계인지 없으면"
            " 물음이 성립하지 않는다"
        )

    rows = list_runs(
        cycle=_CYCLE,
        as_of=as_of,
        sim_run_id=sim_run_id,
        limit=_DAY_ROW_LIMIT,
    )

    # ★ **최신부터 본다 — 여기서 직접 세운다.** `list_runs` 가 이미 `created_at DESC`
    #   로 주지만, 그 정렬은 저쪽 사정이라 바뀔 수 있다. 바뀌는 날 이 모듈은 오류
    #   없이 **옛 경계를 오늘 답으로** 내보낸다.
    최신부터 = sorted(rows, key=lambda row: row["created_at"], reverse=True)

    # ① 그날 그 축에 constraints 를 든 행이 있나.
    #
    # ⚠️ **품목별로 값이 다를 수 있다** (실측 2026-09-09 · 축이 실린 15개 날 묶음 중
    #   5개에서 `warehouse_free_kg` 가 갈렸다). 갈리는 자리는 품목이 아니라 **같은 날을
    #   다시 걸은 회차**다 — 한 회차 안의 배추·무·양파는 실측 전부가 같은 값이다.
    #   그러니 최신 회차가 오늘의 사실이고, **어느 실행에서 왔는지는 `source_ref` 가
    #   숨김없이 말한다.**
    for row in 최신부터:
        constraints = constraints_of(row)
        if constraints:
            return boundary_from(row, constraints)

    # ② 관문 행이 있나 — **모양이 아니라 업무 키로 잡는다.**
    #
    # 🔴 `01-24` · `01-31` 의 `E4_NOT_STARTED` 여섯 행은 품목이 실린 **매입 행**이고
    #   (실측), 품목이 없는 채로 죽은 옛 매입 행도 14행 있다. 모양으로 잡으면 그것들이
    #   관문으로 읽혀 없는 *"장부가 막았다"* 가 생긴다. 관문 행에는 그 행만의 업무 키가
    #   있고 (`run_repository.ledger_gap_request_id`), 적는 쪽이 그 키를 적는다.
    if any(is_ledger_gap_row(row) for row in 최신부터):
        return ProcurementBoundary(present=False, absent_reason="LEDGER_GAP")

    # ③ 실행일이 아닌가. **요일 판정을 다시 짓지 않는다.**
    if not is_execution_day(as_of, calendar=calendar):
        return ProcurementBoundary(present=False, absent_reason="NOT_EXECUTION_DAY")

    # ④ 실행일이고 관문도 안 막았는데 행이 없다. **운영 공백이다.**
    return ProcurementBoundary(present=False, absent_reason="NO_PROCUREMENT_RUN")
