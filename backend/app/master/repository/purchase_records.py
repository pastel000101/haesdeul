"""`master_purchase_records` 적재·조회 (설계 260915 안 A §4-1).

★ **사람이 적은 실매입 원문**이다. 원장(`purchases` · `payables` · `inbound_schedules`)
  에 무엇이 앉는지는 여기서 모른다 — 그것은 `transition.apply_approval` 이 이 값으로
  덮은 약정을 받아 정한다.

★ UPDATE · DELETE 가 없다. **한 승인에 한 번**이고(PK), 다시 적으면 PK 가 막는다.

🔴 **PK 에 실행 축이 있다** — `(sim_run_id, request_id, decision_seq, leg_seq)`
   (9/15 재무 회신 ③). 그래서 조회도 축으로 좁힌다. 다른 실행의 같은 업무 키는
   별개 기록이다.

🔴 **적재는 부르는 쪽 커넥션으로 하고 커밋하지 않는다.** 커밋 시점은 부르는 쪽이
   정한다 — `purchase_record.record_purchase` 는 회차 행을 다 적고 **그 자리에서**
   커밋한다 (전이보다 앞). 전이는 그 뒤 별도 트랜잭션이라 여기까지 되감지 못한다.

★ 2026-09-30 재구성 BL-018: `master/purchase_record_repository.py` 에서 옮겼다. 조회 넷은 종전에
  `master/db.py` 의 `fetch_all` 이 호출마다 조회 연결을 스스로 빌렸다 — 이제 받은 연결로 실행만
  하고(`select_*`), 조회 연결은 `readmodel/purchase_record.py` 가 호출마다 하나씩 빌린다(횟수 같음).
  적재(`insert_purchase_record_legs`)는 종전대로 부르는 쪽 연결을 받는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from psycopg import sql

from app.core.settings import get_db_schema
from app.master.domain.commitment import RecordedLeg

_TABLE = "master_purchase_records"


def _table(schema: str | None = None) -> sql.Composable:
    """기록 표. `schema` 를 안 주면 여기서 읽는다(연결을 받아 쓰는 적재 경로 — 종전과 같다)."""
    name = get_db_schema() if schema is None else schema
    return sql.SQL("{}.{}").format(sql.Identifier(name), sql.Identifier(_TABLE))


def insert_purchase_record_legs(
    conn: Any,
    *,
    sim_run_id: str,
    request_id: str,
    decision_seq: int,
    grade: str,
    recorded_by: str,
    legs: Sequence[RecordedLeg],
) -> None:
    """회차마다 한 행을 적는다. 🔴 **커밋하지 않는다.**

    ★ 이미 적힌 승인이면 PK `(sim_run_id, request_id, decision_seq, leg_seq)` 가
      `UniqueViolation` 으로 막는다 — 조용히 덮어쓰지 않는다.
    """
    query = sql.SQL(
        """
        INSERT INTO {} (
            sim_run_id, request_id, decision_seq, leg_seq,
            quantity_kg, amount_krw, purchase_date, arrival_date,
            grade, recorded_by
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
    ).format(_table())
    with conn.cursor() as cursor:
        for leg in legs:
            cursor.execute(
                query,
                (
                    sim_run_id,
                    request_id,
                    decision_seq,
                    leg.seq,
                    leg.qty_kg,
                    leg.amount_krw,
                    leg.purchase_date,
                    leg.arrival_date,
                    grade,
                    recorded_by,
                ),
            )


def select_purchase_record_legs(
    conn: Any, *, sim_run_id: str, request_id: str, decision_seq: int, schema: str
) -> list[dict[str, Any]]:
    """그 실행 축 · 그 승인에 적힌 기록 행 전부. 회차 순. 없으면 **빈 목록**."""
    query = sql.SQL(
        """
        SELECT sim_run_id, request_id, decision_seq, leg_seq,
               quantity_kg, amount_krw, purchase_date, arrival_date,
               grade, recorded_by, recorded_at
        FROM {}
        WHERE sim_run_id = %s AND request_id = %s AND decision_seq = %s
        ORDER BY leg_seq
        """
    ).format(_table(schema))
    with conn.cursor() as cursor:
        cursor.execute(query, (sim_run_id, request_id, decision_seq))
        return [dict(row) for row in cursor.fetchall()]


