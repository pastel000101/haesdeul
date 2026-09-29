"""재무 테스트용 가짜 연결 — 풀에서 빌리는 자리를 **가짜 연결 하나**로 바꾼다.

★ 2026-09-29 재구성 BL-014: 재무 SQL 은 repository 가 **넘겨받은 연결**로 실행하고, 연결은
  readmodel(조회 · `core_db.read_connection`)과 service(쓰기 트랜잭션 · `core_db.connection`)가
  빌린다. 전에는 테스트가 `app.finance.db.fetch_all` 한 자리를 바꿔 끼웠다 — 이제는 빌리는 입구
  둘을 바꾸고, SQL 은 진짜 repository 코드가 가짜 커서에 실행한다. 판매
  (`tests/sales/sales_fake_connection.py`)와 같은 모양이다.

★ 실 DB 가드(루트 `tests/conftest.py`)는 그대로다. 이 헬퍼를 안 쓰고 재무 조회를 부르면
  빌리는 순간 가드에 막힌다.

```python
conn = lend(monkeypatch, answer)        # answer(query_text, params) -> 행 목록
...
assert conn.borrows == ["read"]         # 조회 연결을 한 번 빌렸다
```

`answer` 는 SQL 을 문자열(`as_string(None)` — 없으면 `str`)로 받는다. 질의마다 다른 행을
돌려주려면 문면으로 가른다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, Self

import pytest

Answer = Callable[[str, Any], Any]


def query_text(query: Any) -> str:
    """psycopg `sql.Composed` 든 문자열이든 비교할 수 있는 한 줄로."""
    try:
        text = query.as_string(None)
    except Exception:  # noqa: BLE001 - 문자열 질의
        text = str(query)
    return " ".join(text.split())


class FakeCursor:
    """`answer` 가 돌려준 행을 `fetchone` · `fetchall` 로 내준다."""

    def __init__(self, conn: FakeConnection) -> None:
        self.conn = conn
        self.rows: list[Any] = []
        self.rowcount = 0

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def execute(self, query: Any, params: Any = None) -> None:
        text = query_text(query)
        self.conn.executed.append((text, params))
        result = self.conn.answer(text, params)
        self.rows = [] if result is None else list(result)
        self.rowcount = len(self.rows)

    def fetchall(self) -> list[Any]:
        return list(self.rows)

    def fetchone(self) -> Any:
        return self.rows[0] if self.rows else None


class FakeConnection:
    """빌린 연결 하나. 실행한 SQL · commit · rollback · 반환을 순서대로 적는다."""

    def __init__(self, answer: Answer | None = None) -> None:
        self.answer: Answer = answer or (lambda _query, _params: [])
        self.executed: list[tuple[str, Any]] = []
        self.events: list[str] = []
        self.borrows: list[str] = []

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.events.append("commit")

    def rollback(self) -> None:
        self.events.append("rollback")


def lend(
    monkeypatch: pytest.MonkeyPatch, answer: Answer | None = None, *, reads: bool = True
) -> FakeConnection:
    """풀 대여 입구 둘(`connection` · `read_connection`)이 이 가짜 연결을 빌려 주게 한다.

    :param reads: 거짓이면 쓰기 입구(`connection`)만 바꾼다. 조회는 그대로 실 DB 가드에 막힌다 —
        종전 검사가 쓰기 헬퍼만 바꿔 끼우고 조회는 실패하게 두던 조건을 그대로 잴 때 쓴다.
    """
    conn = FakeConnection(answer)

    @contextmanager
    def borrow(kind: str) -> Iterator[FakeConnection]:
        conn.borrows.append(kind)
        try:
            yield conn
        finally:
            conn.events.append(f"returned:{kind}")

    monkeypatch.setattr("app.core.db.connection", lambda: borrow("write"))
    if reads:
        monkeypatch.setattr("app.core.db.read_connection", lambda: borrow("read"))
    monkeypatch.setenv("DB_SCHEMA", "haetdeul")
    return conn


@contextmanager
def lent(answer: Answer | None = None, *, reads: bool = True) -> Iterator[FakeConnection]:
    """`lend` 의 `with` 판 — `patch(...)` 묶음 안에 나란히 쓴다. 블록이 끝나면 되돌린다."""
    with pytest.MonkeyPatch.context() as monkeypatch:
        yield lend(monkeypatch, answer, reads=reads)
