"""모듈 하나의 연결 대여를 가짜 연결로 바꾸는 검사 도우미.

★ 2026-09-30 재구성 BL-018: 마스터의 옛 SQL 헬퍼(`master/db.py` 의 `fetch_one` · `fetch_all` ·
  `execute_returning_one`)는 **연결 대여와 실행**을 한 함수로 했고, 검사는 그 함수를 모듈 이름에서
  갈아 끼워 «DB 대신 이 행을 준다» 를 했다. 헬퍼를 없애고 대여는 readmodel · service 가
  (`core_db.read_connection()` · `core_db.connection()` + `core_db.transaction(conn)`), 실행은
  repository 가(받은 연결의 커서) 하게 되어 문이 둘로 갈렸다.

  이 도우미는 **그 모듈의 `core_db` 이름 하나만** 가짜로 바꾼다 — 빌리면 가짜 연결을 주고, 그 연결의
  커서가 받은 문장 · 값을 검사가 준 옛 모양의 가짜 함수(`(문장, 값) → 행`)에 넘긴다. repository 의
  문장 짓기는 진짜로 돈다. 다른 모듈의 대여는 건드리지 않는다(막는 범위가 종전 헬퍼 한 함수와 같다).

```text
대여            가짜 함수
read_connection  fetchone → fetch_one(문장[, 값]) · fetchall → fetch_all(문장[, 값])
connection       fetchone → execute_returning_one(문장[, 값]) · fetchall → fetch_all(문장[, 값])
                 (값 없이 `execute(문장)` 했으면 옛 헬퍼처럼 문장 하나만 넘긴다)
transaction      아무것도 안 한다(가짜 연결에는 commit · rollback 이 없다 — 세지 않는다)
```

쓰는 법:

    patch_sql_helpers(monkeypatch, readmodel_inputs, fetch_one=one, fetch_all=many)
    patch_sql_helpers(monkeypatch, "app.master.readmodel.runs", fetch_all=fake)

같은 모듈에 두 번 부르면 같은 가짜에 함수를 더한다(옛 검사가 `fetch_one` · `fetch_all` 을 따로
갈아 끼우던 모양 그대로 옮길 수 있게).
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import ModuleType
from typing import Any, Self

import pytest

#: 값 없이 부른 `execute(문장)` 표지. 옛 헬퍼도 값이 없으면 `fetch_all(문장)` 하나로 불렀다 —
#: 가짜 함수에 넘기는 인자 모양을 그대로 맞춘다.
_NO_PARAMS: Any = object()


class _FakeCursor:
    def __init__(self, owner: _FakeCoreDb, kind: str) -> None:
        self._owner = owner
        self._kind = kind
        self._query: Any = None
        self._params: Any = _NO_PARAMS
        self.rowcount = 0

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, query: Any, params: Any = _NO_PARAMS) -> None:
        self._query, self._params = query, params

    def _call(self, name: str) -> Any:
        fake = self._owner.fakes.get(name)
        if fake is None:
            raise AssertionError(f"가짜 {name} 가 없는데 불렸다: {self._query!r}")
        if self._params is _NO_PARAMS:
            return fake(self._query)
        return fake(self._query, self._params)

    def fetchone(self) -> Any:
        return self._call("fetch_one" if self._kind == "read" else "execute_returning_one")

    def fetchall(self) -> Any:
        return self._call("fetch_all")


class _FakeConn:
    def __init__(self, owner: _FakeCoreDb, kind: str) -> None:
        self._owner = owner
        self._kind = kind

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._owner, self._kind)


class _FakeCoreDb:
    """`app.core.db` 자리에 앉는 가짜 — 대여 셋만 흉내 낸다."""

    Borrow = Callable[[], Any]

    def __init__(self) -> None:
        self.fakes: dict[str, Callable[..., Any]] = {}

    @contextmanager
    def read_connection(self) -> Iterator[_FakeConn]:
        yield _FakeConn(self, "read")

    @contextmanager
    def connection(self) -> Iterator[_FakeConn]:
        yield _FakeConn(self, "write")

    @contextmanager
    def transaction(self, conn: Any) -> Iterator[None]:
        yield None


def patch_sql_helpers(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType | str,
    *,
    fetch_one: Callable[..., Any] | None = None,
    fetch_all: Callable[..., Any] | None = None,
    execute_returning_one: Callable[..., Any] | None = None,
) -> _FakeCoreDb:
    """`module` 의 `core_db` 를 가짜로 바꾸고 옛 모양의 가짜 함수를 싣는다."""
    target = importlib.import_module(module) if isinstance(module, str) else module
    current = getattr(target, "core_db", None)
    fake = current if isinstance(current, _FakeCoreDb) else _FakeCoreDb()
    if fake is not current:
        monkeypatch.setattr(target, "core_db", fake)
    for name, fn in (
        ("fetch_one", fetch_one),
        ("fetch_all", fetch_all),
        ("execute_returning_one", execute_returning_one),
    ):
        if fn is not None:
            fake.fakes[name] = fn
    return fake
