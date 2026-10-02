"""마스터 원장 SQL — 승인 매입의 원장 쓰기 · 취소와 일별 마감 · 실행 조회.

받은 연결로 실행하고 commit 하지 않는다.

```text
persist_purchases(...)    승인 매입을 purchases · purchase_items 에 적는다
cancel_purchases(...)     승인 매입 header 를 CANCELLED 로 적는다
select_walk_closings(...) 걷기 구간의 현금 칸 — 걷기 요약의 「현금」·「현금항등식」 두 줄
select_sim_run(...) ·     번인 구간 — 에이전트가 판단하기 전에 회사가 어떻게 왔는가
select_closings(...)
```

조회 연결 대여와 조립은 `readmodel/ledger.py`(`get_burn_in` · `read_walk_closings`), 매입 행
계산은 `domain/ledger.py` 에 있다. 마감 조회는 읽기만 한다 — 마감을 만들지도, 금액을 세지도
않는다.

---

번인 구간: 에이전트가 판단하기 전에 회사가 어떻게 왔는가.

`sim_runs` 에 `SIM-BURNIN-202512` 가 `status=SEEDED` 로 심겨 있다.

```text
run_type     BURN_IN
기간         2025-12-02 ~ 12-31 (30일)
as_of        2025-12-31                    ← 에이전트가 처음 판단하는 날
note         "Agent 실행 전 30일 Persona 이력"
```

마감 행은 재무가 적는다(`finance/service/closing.py`). 여기는 읽기만 한다.

왜 이 화면이 필요한가. 에이전트가 12-31 에 "살 안이 없다" 고 답하는데, 그 앞의 30일을
안 보면 시스템이 고장 난 것처럼 읽힌다. 무차입 현금이 5,820만원에서 -1,328만원까지
떨어지고 미수금이 7,305만원 잠긴 회사에게 "지금 사지 마라" 는 정상 판단이다. 결론만
보여주면 그 사실이 사라진다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Any

from psycopg import sql

from app.core.settings import get_db_schema
from app.master.domain.ledger import PurchaseLedgerNotWritable, PurchaseWrite
from app.master.domain.purchase_ids import purchase_item_id_for

#: 매입 유형. `purchase_type` 에는 CHECK 가 없고 번인이 `SAFETY_STOCK_INIT` ·
#: `BURNIN_REPLENISHMENT` 를 쓴다. 승인이 만든 매입은 그 둘 중 어느 것도 아니다.
#:
#: 새 축을 만들지 않고 이미 쓰는 어휘에 맞춘다. 물류 inbound `source_ref` 가
#:   `MASTER-APPROVAL:{approval_id}` 를 쓴다 — 같은 사실을 두 이름으로 부르지
#:   않으려고 그쪽 어휘를 따른다.
MASTER_PURCHASE_TYPE = "MASTER_APPROVAL"

#: `settlement_status` CHECK 는 `SETTLED · OPEN · CANCELLED` 셋이다. 승인 시점의
#: 매입은 아직 정산되지 않았다.
_OPEN = "OPEN"


def persist_purchases(conn: Any, rows: Sequence[PurchaseWrite]) -> dict[str, int]:
    """계산된 매입 원장 행을 부르는 쪽 커넥션으로 기록한다.

    commit 하지 않는다. 커밋은 재무·물류 write 와 함께 마스터가 한 번 한다. 여기서
    커밋하면 매입만 먼저 확정되고, 뒤이어 재무가 터졌을 때 채무 없는 매입이 남는다.

    멱등: 같은 승인을 다시 반영해도 행이 늘지 않는다 — `purchase_id` 와
    `purchase_item_id` 가 둘 다 PK 이고 `ON CONFLICT DO NOTHING` 이 받는다.
    `purchase_id_for` 가 결정론이라 두 번째 반영도 같은 키가 나온다.

    :raises PurchaseLedgerNotWritable: 품목명을 `items` 에서 못 찾을 때.
    """
    schema = sql.Identifier(get_db_schema())
    written = {"purchases": 0, "purchase_items": 0}
    with conn.cursor() as cursor:
        for row in rows:
            item_id = _item_id_of(cursor, schema, row.item_name)
            cursor.execute(
                sql.SQL(
                    """
                    INSERT INTO {}.purchases (
                        purchase_id, sim_run_id, supplier_partner_id,
                        purchase_date, payment_due_date, purchase_type,
                        source_market_label, total_amount_krw, settlement_status,
                        proposal_id, scenario_id, source_event_id, evidence_id, note
                    )
                    VALUES (
                        %s, %s, NULL,
                        %s, %s, %s,
                        NULL, %s, %s,
                        %s, %s, NULL, NULL, NULL
                    )
                    ON CONFLICT (purchase_id) DO NOTHING
                    """
                ).format(schema),
                [
                    row.purchase_id,
                    row.sim_run_id,
                    row.purchase_date,
                    row.payment_due_date,
                    MASTER_PURCHASE_TYPE,
                    row.total_amount_krw,
                    _OPEN,
                    row.proposal_id,
                    row.scenario_id,
                ],
            )
            written["purchases"] += cursor.rowcount

            cursor.execute(
                sql.SQL(
                    """
                    INSERT INTO {}.purchase_items (
                        purchase_item_id, purchase_id, item_id, grade, market_name,
                        quantity_kg, unit_price_krw_per_kg, line_amount_krw, source_quote_id
                    )
                    VALUES (%s, %s, %s, %s, NULL, %s, %s, %s, NULL)
                    ON CONFLICT (purchase_item_id) DO NOTHING
                    """
                ).format(schema),
                [
                    # 접미사는 `item_id` 에서 `ITEM-` 을 뗀 나머지다 — 번인의
                    # `PITEM-SAFETY-001-BAECHU` 가 그 모양이다.
                    purchase_item_id_for(row.purchase_id, _item_code_of(item_id)),
                    row.purchase_id,
                    item_id,
                    # 약정이 실어 온 등급이다 (#69 · 약정의 `sourcing_plan`).
                    #
                    # 안 오면 `None` 이다. 빈 문자열로 채우지 않는다 — `''` 는
                    # "등급이 비어 있다" 라는 없는 사실을 만든다. `market_name` 과
                    # `source_quote_id` 는 승인이 모르는 사실이라 `NULL` 이다.
                    row.grade,
                    row.quantity_kg,
                    row.unit_price_krw_per_kg,
                    row.line_amount_krw,
                ],
            )
            written["purchase_items"] += cursor.rowcount
    return written


def cancel_purchases(conn: Any, purchase_ids: Iterable[str]) -> int:
    """이 승인이 만든 매입 header 를 `CANCELLED` 로 적는다.

    DELETE 하지 않는다. 승인이 있었다는 사실이 사라지면 "승인했고 취소했다" 를 아무도
    말할 수 없다 — `master_decisions` 가 append-only 인 것과 같은 규율이다.
    `purchases.settlement_status` 에 `CANCELLED` 칸이 있다 (실 DB CHECK 실측).

    commit 하지 않는다. 커밋은 재무·물류 취소와 함께 마스터가 한 번 한다. 여기서
    커밋하면 매입만 먼저 물리고, 뒤이어 재무가 터졌을 때 채무는 살아 있는데 매입은
    취소된 장부가 남는다.

    멱등: 이미 `CANCELLED` 인 행은 안 센다. `WHERE settlement_status <> 'CANCELLED'` 가
    재시도를 멱등으로 만든다 — 두 번째 취소는 0 을 돌려준다. 재무 `#302` 의 "retry
    no-op" 과 같은 모양이고, 그래야 마스터가 "이번에 실제로 물린 것" 을 말할 수 있다.

    주의: `SETTLED` 도 물린다. 지급 여부를 여기서 판정하지 않는다 — 그 판정은 `payables`
    를 든 재무 몫이고(`OPEN + paid=0` 만 취소), 재무가 거절하면 이 write 도 같은
    트랜잭션에서 롤백된다. 두 곳이 각자 판정하면 어느 날 갈린다.

    :returns: 이번 호출로 `CANCELLED` 가 된 header 수.
    """
    ids = [pid for pid in purchase_ids if pid]
    if not ids:
        # 회차 일정이 없던 약정도 승인은 살아 있다 — 물릴 원장이 없다는 것은 정상
        # 상태이지 예외가 아니다 (`build_purchase_rows` 와 같은 태도).
        return 0
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                UPDATE {}.purchases
                   SET settlement_status = 'CANCELLED'
                 WHERE purchase_id = ANY(%s)
                   AND settlement_status <> 'CANCELLED'
                """
            ).format(schema),
            (ids,),
        )
        return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0


