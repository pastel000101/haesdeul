"""
collection_events.py — `master_collection_events` 를 읽어 수금 사건을 나른다.

🔴 **없던 것은 사건이 아니라 「수금일」이었다** (실측 2026-09-08 · `DB_SCHEMA=haetdeul`).

```text
haetdeul.receivables   15행
  COLLECTED  6   원금 19,010,294   미수          0
  PARTIAL    2   원금  5,896,967   미수  2,948,483
  OPEN       7   원금 18,974,071   미수 18,974,071

🟢 **얼마** 들어왔나   received_amount_krw 에 있다
🔴 **언제** 들어왔나   **어디에도 없다** — receivables 에 수금일 칸이 없다
🔴 수금 사건 표        **없었다** (%collect% · %cash% · %payment% 전수 0건)
```

★ 그래서 `database/master_collection_events.sql` 이 그 칸을 담을 표를 세웠고, 이
  모듈이 그 표를 읽는다. **`receivables` 에 칸을 더하지 않은 이유**는 `PARTIAL` 이
  실제로 2건 있어서다 — 한 채권이 여러 번 나눠 들어오고, 그것은 한 칸에 못 적는다.

---

🔴 **`CollectionEvent` 를 새로 만들지 않는다.** 재무 것(`app.finance.schemas.collections`)을
  그대로 쓴다. 마스터가 같은 모양을 하나 더 두면 *"수금 사건이 무엇인가"* 의 주인이
  둘이 되고, 재무가 칸을 더하는 날 조용히 갈린다.

⚠️ **`note` 는 사건에 안 실린다.** 표에는 있고 `CollectionEvent` 에는 없다 — 그 모양은
  재무가 정하는 것이라 마스터가 칸을 더하지 않는다. `note` 는 *"이 사건을 왜 사실로
  두었나"* 를 **표에** 남기는 자리이고, 사람이 표를 볼 때 쓴다.

---

🔴 **못 읽은 것과 없는 것은 다르다** (`opened_days_after` 와 같은 규율).

```text
()          이 축에 사건이 **없다**       → 오늘 들어올 것이 없었다 (정상)
예외        **못 읽었다**                 → 있었는지조차 모른다
```

  ⚠️ **조회 실패를 `()` 로 접지 않는다.** 접으면 표가 안 서 있거나 DB 가 끊긴 날이
    *"오늘은 들어올 게 없었다"* 로 읽히고, **들어왔어야 할 현금이 장부에 없는 채로**
    매입 판단이 돈다. 접는 자리는 여기가 아니라 부르는 쪽이고, 거기서 `BLOCKED` 가
    된다 (`app/master/finance_collection.py`).

★ 그래서 이 모듈은 **예외를 그대로 올린다.** 로그로 삼키지도 않는다.

★ 2026-09-30 재구성 BL-018: `master/collection_events.py` 에서 SQL · 행 읽기를 남겼다 — 받은 연결로
  실행한다(`select_collection_events`). 연결을 빌리는 것(종전 그대로 `connection()` 또는 넘겨받은
  `borrow`)은 `readmodel/collection_events.py` 다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from psycopg import sql

from app.core.settings import get_db_schema
from app.finance.schemas.collections import CollectionEvent

TABLE = "master_collection_events"

#: 조회 컬럼 순서. 아래 SELECT 와 **같아야 한다.**
_COLUMNS = (
    "sim_run_id",
    "financing_mode",
    "collection_date",
    "receivable_id",
    "target_received_total_krw",
)


def table(schema: str | None = None) -> sql.Composable:
    """수금 사건 표. `schema` 를 안 주면 여기서 읽는다(연결을 받아 쓰는 시드 경로 — 종전과 같다)."""
    name = get_db_schema() if schema is None else schema
    return sql.SQL("{}.{}").format(sql.Identifier(name), sql.Identifier(TABLE))


def select_collection_events(
    conn: Any, *, sim_run_id: str, financing_mode: str, schema: str
) -> tuple[CollectionEvent, ...]:
    """`(sim_run_id, financing_mode)` 축의 수금 사건 전부. 날짜순. 못 읽으면 예외."""
    query = sql.SQL(
        "SELECT sim_run_id, financing_mode, collection_date, receivable_id,"
        " target_received_total_krw"
        " FROM {} WHERE sim_run_id = %s AND financing_mode = %s"
        " ORDER BY collection_date, receivable_id"
    ).format(table(schema))
    with conn.cursor() as cursor:
        cursor.execute(query, (sim_run_id, financing_mode))
        rows = cursor.fetchall()
    return tuple(event(row) for row in rows)


def event(row: Any) -> CollectionEvent:
    """행 하나를 재무 사건으로 옮긴다. **값을 바꾸지 않는다.**

    ★ `dict_row` 면 Mapping, 아니면 순서 튜플이다 — `_COLUMNS` 와 짝이다.

    ⚠️ **금액을 반올림하거나 형변환하지 않는다.** 누적 target 은 재무가 delta 로
      접어 쓰는 값이라, 여기서 손대면 `receivables` 항등식이 어긋난 이유를 재무 쪽에서
      찾게 된다.
    """
    values = [row[name] for name in _COLUMNS] if isinstance(row, Mapping) else list(row)
    sim_run_id, financing_mode, collection_date, receivable_id, target = values
    return CollectionEvent(
        sim_run_id=sim_run_id,
        financing_mode=financing_mode,
        collection_date=collection_date,
        receivable_id=receivable_id,
        target_received_total_krw=target,
    )