def select_recorded_decision_keys(
    conn: Any, *, sim_run_id: str, schema: str
) -> list[tuple[str, int]]:
    """이 실행 축에서 **기록이 있는 승인** `(request_id, decision_seq)` 전부."""
    query = sql.SQL(
        "SELECT DISTINCT request_id, decision_seq FROM {} WHERE sim_run_id = %s"
    ).format(_table(schema))
    with conn.cursor() as cursor:
        cursor.execute(query, (sim_run_id,))
        return [(row["request_id"], int(row["decision_seq"])) for row in cursor.fetchall()]


def select_recorded_sums_by_plan(
    conn: Any, *, sim_run_id: str, as_of: date, schema: str
) -> list[dict[str, Any]]:
    """그 실행 축 · 그날 승인에 적힌 실매입의 **합**을 `(품목, 안 이름)` 마다 한 줄로.

    한 줄 = `{"item", "scenario_label", "quantity_kg", "amount_krw"}` (조회 결과 그대로).
    화면이 쓰는 합계 모양(`RecordedTotals` · 단가)으로 엮는 일은
    `master/readmodel/purchase_record.recorded_totals_by_plan` 이 한다.

    🟢 2026-09-29 (재구성 BL-012) 전에는 이 자리의 `recorded_totals_by_plan` 이 SQL 과
       합계 조립을 함께 했고, 화면 둘(대시보드 · 매입 탭)이 이 repository 를 직접 불렀다.
       SQL 은 여기 그대로 두고 결과 조립만 readmodel 로 옮겼다 — 질의 문면은 같다.

    ★ **왜 `(품목, 안 이름)` 인가.** 화면의 매입안은 그 둘로 이름을 짓는다
      (`api/purchase/presenter._plan` 의 `key=f"{item} · {label}"`). 기록 표에는 품목도 안
      이름도 없어서 — 있는 것은 업무 키와 회차뿐이다 — 결정과 실행을 지나 그 둘까지
      되짚는다.

    .. code-block:: text

        master_purchase_records   sim_run_id · request_id · decision_seq · 수량 · 금액
          └ master_decisions      (request_id, decision_seq) 로 **하나**  → scenario_label
              └ master_agent_runs (request_id, sim_run_id, as_of)        → item

    🔴 **실행 축과 기준일을 둘 다 건다.** 축을 빼면 다른 걷기에서 산 값이 이 걷기의 안에
       붙고, 기준일을 빼면 어제 산 값이 오늘 안에 붙는다. 둘 다 틀린 줄도 모르는 오류다.

    ⚠️ 적힌 기록이 없으면 **빈 목록**이다. 0 으로 채우지 않는다 — «안 샀다» 와 «못 읽었다»
      는 부르는 쪽이 가린다.
    """
    ident = sql.Identifier(schema)
    query = sql.SQL(
        """
        SELECT r.item AS item,
               d.scenario_label AS scenario_label,
               sum(p.quantity_kg) AS quantity_kg,
               sum(p.amount_krw)  AS amount_krw
          FROM {} p
          JOIN {}.{} d
            ON d.request_id = p.request_id AND d.decision_seq = p.decision_seq
          JOIN (SELECT DISTINCT request_id, sim_run_id, as_of, item
                  FROM {}.{} WHERE cycle = 'PROCUREMENT') r
            ON r.request_id = p.request_id AND r.sim_run_id = p.sim_run_id
         WHERE p.sim_run_id = %(sim_run_id)s
           AND r.as_of = %(as_of)s
           AND d.scenario_label IS NOT NULL
         GROUP BY r.item, d.scenario_label
        """
    ).format(
        _table(schema),
        ident,
        sql.Identifier("master_decisions"),
        ident,
        sql.Identifier("master_agent_runs"),
    )
    with conn.cursor() as cursor:
        cursor.execute(query, {"sim_run_id": sim_run_id, "as_of": as_of})
        return cursor.fetchall()


def select_last_closed_date(conn: Any, *, sim_run_id: str, schema: str) -> date | None:
    """그 실행 축의 **마지막 재무 일마감일**. 마감이 없으면 `None`.

    ★ **읽기만 한다.** `daily_closings` 의 주인은 재무다 (`finance/closing.py` 가 적는다).
      실매입 매입일이 이미 마감된 날로 들어가지 못하게 막는 데만 쓴다
      (설계 260915 안 A §4-6 ②).
    """
    query = sql.SQL(
        "SELECT max(close_date) AS last_close FROM {}.{} WHERE sim_run_id = %s AND closed"
    ).format(sql.Identifier(schema), sql.Identifier("daily_closings"))
    with conn.cursor() as cursor:
        cursor.execute(query, (sim_run_id,))
        rows = cursor.fetchall()
    return rows[0]["last_close"] if rows else None