def _item_id_of(cursor: Any, schema: sql.Identifier, item_name: str) -> str:
    """계약 품목명(`배추`)을 `items.item_id`(`ITEM-BAECHU`)로.

    하드코딩 맵을 만들지 않는다. 그 맵이 또 하나의 어휘가 되어 `items` 표와 갈린다.
    주인은 `items` 표이고 마스터는 읽기만 한다.

    실패 처리: 못 찾으면 조용히 넘기지 않는다. 품목 이름과 id 를 섞어 찾으면 매칭
    0건인데 에러가 안 난다 (물류가 같은 자리에서 겪은 것: `ITEM-BAECHU` 를 `배추` 로
    찾음). 여기서 넘어가면 `item_id` 가 없는 채로 FK 에 걸리거나, 더 나쁘게 엉뚱한
    품목에 수량이 붙는다.
    """
    cursor.execute(
        sql.SQL("SELECT item_id FROM {}.items WHERE item_name = %s").format(schema),
        [item_name],
    )
    row = cursor.fetchone()
    if not row:
        raise PurchaseLedgerNotWritable(
            f"items 표에 품목명이 없다: {item_name!r} — item_id 를 지어내지 않는다."
        )
    item_id = row["item_id"] if isinstance(row, Mapping) else row[0]
    if not isinstance(item_id, str) or not item_id.strip():
        raise PurchaseLedgerNotWritable(f"items 표의 item_id 를 읽을 수 없다: {item_name!r}")
    return item_id


