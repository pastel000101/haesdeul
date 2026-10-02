"""물류 repository 의 공용 도우미 — 스키마 이름 · 행 읽기.

  ```text
  get_db_schema      `.env` 를 프로세스에서 한 번만 적재하고 스키마 이름을 읽는다
  schema_identifier  스키마 이름을 SQL 식별자로
  cell               행 한 칸 — 매핑이면 이름으로, 아니면 순서로
  dict_rows          행을 dict 로 편다
  named_rows         칸 이름 목록으로 편다(튜플 행도 읽는다)
  ```

예외 종류 · 문구가 모듈마다 다른 `_quantity` · `_require_text` · `_one` 류는 여기로 합치지
않는다 — 같은 이름이어도 규약(어느 실패로 멈추나 · 0 이하를 받나)이 갈린다.

행 모양을 강요하지 않는다. 공통 풀은 연결을 `dict_row` 로 만들지만 검사 대역은 튜플 행을 주기도
한다 — `cell` · `named_rows` 는 둘 다 읽는다.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from psycopg import sql

from app.core import settings


def get_db_schema() -> str:
    """물류 SQL 이 쓸 스키마 이름. `.env` 는 프로세스에서 한 번만 적재하고 값은 매번 읽는다.

    대시보드 한 요청에 스키마 이름을 550번 읽으며 매번 `.env` 를 파싱해 7.8초를 쓴 일이 있어
    물류는 한 번만 적재한다(`core/settings.load_env_file_once`).
    적재 뒤 값은 환경변수에서 매번 읽으므로 검사가 `DB_SCHEMA` 를 바꾸면 그대로 따라간다.
    """
    return settings.get_db_schema(load=settings.load_env_file_once)


def schema_identifier() -> sql.Identifier:
    """`get_db_schema()` 를 SQL 식별자로."""
    return sql.Identifier(get_db_schema())


def cell(row: Any, index: int, name: str) -> Any:
    """행 한 칸 — 매핑이면 이름으로, 아니면 순서로 꺼낸다."""
    if isinstance(row, Mapping):
        return row[name]
    return row[index]


def dict_rows(conn: Any, query: Any, params: Any = None) -> list[dict[str, Any]]:
    """받은 연결로 실행하고 행을 dict 로 편다."""
    with conn.cursor() as cursor:
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]


def named_rows(
    conn: Any, query: Any, params: Any, names: Sequence[str]
) -> list[dict[str, Any]]:
    """받은 연결로 실행하고 행을 `names` 칸 이름의 dict 로 편다(튜플 행도 읽는다)."""
    with conn.cursor() as cursor:
        cursor.execute(query, params)
        found = cursor.fetchall()
    return [{n: cell(r, i, n) for i, n in enumerate(names)} for r in found]
