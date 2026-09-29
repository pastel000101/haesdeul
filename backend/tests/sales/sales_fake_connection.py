"""판매 테스트용 가짜 연결 — 풀에서 빌리는 자리를 **가짜 연결 하나**로 바꾼다.

★ 2026-09-29 BL-013: 판매 SQL 은 repository 가 **넘겨받은 연결**로 실행하고, 연결은
  readmodel(조회 · `core_db.read_connection`)과 service(쓰기 트랜잭션 · `core_db.connection`)가
  빌린다. 전에는 테스트가 모듈마다 `fetch_all` · `execute_returning_one` 을 바꿔 끼웠다 — 이제는
  빌리는 입구 둘을 바꾸고, SQL 은 진짜 repository 코드가 가짜 커서에 실행한다.

★ 실 DB 가드(루트 `tests/conftest.py`)는 그대로다. 이 헬퍼를 안 쓰고 판매 조회를 부르면
  빌리는 순간 가드에 막힌다.

```python
conn = lend(monkeypatch, answer)        # answer(query, params) -> 행 목록
...
assert conn.borrows == ["read"]         # 조회 연결을 한 번 빌렸다
assert conn.events == ["returned:read"] # commit 없이 돌려줬다
```
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, Self

import pytest

Answer = Callable[[Any, Any], Any]


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
        self.conn.executed.append((query, params))
        result = self.conn.answer(query, params)
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
        self.executed: list[tuple[Any, Any]] = []
        self.events: list[str] = []
        self.borrows: list[str] = []

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.events.append("commit")

    def rollback(self) -> None:
        self.events.append("rollback")


def lend(monkeypatch: pytest.MonkeyPatch, answer: Answer | None = None) -> FakeConnection:
    """풀 대여 입구 둘(`connection` · `read_connection`)이 이 가짜 연결을 빌려 주게 한다."""
    conn = FakeConnection(answer)

    @contextmanager
    def borrow(kind: str) -> Iterator[FakeConnection]:
        conn.borrows.append(kind)
        try:
            yield conn
        finally:
            conn.events.append(f"returned:{kind}")

    monkeypatch.setattr("app.core.db.connection", lambda: borrow("write"))
    monkeypatch.setattr("app.core.db.read_connection", lambda: borrow("read"))
    return conn
