"""미적용 전이를 찾는 조회 둘의 SQL. 쓰기가 없다.

"승인은 났는데 매입 원장에 안 닿은 것" 을 찾으려면 두 사실을 맞대야 한다.

```text
승인   master_decisions (+ 어느 실행인가는 master_agent_runs 가 든다)
원장   purchases
```

표를 새로 만들지 않는다. 미적용 목록을 어딘가에 들고 있으면 같은 사실의 주인이 둘이
되고, 한쪽만 고치는 날 갈린다. 둘 다 이미 있는 표이고, 맞대면 답이 나온다 —
`walk_report` 가 성적표를 새 표 없이 낸 것과 같은 자리다.

---

ID 규칙을 SQL 이 알지 못하게 한다.

  `purchase_id` 는 `PUR-{request_id}-D{decision_seq}-S{seq}` 다. 이것을 SQL 안에서
  `'PUR-' || d.request_id || '-D' || d.decision_seq || '-S'` 로 이어 붙이면 ID 규칙의
  주인이 둘(파이썬의 `domain/purchase_ids.py` 의 `purchase_id_prefix_for` 와 이 SQL)이
  되고, 한쪽만 바뀌는 날 이 조회가 에러 없이 늘 0건을 돌려준다.

  그래서 이 파일은 두 목록을 그냥 가져오기만 한다.

  ```text
  ① select_current_approvals(sim_run_id)    승인 행 (request_id · decision_seq · as_of)
  ② select_ledger_purchase_ids(sim_run_id)  그 실행의 purchase_id 전부
  ```

  조회 연결은 `readmodel/pending_transitions.py` 가 빌린다. 맞대는 것은
  `domain/pending_transition.pending_approvals` 이고, 거기서 앞머리를 만드는 것은 ID
  규칙의 주인 하나다.

② 를 통째로 가져오는 이유. 실행 하나의 매입 원장은 걷기 179일 × 품목 셋이라 크기가 안
는다. 회차별 `LIKE` 를 SQL 로 밀면 위의 규칙 하나를 SQL 이 알아야 하고, 그 대가가 이
편의보다 크다.
"""

from __future__ import annotations

from typing import Any

from psycopg import sql

from app.master.domain.decision import PROCUREMENT_CYCLE


def _table(schema: str, name: str) -> sql.Composable:
    return sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(name))


def select_current_approvals(conn: Any, *, sim_run_id: str, schema: str) -> list[dict[str, Any]]:
    """이 실행 축 · 매입 사이클에서 업무 키마다 최신 회차가 승인인 행(`DISTINCT ON`)."""
    query = sql.SQL(
        """
        SELECT request_id, decision_seq, as_of, sim_run_id, decided_by
        FROM (
            SELECT DISTINCT ON (d.request_id)
                   d.request_id  AS request_id,
                   d.decision_seq AS decision_seq,
                   d.decision     AS decision,
                   d.decided_by   AS decided_by,
                   r.as_of        AS as_of,
                   r.sim_run_id   AS sim_run_id
            FROM {} AS d
            JOIN {} AS r
              ON r.run_id = d.run_id
             AND r.request_id = d.request_id
            WHERE r.sim_run_id = %s
              AND r.cycle = %s
            ORDER BY d.request_id, d.decision_seq DESC
        ) AS current_decision
        WHERE decision = %s
        ORDER BY as_of, request_id
        """
    ).format(_table(schema, "master_decisions"), _table(schema, "master_agent_runs"))
    with conn.cursor() as cursor:
        cursor.execute(query, (sim_run_id, PROCUREMENT_CYCLE, "APPROVE"))
        return cursor.fetchall()


def select_ledger_purchase_ids(conn: Any, *, sim_run_id: str, schema: str) -> list[dict[str, Any]]:
    """이 실행 축의 `purchases` Header ID 행 전부(정산 상태로 거르지 않는다)."""
    query = sql.SQL("SELECT purchase_id FROM {} WHERE sim_run_id = %s").format(
        _table(schema, "purchases")
    )
    with conn.cursor() as cursor:
        cursor.execute(query, (sim_run_id,))
        return cursor.fetchall()
