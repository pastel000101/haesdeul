"""`tests/core` 공용 — psycopg_pool 이 만드는 연결의 가짜 (`FakePgConnection`).

★ `conftest.py` 가 아니라 이 모듈에 두는 이유: 검사 모듈이 `conftest` 를 이름으로 가져가면
  폴더를 합쳐 돌릴 때 수집이 멈춘다 (`tests/master/test_conftest_not_imported_by_name.py`).
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Callable
from typing import Any, ClassVar, Self

from psycopg import errors, pq

IDLE = pq.TransactionStatus.IDLE
INTRANS = pq.TransactionStatus.INTRANS
INERROR = pq.TransactionStatus.INERROR
UNKNOWN = pq.TransactionStatus.UNKNOWN


class _Result:
    status = pq.ExecStatus.EMPTY_QUERY
    error_message = b""


class _PgConn:
    """`conn.pgconn` 자리. 풀이 읽는 `transaction_status` 와, 빌려 주기 전 확인
    (`app.core.db.check_connection_within`)이 쓰는 libpq 비동기 API 몇 개를 흉내 낸다.

    확인은 빈 질의를 보내고(`send_query`) 답이 올 때까지 소켓을 기다린다. 이 가짜는 답을 바로
    주거나(`is_busy` 0), 연결의 `hang_checks` 가 참이면 끝내 답을 주지 않는다(`is_busy` 1 · 아무도
    쓰지 않는 소켓 — 기다리는 쪽은 자기 시간 제한까지 기다린다).
    """

    def __init__(self, connection: FakePgConnection) -> None:
        self.transaction_status = IDLE
        self._connection = connection
        self._pending: list[_Result] = []
        self._silent: tuple[socket.socket, socket.socket] | None = None

    @property
    def status(self) -> pq.ConnStatus:
        return pq.ConnStatus.BAD if self._connection.closed else pq.ConnStatus.OK

    @property
    def socket(self) -> int:
        if self._silent is None:
            self._silent = socket.socketpair()
        return self._silent[0].fileno()

    def send_query(self, command: bytes) -> None:
        conn = self._connection
        if conn.closed:
            raise errors.OperationalError("the connection is closed")
        conn.events.append("check")
        error = conn.check_error() if callable(conn.check_error) else conn.check_error
        if error is not None:
            raise error
        self._pending = [] if conn.hang_checks else [_Result()]

    def flush(self) -> int:
        return 0

    def consume_input(self) -> None:
        return None

    def is_busy(self) -> int:
        return 1 if self._connection.hang_checks else 0

    def get_result(self) -> _Result | None:
        return self._pending.pop(0) if self._pending else None

    def finish(self) -> None:
        if self._silent is not None:
            for end in self._silent:
                end.close()
            self._silent = None


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
    - 빌려 주기 전 확인은 `pgconn` 의 libpq 비동기 API 로 온다 — 이벤트 «check» 로 남는다.

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
        self._closed = False
        self.pgconn = _PgConn(self)
        self.lock = threading.Lock()
        self._autocommit = False
        #: 참이면 빌려 주기 전 확인에 끝내 답하지 않는다(DB 프로세스가 멈춘 것처럼).
        self.hang_checks = False
        #: 있으면 빌려 주기 전 확인이 이 오류로 실패한다. 함수면 확인할 때마다 불러
        #: 오류(또는 None)를 받는다.
        self.check_error: BaseException | Callable[[], BaseException | None] | None = None
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
        """psycopg_pool 의 기본 확인(`ConnectionPool.check_connection`)이 부르는 자리."""
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
        self.pgconn.finish()

    def break_(self) -> None:
        """서버 쪽에서 끊긴 연결(검사용)."""
        self._closed = True
        self.pgconn.transaction_status = UNKNOWN
