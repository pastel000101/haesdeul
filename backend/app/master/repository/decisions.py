"""`master_decisions` 적재·조회.

★ `run_repository.try_save_run` 과 달리 **실패를 삼키지 않는다.**
  실행 이력은 없어도 결과를 줄 수 있지만, 결정은 안 남으면 승인이 없었던 것과 같다.
  적재가 실패하면 사용자에게 실패를 알려야 한다.

★ UPDATE·DELETE 가 없다. 번복은 `decision_seq` 를 올린 새 행이다.

★ 2026-09-30 재구성 BL-018: `master/decision_repository.py` 에서 SQL 만 남겼다 — 받은 연결로
  실행한다. 종전에는 `master/db.py` 헬퍼가 호출마다 연결을 스스로 빌렸다. 연결은 부르는 쪽이
  빌린다: 조회는 `readmodel/decisions.py`(조회 연결), 적재 · 후속 링크는 `service/decision.py`.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from psycopg import sql

from app.master.schemas.decision import Decision, DecisionOut, RevalidationOutcome

_TABLE = "master_decisions"
_COLUMNS = (
    "decision_id",
    "request_id",
    "decision_seq",
    "decision",
    "scenario_label",
    "condition_text",
    "decided_by",
    "follow_up_request_id",
    "end_code_at_decision",
    # DB 컬럼은 `run_id` 지만 코드에서는 `history_run_id` 로 부른다 —
    # `plan[].run_id`(부서 호출 id) 와 헷갈리지 않게 하려는 것이다.
    "run_id",
    # 최종 승인 시점 재검증 (2026-09-07). **`follow_up_request_id` 와 다른 칸이다** —
    # 저쪽은 조건부 재요청 체인이고 이쪽은 승인 직전 재검증이다.
    # `decision_service.record_decision` 이 승인에서만 채운다 (M-4). NULL 은
    # **"재검증을 하지 않았다"** 이지 실패가 아니다.
    "revalidation_request_id",
    "revalidation_outcome",
    "note",
    "created_at",
)


def _columns() -> sql.Composed:
    return sql.SQL(", ").join(sql.Identifier(c) for c in _COLUMNS)


def row_to_out(row: dict[str, Any]) -> DecisionOut:
    return DecisionOut(
        decision_id=cast(UUID, row["decision_id"]),
        request_id=row["request_id"],
        decision_seq=row["decision_seq"],
        decision=cast(Decision, row["decision"]),
        scenario_label=row.get("scenario_label"),
        condition_text=row.get("condition_text"),
        decided_by=row["decided_by"],
        follow_up_request_id=row.get("follow_up_request_id"),
        end_code_at_decision=row["end_code_at_decision"],
        history_run_id=(None if row.get("run_id") is None else str(row["run_id"])),
        revalidation_request_id=row.get("revalidation_request_id"),
        revalidation_outcome=cast("RevalidationOutcome | None", row.get("revalidation_outcome")),
        note=row.get("note"),
        created_at=row["created_at"],
    )


def select_decisions(conn: Any, request_id: str, *, schema: str) -> list[dict[str, Any]]:
    """한 요청에 붙은 결정 행 전부. 오래된 것부터."""
    query = sql.SQL("SELECT {} FROM {}.{} WHERE request_id = %s ORDER BY decision_seq ASC").format(
        _columns(),
        sql.Identifier(schema),
        sql.Identifier(_TABLE),
    )
    with conn.cursor() as cursor:
        cursor.execute(query, (request_id,))
        return cursor.fetchall()


def insert_decision(
    conn: Any,
    *,
    schema: str,
    decision_id: UUID,
    request_id: str,
    decision_seq: int,
    decision: Decision,
    decided_by: str,
    end_code_at_decision: str,
    scenario_label: str | None,
    condition_text: str | None,
    follow_up_request_id: str | None,
    history_run_id: str | None,
    revalidation_request_id: str | None,
    revalidation_outcome: RevalidationOutcome | None,
    note: str | None,
) -> dict[str, Any]:
    """결정 1건 INSERT … RETURNING. **행이 안 나오면 예외다** — 문구는 종전 헬퍼와 같다."""
    query = sql.SQL(
        """
        INSERT INTO {}.{} (
            decision_id, request_id, decision_seq, decision, scenario_label,
            condition_text, decided_by, follow_up_request_id, end_code_at_decision,
            run_id, revalidation_request_id, revalidation_outcome, note
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING {}
        """
    ).format(
        sql.Identifier(schema),
        sql.Identifier(_TABLE),
        _columns(),
    )
    with conn.cursor() as cursor:
        cursor.execute(
            query,
            (
                decision_id,
                request_id,
                decision_seq,
                decision,
                scenario_label,
                condition_text,
                decided_by,
                follow_up_request_id,
                end_code_at_decision,
                history_run_id,
                revalidation_request_id,
                revalidation_outcome,
                note,
            ),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Database write did not return a row")
        return row


def update_follow_up(
    conn: Any, *, decision_id: UUID, follow_up_request_id: str, schema: str
) -> bool:
    """NULL 이던 후속 링크만 채운다(`IS NULL` 조건 — 한 번만). 채웠으면 True."""
    query = sql.SQL(
        """
        UPDATE {}.{}
           SET follow_up_request_id = %s
         WHERE decision_id = %s
           AND follow_up_request_id IS NULL
        RETURNING decision_id
        """
    ).format(
        sql.Identifier(schema),
        sql.Identifier(_TABLE),
    )
    with conn.cursor() as cursor:
        cursor.execute(query, (follow_up_request_id, decision_id))
        return cursor.fetchone() is not None
