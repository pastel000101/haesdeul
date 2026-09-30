"""매입 탭이 읽는 마스터 표 SQL — 실행 · 확정 매입 · 결정 · 품목 이름 · 도착일 맞춤.

★ 2026-09-29 재구성 BL-014: 화면 `api/purchase/query._read` 가 재무 DB 입구(`app.finance.db`)의
  조회 헬퍼로 직접 돌리던 SQL 을 옮겼다(문면 · 인자 · 순서 그대로). 당시 조회 헬퍼는 마스터 입구
  `app.master.db.fetch_all` 이었고 조회마다 조회 연결을 빌렸다(종전과 같은 대여). 읽기 순서와
  날짜 창은 `readmodel/purchase_tab.py`, 표 · 문장은 화면이 맡는다.

★ 스키마 이름은 `.env`(`get_db_schema`)가 정한다. 매입 에이전트는 일부러 `get_db_schema` 를
  쓰지 않지만(*"`.env` 가 어느 시세 테이블을 읽을지 정하면 안 된다"* — 에이전트 경로의 이유),
  여기는 화면이 보는 `haetdeul` 도메인 표라 `.env` 가 정하는 것이 맞다 (종전 화면 머리말).
  쓰기 헬퍼는 가져오지 않는다.

★ 2026-09-30 재구성 BL-018: `master/purchase_tab_repository.py` 에서 옮겼다. 받은 연결로 실행만 한다
  (`select_*`) — 조회 연결은 `readmodel/purchase_tab.py` 가 조회마다 하나씩 빌린다(종전과 같은
  횟수).
"""

from datetime import date
from typing import Any

from psycopg import sql

#: 실행 조회(`runs`)가 응답 본문에서 **뽑는 칸** — 매입 탭(`api/purchase/presenter.py`)이 `payload`
#: 에서 읽는 전부다.
#:
#: 🔵 (2026-09-17) 전에는 `response_payload` 를 통째로 끌어왔다 — REH-0914 08-31 41행이
#:   1.6MB 인데 읽는 칸은 6% 남짓이었다 (scenarios 79KB · judgment 14KB · reason 2.5KB).
#:   V13 01-26 은 137행 7.8MB 였다.
#:
#: 🔴 **여기 없는 칸을 `payload` 에서 읽으면 조용히 비어 온다** — `.get()` 이라 예외도
#:    안 난다(안별 컷 사유 · 사유 문장이 「사유를 남긴 실행이 없습니다」로 바뀐다).
#:    그래서 `tests/api/test_purchase_read_narrow.py` 가 매입 탭 소스를 읽어 `payload`
#:    에서 부르는 `.get("…")` 이 전부 여기 있는지 본다. 칸을 새로 읽으면 **여기에 먼저** 적는다.
#:
#: 값이 튜플이면 그 칸 안에서 다시 **그 하위 칸만** 뽑는다 (`judgment` 는 컷 사유만 쓴다).
RUN_PAYLOAD: dict[str, tuple[str, ...]] = {
    "scenarios": (),
    "judgment": ("rejected_reasons",),
    "reason": (),
}


def _payload_projection() -> Any:
    """`response_payload` 에서 `RUN_PAYLOAD` 칸만 뽑아 **같은 모양의 객체**로 만든다.

    ★ 읽는 쪽 코드는 한 글자도 안 바뀐다 — `run["payload"]["scenarios"]` 그대로다.
    ⚠️ 원본에 칸이 없으면 뽑은 객체에는 `null` 로 선다. 읽는 쪽이 전부 `.get(…) or …` 라
       「칸 없음」과 「null」이 같은 판정으로 간다 (실측 — PROCUREMENT 15,235행 중 judgment
       없는 행 3,098 · scenarios 없는 행 3,072 · 본문이 SQL NULL 인 행 0).
    """
    def field(key: str, sub: tuple[str, ...]) -> sql.Composable:
        path = sql.SQL("response_payload->{}").format(sql.Literal(key))
        if not sub:
            return sql.SQL("{}, {}").format(sql.Literal(key), path)
        inner = sql.SQL(", ").join(
            sql.SQL("{}, {}->{}").format(sql.Literal(k), path, sql.Literal(k)) for k in sub
        )
        return sql.SQL("{}, jsonb_build_object({})").format(sql.Literal(key), inner)

    body = sql.SQL(", ").join(field(k, sub) for k, sub in RUN_PAYLOAD.items())
    return sql.SQL(
        "CASE WHEN response_payload IS NULL THEN NULL ELSE jsonb_build_object({}) END"
    ).format(body)


def _table(schema: str, name: str) -> sql.Composable:
    return sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(name))


def select_procurement_runs(conn: Any, as_of: date, *, schema: str) -> list[dict[str, Any]]:
    """그날 매입 실행 행 — 최신부터, 본문은 `RUN_PAYLOAD` 칸만."""
    return _fetch_all(
        conn,
        sql.SQL(
            #  🔴 `run_id` 는 **말로 한 승인이 짚을 행**이다 (2026-09-16). 업무 키 하나에
            #     실행이 여러 행이라(실측 75행) 키만으로는 본 것과 다른 안이 승인될 수
            #     있다 — 마스터 승인 경로가 `history_run_id` 를 받는 이유와 같다.
            "SELECT run_id, request_id, item, end_code, runtime_status, created_at,"
            #  🔵 본문은 **쓰는 칸만** 뽑아 같은 모양(`payload`)으로 싣는다 (`RUN_PAYLOAD`).
            " sim_run_id, {} AS payload"
            " FROM {} WHERE as_of = %(as_of)s AND cycle = 'PROCUREMENT'"
            " ORDER BY created_at DESC"
        ).format(_payload_projection(), _table(schema, "master_agent_runs")),
        {"as_of": as_of},
    )