def _item_code_of(item_id: str) -> str:
    """`ITEM-BAECHU` → `BAECHU`. 접두사가 겹치면 `PITEM-…-ITEM-BAECHU` 가 된다."""
    prefix = "ITEM-"
    return item_id.removeprefix(prefix)


_CLOSING_COLUMNS = (
    "close_date",
    "day_no",
    "base_cash_balance_krw",
    "loan_cash_balance_krw",
    "receivables_balance_krw",
    "inventory_qty_kg",
    "sales_recognized_krw",
    "collection_cash_in_krw",
    "purchase_cash_out_krw",
    "closed",
)


#: 현금 축의 칸 이름. 주인이 여기 하나다.
#:
#: 걷기 요약(`report/walk_summary.py`)이 이 이름들을 손으로 다시 적지 않는다. 적으면
#:   표가 바뀌는 날 요약만 옛 이름을 말하고, 그때 나는 것은 오류가 아니라 조용한 0 이다.
#:   걷기 요약이 `LLM_STATUSES` 를 `envelope` 에서 들여오는 것과 같은 결이다.
CLOSE_DATE = "close_date"
PURCHASE_CASH_OUT = "purchase_cash_out_krw"
LOGISTICS_CASH_OUT = "logistics_cash_out_krw"
PAYROLL_INTEREST_CASH_OUT = "payroll_interest_cash_out_krw"
#: 이 칸을 안 읽으면 요약이 조용히 안 맞는다. 걷기 현금 줄은 유출 칸들과 순현금을
#:   나란히 찍는데, 일반 운영비만 빠지면 «찍힌 칸의 합 ≠ 순현금» 이 된다. 그때 나는
#:   것은 오류가 아니라 읽는 사람이 못 맞추는 표다.
#:
#: 마스터는 이 값을 나르기만 한다. 순현금은 재무가 이미 빼서 적어 놓은 값이고,
#:   여기서 다시 세지 않는다.
OPERATING_EXPENSE_CASH_OUT = "operating_expense_cash_out_krw"
COLLECTION_CASH_IN = "collection_cash_in_krw"
NET_CASH = "base_net_cash_krw"
BASE_CASH_BALANCE = "base_cash_balance_krw"

#: 대출을 포함한 곡선. 현금 항등식에는 안 들어간다 — 차입·상환이 섞여 축이 다르다.
#:   그래도 읽는다: 읽지 않으면 "안 섞었다" 를 아무도 잴 수 없다.
LOAN_CASH_BALANCE = "loan_cash_balance_krw"

#: 걷기가 현금 축을 볼 때 읽는 칸 전부. 여기 있는 것만 읽는다.
WALK_CASH_COLUMNS = (
    CLOSE_DATE,
    PURCHASE_CASH_OUT,
    LOGISTICS_CASH_OUT,
    PAYROLL_INTEREST_CASH_OUT,
    OPERATING_EXPENSE_CASH_OUT,
    COLLECTION_CASH_IN,
    NET_CASH,
    BASE_CASH_BALANCE,
    LOAN_CASH_BALANCE,
)


def _table(schema: str, name: str) -> sql.Composable:
    return sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(name))


def select_walk_closings(
    conn: Any, *, sim_run_id: str, start: date, end: date, schema: str
) -> list[dict[str, Any]]:
    """그 실행의 `start..end` 마감 행(걷기 현금 칸만). 범위 · 정렬은 SQL 이 건다."""
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                "SELECT {} FROM {} WHERE sim_run_id = %s AND close_date BETWEEN %s AND %s"
                " ORDER BY close_date"
            ).format(
                sql.SQL(", ").join(sql.Identifier(c) for c in WALK_CASH_COLUMNS),
                _table(schema, "daily_closings"),
            ),
            (sim_run_id, start, end),
        )
        return cursor.fetchall()


def select_sim_run(conn: Any, sim_run_id: str, *, schema: str) -> dict[str, Any] | None:
    """`sim_runs` 한 행. 없으면 `None`."""
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                "SELECT sim_run_id, run_type, period_start, period_end, as_of, status,"
                " financing_mode, config_json, note FROM {} WHERE sim_run_id = %s"
            ).format(_table(schema, "sim_runs")),
            (sim_run_id,),
        )
        return cursor.fetchone()


def select_closings(conn: Any, sim_run_id: str, *, schema: str) -> list[dict[str, Any]]:
    """그 실행의 일별 마감 행 전부. 날짜순."""
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL("SELECT {} FROM {} WHERE sim_run_id = %s ORDER BY close_date").format(
                sql.SQL(", ").join(sql.Identifier(c) for c in _CLOSING_COLUMNS),
                _table(schema, "daily_closings"),
            ),
            (sim_run_id,),
        )
        return cursor.fetchall()
