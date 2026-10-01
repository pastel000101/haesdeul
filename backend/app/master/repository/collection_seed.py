"""수금 사건 시드 SQL — 채권에서 그날 수금 사건 행을 만든다(받은 연결, commit 없음).

★ 2026-09-30 재구성 BL-018: `master/collection_seed.py` 에서 옮겼다 — `SIM_FIXED`, `_table`,
  `_receivables`, `_COLUMNS`, `_note`, `seed_collection_events`.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from psycopg import sql

from app.core.settings import get_db_schema
from app.master.repository.collection_events import TABLE
from app.master.schemas.collection_seed import CollectionSeedResult

#: 🔴 **가정임을 행마다 표시하는 말머리.** 이 문자열이 없으면 나중에 아무도 이 행이
#:   실측 입금인지 시뮬레이션 가정인지 못 가른다.
SIM_FIXED = "SIM_FIXED"


def _table() -> sql.Composable:
    return sql.SQL("{}.{}").format(sql.Identifier(get_db_schema()), sql.Identifier(TABLE))


def _receivables() -> sql.Composable:
    return sql.SQL("{}.{}").format(sql.Identifier(get_db_schema()), sql.Identifier("receivables"))


#: 조회 컬럼 순서. 아래 SELECT 와 **같아야 한다.**
_COLUMNS = (
    "receivable_id",
    "due_date",
    "original_amount_krw",
    "received_amount_krw",
)


def _note(*, due_date: Any, collection_date: Any, original: Any, received: Any) -> str:
    """*"이 사건을 왜 사실로 두었나"* 를 적는다. **파생식과 성격이 둘 다 들어간다.**

    🔴 **숫자를 넣는다.** 값만 넘기면 사람도 매입 판단도 확정으로 읽는다
      (`app/master/domain/inputs.py` 가 파생분에 파생식을 실어 내보내는 것과 같은 규율).

    🔴 **「당일」은 `collection_date == due_date` 인 날에만 참이다.** 기일 뒤에 집은
      건에까지 「당일」이라 적으면 메모가 **거짓**이 된다 — 그래서 갈라 적는다.

    ★★ **「휴장일이라」고 단정하지 않는다.** 마스터는 그날 왜 안 갔는지를 모른다 —
      휴장일일 수도, 걷기 구간 밖일 수도, 중단됐을 수도 있다. *"기일이 지나 처음 열린
      날"* 이 아는 만큼이고, 그 너머는 지어내는 것이다.

    ⚠️ **두 날짜를 둘 다 적는다.** 하나만 적으면 읽는 사람이 나머지를 되짚어야 한다.
    """
    delta = original - received
    if due_date == collection_date:
        가정 = "계약 결제기일 당일 전액 회수 가정."
    else:
        가정 = (
            "결제기일이 지나 처음 열린 날 전액 회수 가정"
            f" (기일 {due_date} · 회수 {collection_date})."
        )
    return (
        f"{SIM_FIXED}: {가정} 실제 입금 사실이 아니다."
        f" 근거 due_date={due_date} (sales.collection_due_date = sale_date + payment_days)."
        f" 원금 {original} · 기왕수금 {received} → 이 사건의 delta {delta}."
    )


def seed_collection_events(
    conn: Any,
    *,
    sim_run_id: str,
    financing_mode: str,
    as_of: date,
) -> CollectionSeedResult:
    """결제기일이 `as_of` 까지 **지난** 미수 채권을 `master_collection_events` 에 옮긴다.

    ★★ **`due_date <= as_of` 다. `= as_of` 가 아니다.** `SIM-CHAIN-V3` 실측에서 만기
      7건의 회수 여부가 **그날 걷기가 갔느냐와 7/7 로 맞았다** — 요일이 아니라 그것이
      전부였다. `= as_of` 면 걷기가 안 간 날의 만기는 아무도 다시 안 보고, 실제로
      2026-02-08(일) 만기 283,819원 한 건이 `OPEN` 으로 남아 재무가
      `SALES_PARTNER_HAS_OVERDUE_AR` 를 내기 시작했고 **3월 31일까지 판매가 한 건도
      안 섰다.** 28만원 한 건이 두 달치 판매를 죽였다.

    ★ **`collection_date` 는 그대로 `as_of` 다.** 그래서 일요일 만기는 월요일에 회수
      사건을 받는다. 이것은 `collection.py:30` 의 「`due_date` 경과 ≠ 자동 수금」을
      **어기는 것이 아니다** — 기일 경과를 수금으로 읽는 것이 아니라, 사건을 만드는
      날을 기일 뒤 처음 열린 날로 옮기는 것이다.

    🔴 **커밋하지 않는다. 커넥션도 열지 않는다.** 트랜잭션 경계는 부르는 쪽 것이다
      (`DayOpening` Protocol 과 같은 분담).

    ⚠️ **`receivables` 에는 `financing_mode` 칸이 없다.** 그 표의 축은 `sim_run_id`
      하나이고, `financing_mode` 는 **만들 사건의 축**으로만 쓴다 — 재무가 준 값을
      그대로 싣는다.

    🔴 **`outstanding_amount_krw > 0` 만 고른다.** `COLLECTED` 는 낼 것이 없어 행을
      만들지 않는다.

    🔴 **`target_received_total_krw` 는 `original_amount_krw` 다.** `outstanding` 이
      아니다 — 이 칸은 **누적** target 이라 잔액을 적으면 두 번째 분할 수금에서
      역행으로 거부된다. 그리고 이 한 줄이 조건 `④⑤` 를 동시에 만든다.
    """
    query = sql.SQL(
        "SELECT receivable_id, due_date, original_amount_krw, received_amount_krw"
        " FROM {} WHERE sim_run_id = %s AND due_date <= %s AND outstanding_amount_krw > 0"
        " ORDER BY receivable_id"
    ).format(_receivables())

    insert = sql.SQL(
        "INSERT INTO {} (sim_run_id, financing_mode, collection_date, receivable_id,"
        " target_received_total_krw, note)"
        " VALUES (%s, %s, %s, %s, %s, %s)"
        " ON CONFLICT DO NOTHING"
    ).format(_table())

    created = 0
    skipped = 0
    with conn.cursor() as cursor:
        cursor.execute(query, (sim_run_id, as_of))
        rows = cursor.fetchall()
        for row in rows:
            values = (
                [row[name] for name in _COLUMNS] if isinstance(row, Mapping) else list(row)
            )
            receivable_id, due_date, original, received = values
            cursor.execute(
                insert,
                (
                    sim_run_id,
                    financing_mode,
                    as_of,
                    receivable_id,
                    original,
                    _note(
                        due_date=due_date,
                        collection_date=as_of,
                        original=original,
                        received=received,
                    ),
                ),
            )
            if cursor.rowcount == 1:
                created += 1
            else:
                # ★ **이미 있던 사건이다.** PK 가 멱등을 잡는 자리가 여기다.
                skipped += 1

    return CollectionSeedResult(created=created, skipped=skipped)