def select_approved_purchase_lines(conn: Any, as_of: date, *, schema: str) -> list[dict[str, Any]]:
    """그날까지의 승인 매입 줄 (`MASTER_APPROVAL`) — 최신순."""
    #  🔴 레슨 ③ — purchases 와 purchase_items 를 조인하면 total_amount_krw 가
    #     줄마다 반복된다 (PUR-KIMCHI-015 는 5줄이고 다섯 다 3,370,487). 줄 금액은
    #     line_amount_krw 로 읽는다. 여기서는 아예 total 을 안 가져온다.
    return _fetch_all(
        conn,
        sql.SQL(
            "SELECT p.purchase_id, p.purchase_date, p.payment_due_date,"
            " p.settlement_status, p.sim_run_id, i.item_id, i.grade, i.quantity_kg,"
            " i.unit_price_krw_per_kg, i.line_amount_krw"
            " FROM {} p JOIN {} i USING (purchase_id)"
            " WHERE p.purchase_type = 'MASTER_APPROVAL' AND p.purchase_date <= %(as_of)s"
            #  🔵 **최신순이다** (2026-09-17). 오래된 순이면 오늘 산 줄이 수백 줄 맨 아래에
            #     깔린다 (REH-0914 08-31 · 291줄). 같은 날 안에서도 뒤집어 **통째로 역순**이다.
            #  ⚠️ 순서에 기대는 계산은 없다 — 이번 주 매입액 · 입고 예정은 합이고, 도착일 맞춤은
            #     `arrivals` 쪽 순서를 쓴다. 화면이 자르는 것은 페이지뿐이다(`page.tsx`).
            " ORDER BY p.purchase_date DESC, i.purchase_item_id DESC"
        ).format(_table(schema, "purchases"), _table(schema, "purchase_items")),
        {"as_of": as_of},
    )


def select_decisions(conn: Any, request_ids: list[str], *, schema: str) -> list[dict[str, Any]]:
    """그날 실행의 요청들에 적힌 결정 — 회차(`decision_seq`)까지."""
    #  🟡 **그날 실행이 없어도 조회를 낸다** — 빈 목록이면 0행이다(실 DB 로 확인 · 2026-09-18).
    #     `WHERE` 는 **SELECT 줄과 떨어진 자리에** 붙인다. 번복 고침(`#820`)이 그 SELECT 줄에
    #     `decision_seq` 를 더하고, 그 검사는 실행 없이 결정 조회 문면을 본다 — 둘이 어느 순서로
    #     들어와도 줄이 안 부딪치고 검사도 안 깨지게 한다.
    return _fetch_all(
        conn,
        #  🔴 `decision_seq` 를 같이 읽는다 (2026-09-17) — 결정 표는 append-only 라 번복도
        #     새 행이고 **최대 회차가 유효하다**. 순서를 안 읽으면 되돌린 승인이 남는다.
        sql.SQL("SELECT request_id, decision_seq, decision, scenario_label FROM {}").format(
            _table(schema, "master_decisions")
        )
        + sql.SQL(" WHERE request_id = ANY(%(request_ids)s)"),
        {"request_ids": request_ids},
    )


def select_item_names(conn: Any, *, schema: str) -> list[dict[str, Any]]:
    """품목 id → 이름을 만들 품목 행."""
    return _fetch_all(
        conn, sql.SQL("SELECT item_id, item_name FROM {}").format(_table(schema, "items"))
    )


def select_arrival_runs(
    conn: Any, dates: list[date], sim_run_id: str | None, *, schema: str
) -> list[dict[str, Any]]:
    """원장 날짜들의 매입 실행 시나리오 — 확정 매입의 도착일을 금액으로 맞춰 오는 재료."""
    #  🔵 축 조건은 **줄 때만** 붙인다. `None` 을 `= %(sim)s` 에 넣으면 `NULL = NULL` 이라
    #     0행이 되고, 그건 «안 거른다» 가 아니라 «다 버린다» 다.
    axis = sql.SQL("") if sim_run_id is None else sql.SQL(" AND sim_run_id = %(sim)s")
    return _fetch_all(
        conn,
        sql.SQL(
            "SELECT as_of, item, sim_run_id, response_payload->'scenarios' AS scenarios"
            " FROM {} WHERE as_of = ANY(%(dates)s) AND cycle = 'PROCUREMENT'{}"
        ).format(_table(schema, "master_agent_runs"), axis),
        {"dates": dates} if sim_run_id is None else {"dates": dates, "sim": sim_run_id},
    )


def _fetch_all(conn: Any, query: Any, params: Any = None) -> list[dict[str, Any]]:
    with conn.cursor() as cursor:
        cursor.execute(query, params)
        return cursor.fetchall()
