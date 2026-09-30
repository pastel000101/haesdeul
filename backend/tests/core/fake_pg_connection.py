"""`tests/core` 공용 — psycopg_pool 이 만드는 연결의 가짜 (`FakePgConnection`).

★ `conftest.py` 가 아니라 이 모듈에 두는 이유: 검사 모듈이 `conftest` 를 이름으로 가져가면
  폴더를 합쳐 돌릴 때 수집이 멈춘다 (`tests/master/test_conftest_not_imported_by_name.py`).
"""

from __future__ import annotations

from typing import Any, ClassVar, Self

from psycopg import errors, pq

IDLE = pq.TransactionStatus.IDLE
INTRANS = pq.TransactionStatus.INTRANS
INERROR = pq.TransactionStatus.INERROR
UNKNOWN = pq.TransactionStatus.UNKNOWN


class _PgConn:
    """`conn.pgconn` 자리 — 풀이 읽는 것은 `transaction_status` 하나다."""

    def __init__(self) -> None:
        self.transaction_status = IDLE


class FakeCursor:
    def __init__(self, connection: FakePgConnection) -> None:
        self.connection = connection
        self.rowcount = 0

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def execute(self, query: Any, params: Any = None) -> None:
        conn = self.connection
        conn.events.append("execute")
        conn.executed.append((query, params))
        if not conn.autocommit:
            conn.pgconn.transaction_status = INTRANS
        if conn.fail_on_execute is not None:
            if not conn.autocommit:
                conn.pgconn.transaction_status = INERROR
            raise conn.fail_on_execute

    def executemany(self, query: Any, rows: Any) -> None:
        conn = self.connection
        conn.events.append("executemany")
        conn.executed.append((query, list(rows)))
        if not conn.autocommit:
            conn.pgconn.transaction_status = INTRANS

    def fetchone(self) -> Any:
        return self.connection.one

    def fetchall(self) -> Any:
        return list(self.connection.rows)


class FakePgConnection:
    """psycopg_pool 이 만드는 연결의 가짜. **풀이 부르는 것과 psycopg 의 규칙만** 흉내 낸다.

    - `connect(conninfo, **kwargs)` — 풀의 작업 스레드가 부른다. 인자를 `connects` 에 적는다.
    - 트랜잭션 상태: autocommit 이 꺼져 있으면 첫 문장에서 INTRANS, 실패하면 INERROR,
      `commit`/`rollback` 으로 IDLE, `close` 로 UNKNOWN(끊김).
    - `autocommit` 은 IDLE 일 때만 바꿀 수 있다(psycopg 3.3.4 `_check_intrans_gen`).

    검사마다 `reset()` 으로 기록과 설정을 비운다.
    """

    connects: ClassVar[list[dict[str, Any]]] = []
    made: ClassVar[list[FakePgConnection]] = []
    #: 다음 `connect` 가 올릴 오류(있으면).
    fail_connect: ClassVar[BaseException | None] = None
    #: 새 연결에 줄 기본값.
    one: ClassVar[Any] = None
    rows: ClassVar[Any] = ()

    def __init__(self, kwargs: dict[str, Any]) -> None:
        self.kwargs = kwargs
        self.pgconn = _PgConn()
        self._autocommit = False
        self._closed = False
        self._pool: Any = None
        self.events: list[str] = []
        self.executed: list[tuple[Any, Any]] = []
        self.fail_on_execute: BaseException | None = None
        self.one = type(self).one
        self.rows = type(self).rows

    @classmethod
    def reset(cls) -> None:
        cls.connects = []
        cls.made = []
        cls.fail_connect = None
        cls.one = None
        cls.rows = ()

    @classmethod
    def connect(cls, conninfo: str = "", **kwargs: Any) -> FakePgConnection:
        if cls.fail_connect is not None:
            raise cls.fail_connect
        cls.connects.append(kwargs)
        conn = cls(kwargs)
        cls.made.append(conn)
        return conn

    def __repr__(self) -> str:
        return f"<FakePgConnection {self.kwargs.get('dbname')}>"

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def autocommit(self) -> bool:
        return self._autocommit

    @autocommit.setter
    def autocommit(self, value: bool) -> None:
        if self.pgconn.transaction_status != IDLE:
            raise errors.ProgrammingError("can't change 'autocommit' now")
        self._autocommit = value

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def execute(self, query: Any, params: Any = None) -> None:
        """풀의 살아 있는지 확인(`check_connection`)이 부르는 자리."""
        if self._closed:
            raise errors.OperationalError("the connection is closed")
        self.events.append("check")

    def commit(self) -> None:
        self.events.append("commit")
        self.pgconn.transaction_status = IDLE

    def rollback(self) -> None:
        self.events.append("rollback")
        self.pgconn.transaction_status = IDLE

    def close(self) -> None:
        self.events.append("close")
        self._closed = True
        self.pgconn.transaction_status = UNKNOWN

    def break_(self) -> None:
        """서버 쪽에서 끊긴 연결(검사용)."""
        self._closed = True
        self.pgconn.transaction_status = UNKNOWN
