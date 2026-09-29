"""PostgreSQL 연결 모듈 — 연결 풀을 **준비하고, 연결을 빌려 주고, 돌려받고, 정리한다.**

```text
준비      DatabasePool(name, settings)   접속 대상 하나에 풀 하나. 첫 대여(또는 open)에서 열린다
          SERVICE_POOL                   서비스 DB(DB_*) 풀. 부서 다섯과 마스터가 함께 쓴다
빌려 준다 connection()                   블록 동안 빌려 주고 끝나면 돌려준다. commit 없음
          transaction(conn)              블록 하나 = 트랜잭션 하나. 정상 commit · 예외 rollback
          read_connection()              조회 전용 대여. 문장마다 바로 끝난다(autocommit)
          db_connection()                HTTP 입구의 Depends 용 — 요청 처리 동안 connection() 하나
정리      pool_lifespan()                앱 lifespan · CLI main 이 감싼다. 시작 때 서비스 풀을 열고
                                         끝날 때(정상 · 예외) 모든 풀을 닫는다
          close_pools()
```

★ **돌려주는 것과 트랜잭션을 끝내는 것은 다른 일이다** (2026-09-29 풀 전환).

  - commit · rollback 은 업무 경계를 쥔 쪽(service · 마스터 경계 함수 · 라우터)이
    `conn.commit()` · `conn.rollback()` 이나 `transaction(conn)` 으로 한다.
  - `connection()` 블록이 끝나면 연결을 풀에 돌려준다. 끝나지 않은 트랜잭션이 남아 있으면
    **rollback 한다 — 반환이 commit 을 만들지 않는다.** 종전 `close()` 가 커밋 안 된 일을
    버리던 것과 같은 결과다.

🔴 **빌린 연결을 `close()` 하지 않는다.** 이 풀은 `close()` 를 반환으로 바꾸지 않는다
   (`close_returns=False` · psycopg_pool 3.3.3 소스) — 연결이 진짜로 닫히고, 풀은 돌아온
   연결을 버린 뒤 새로 만든다.

🔴 **빌린 연결에 `with conn:` 을 쓰지 않는다.** psycopg 3.3.4 `Connection.__exit__` 는 풀에서
   온 연결이면 commit(또는 rollback)만 하고 닫지도 돌려주지도 않는다. 블록은 `connection()`
   · `transaction(conn)` 으로 연다.

★ **조회는 트랜잭션 없이 한 문장씩 끝낸다** (`read_connection`). 종전 부서 읽기 범위
  (`ReadConnectionScope`)가 하던 «화면 한 판 = 연결 하나» 는 풀이 연결을 재사용하므로 필요 없다.
  남는 차이는 조회마다 BEGIN · COMMIT 왕복이 붙는 것인데, autocommit 으로 읽으면 한 문장 =
  한 왕복이다. 읽기 전용 SELECT 라 보이는 결과는 같다(READ COMMITTED 는 문장마다 새 스냅숏).

★ **빌려 줄 때 연결이 살아 있는지 확인한다** (`check=ConnectionPool.check_connection` · 빈
  질의 한 번). 종전에는 매번 새 연결이라 죽은 연결을 받을 일이 없었다 — DB 가 재시작되거나
  쉬던 연결이 끊겨도 요청이 그 연결로 실패하지 않게 풀이 바꿔 준다.

★ **이 모듈은 SQL 을 실행하지 않는다.** SQL 은 연결을 받은 쪽(부서 `db.py` 의 조회 함수,
  repository 함수)이 `conn.cursor()` 로 직접 실행한다 (`tests/core/test_core_db.py` 가 잠근다).

★ 역할 나눔 (설계서 §책임 위치)

```text
router / CLI   요청을 받아 service 를 부른다. HTTP 는 Depends(db_connection) 로 연결을 받는다
service        업무 순서와 트랜잭션 경계: with connection() as conn, transaction(conn):
repository     받은 conn 으로 SQL 실행. commit · rollback · 반환을 하지 않는다
연결 모듈      (이 파일) 풀 준비 · 연결 대여 · 반환 · 정리
```

🔴 **ORM 을 쓰지 않는다.** SQL 문자열 + 매개변수 바인딩을 그대로 psycopg 에 넘긴다.
"""

import logging
import threading
import weakref
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager, suppress
from typing import Any

import psycopg
from psycopg import pq, sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.core.settings import (
    DatabaseSettings,
    MissingDatabaseEnvironment,
    PoolSettings,
    database_settings,
    pool_settings,
)

logger = logging.getLogger(__name__)

#: 커서에 넘기는 SQL 과 매개변수의 모양.
Query = str | sql.Composed
Params = Sequence[object] | Mapping[str, object] | None

Connection = psycopg.Connection[dict[str, Any]]
#: 연결을 빌려 주는 함수. `with borrow() as conn:` 블록이 끝나면 돌려준다.
Borrow = Callable[[], AbstractContextManager[Connection]]

#: 접속이 안 되면 이만큼 기다리고 포기한다(초). 부서 다섯 곳이 따로 적던 값을 한 자리로 옮겼다.
#: 풀이 새 연결을 만들 때마다 이 값을 넘긴다.
#:
#: - 없으면 libpq 기본이 0(무제한)이라, 응답 없는 호스트에서 연결이 끝나지 않는다
#:   (매입 `#81` · ML `#358` 실측).
#: - 프론트 읽기 타임아웃(20초)보다 짧아야 백엔드가 먼저 포기하고 자기 사유를 화면에 싣는다
#:   (`tests/test_purchase_agent/test_auction_quotes.py` 가 `0 < 값 < 15` 로 잠근다).
#: - 재시도 로직이 없으므로 너무 짧으면 일시 실패가 그대로 실패가 된다.
#: - 부서마다 같은 값이어야 어느 쪽이 먼저 끊겼는지 화면만 보고 가릴 수 있다 — 이제 한 값이다.
CONNECT_TIMEOUT_SECONDS = 5

_IN_TRANSACTION = (pq.TransactionStatus.INTRANS, pq.TransactionStatus.INERROR)

#: 만들어진 풀 전부. `close_pools()` 가 여기를 돌며 닫는다.
_POOLS: "weakref.WeakSet[DatabasePool]" = weakref.WeakSet()


# ── 준비 · 정리 ──────────────────────────────────────────────────────────────


class DatabasePool:
    """접속 대상 **하나**의 연결 풀. 접속 정보가 다르면 풀도 따로 만든다.

    ```text
    서비스 DB   SERVICE_POOL (이 모듈)          DB_*            부서 다섯 · 마스터가 함께 쓴다
    ML 원본 DB  app/ml/db.py::SOURCE_POOL       ML_SOURCE_DB_*  원자료 · 학습 테이블
    ```

    ★ **열기는 한 번이다.** 첫 대여(또는 `open()`)가 psycopg_pool 풀을 만들고, 그 뒤 대여는
      전부 그 풀을 쓴다. 요청 · 쿼리마다 풀을 새로 만들지 않는다. `close()` 뒤에 다시 빌리면
      새 풀을 연다 — psycopg_pool 은 닫힌 풀을 다시 열지 못한다(3.3.3 소스).

    ★ 접속 정보(`settings`)와 크기(`pool_settings`)는 **열 때** 읽는다. 설정이 없으면
      `MissingDatabaseEnvironment`(종전과 같은 문구)를 빌리려던 자리에서 올린다.

    :param name: 풀 이름(로그에 나온다).
    :param settings: 접속 정보를 돌려주는 함수. 열 때 한 번 부른다.
    :param connection_class: 연결 종류. 기본은 psycopg `Connection` — 검사가 가짜를 넣는 자리다.
    """

    def __init__(
        self,
        name: str,
        settings: Callable[[], DatabaseSettings],
        *,
        connection_class: type[Connection] = psycopg.Connection,
    ) -> None:
        self.name = name
        self._settings = settings
        self._connection_class = connection_class
        self._pool: ConnectionPool[Connection] | None = None
        self._lock = threading.Lock()
        _POOLS.add(self)

    @property
    def is_open(self) -> bool:
        return self._pool is not None

    def open(self) -> None:
        """풀을 연다. 이미 열려 있으면 그대로 둔다.

        연결은 풀의 작업 스레드가 뒤에서 `min_size` 만큼 채운다 — 여기서 기다리지 않는다.
        """
        self._opened()

    def close(self) -> None:
        """풀을 닫는다. 쉬던 연결은 바로 닫고, 빌려 간 연결은 돌아오는 대로 닫는다."""
        with self._lock:
            pool, self._pool = self._pool, None
        if pool is not None:
            pool.close()

    def _opened(self) -> ConnectionPool[Connection]:
        with self._lock:
            if self._pool is None:
                self._pool = _new_pool(
                    self.name, self._settings(), pool_settings(), self._connection_class
                )
            return self._pool

    @contextmanager
    def connection(self) -> Iterator[Connection]:
        """연결 하나를 빌려 주고, 블록이 끝나면(정상 · 예외) 돌려준다. **commit 하지 않는다.**

        끝나지 않은 트랜잭션은 돌려주기 전에 rollback 한다. 빌릴 연결이 `timeout_seconds`
        안에 안 나오면 `psycopg_pool.PoolTimeout`(`psycopg.OperationalError` 의 하위 종류).
        """
        pool = self._opened()
        conn = pool.getconn()
        try:
            yield conn
        finally:
            _settle_before_return(conn)
            pool.putconn(conn)

    @contextmanager
    def read_connection(self) -> Iterator[Connection]:
        """조회 전용 대여. 문장마다 바로 끝나는(autocommit) 연결을 빌려 주고 돌려준다.

        🔴 **SELECT 에만 쓴다.** 쓰기 문장을 보내면 그 문장이 곧바로 확정된다 — 쓰기는
           `connection()` + `transaction(conn)` 으로 경계를 눈에 보이게 연다.
        """
        with self.connection() as conn:
            conn.autocommit = True
            yield conn


def _new_pool(
    name: str,
    settings: DatabaseSettings,
    sizes: PoolSettings,
    connection_class: type[Connection],
) -> ConnectionPool[Connection]:
    pool: ConnectionPool[Connection] = ConnectionPool(
        connection_class=connection_class,
        kwargs={
            "host": settings.host,
            "port": settings.port,
            "dbname": settings.name,
            "user": settings.user,
            "password": settings.password,
            "row_factory": dict_row,
            "connect_timeout": CONNECT_TIMEOUT_SECONDS,
        },
        min_size=sizes.min_size,
        max_size=sizes.max_size,
        timeout=sizes.timeout_seconds,
        check=ConnectionPool.check_connection,
        name=f"haesdeul-{name}",
        open=False,
    )
    pool.open(wait=False)
    return pool


def _settle_before_return(conn: Connection) -> None:
    """돌려주기 전에 연결을 «트랜잭션 없음 · autocommit 꺼짐» 으로 되돌린다. commit 하지 않는다.

    되돌리지 못하면(끊긴 연결) 닫는다 — 풀이 받아서 버리고 새로 만든다. 여기서 난 오류는
    올리지 않는다. 블록 안에서 난 진짜 오류를 덮지 않기 위해서다.
    """
    if conn.closed:
        return
    try:
        if conn.pgconn.transaction_status in _IN_TRANSACTION:
            conn.rollback()
        if conn.autocommit:
            conn.autocommit = False
    except Exception:
        logger.warning("풀에 돌려줄 연결을 정리하지 못해 닫는다: %s", conn, exc_info=True)
        with suppress(Exception):
            conn.close()


#: 서비스 DB(`DB_*`) 풀. 부서 다섯과 마스터가 같은 접속 대상 · 같은 계정이라 하나를 나눠 쓴다.
SERVICE_POOL = DatabasePool("service", database_settings)


def close_pools() -> None:
    """만들어진 풀을 모두 닫는다(서비스 · ML 원본). 앱 · CLI 종료 때 부른다."""
    for pool in list(_POOLS):
        pool.close()


@contextmanager
def pool_lifespan() -> Iterator[None]:
    """앱 · CLI **한 번의 실행**을 감싼다. 시작 때 서비스 풀을 열고, 끝날 때 모든 풀을 닫는다.

    ```python
    @asynccontextmanager
    async def lifespan(_app):          # app/main.py
        with pool_lifespan():
            yield

    def main(argv):                    # CLI
        args = parser.parse_args(argv) # --help 는 풀을 건드리지 않는다
        with pool_lifespan():
            ...
    ```

    ★ **DB 설정이 아예 없으면 미리 열지 않고 넘어간다** (`MissingDatabaseEnvironment`). DB 없이
      도는 검사와 개발 PC 에서도 앱이 떠야 하고, 그 자리의 DB 경로는 첫 대여에서 종전 문구로
      실패한다. 그 밖의 오류(풀 크기 설정이 틀림 등)는 시작을 막는다.

    ★ ML 원본 풀은 여기서 열지 않는다 — ML 예측 조회가 처음 읽을 때 연다. 닫는 것은 여기서 한다.
    """
    try:
        SERVICE_POOL.open()
    except MissingDatabaseEnvironment as exc:
        logger.warning("DB 설정이 없어 서비스 풀을 미리 열지 않았다(첫 대여에서 연다): %s", exc)
    try:
        yield
    finally:
        close_pools()


# ── 빌려 준다 ────────────────────────────────────────────────────────────────


def connection() -> AbstractContextManager[Connection]:
    """서비스 DB 연결을 빌린다. **commit 은 빌린 쪽이 한다** (`DatabasePool.connection`)."""
    return SERVICE_POOL.connection()


def read_connection() -> AbstractContextManager[Connection]:
    """서비스 DB 조회 전용 연결을 빌린다 (`DatabasePool.read_connection`)."""
    return SERVICE_POOL.read_connection()


@contextmanager
def transaction(conn: Connection) -> Iterator[Connection]:
    """빌린 연결에서 **블록 하나를 트랜잭션 하나로** 끝낸다. 연결은 돌려주지 않는다.

    ```python
    with connection() as conn, transaction(conn):
        repository.insert_something(conn, ...)     # SQL 은 repository 가 실행한다
    ```

    psycopg 3 `Connection.__exit__` 의 트랜잭션 처리와 같다 (3.3.4 소스 확인): 예외 없이 끝나면
    `commit`, 예외면 `rollback`(실패는 경고만 남기고 원래 예외를 올린다). 종전 부서 라우터의
    `with get_connection() as conn:` 과 같은 경계이고, 다른 점은 닫지 않는다는 것 하나다 —
    돌려주는 것은 빌린 블록(`connection()`)이다.

    ⚠️ 마스터 경계 함수(`day_open` · `transition` 등)는 명시적 `commit()` · `rollback()` 을 쓴다
      (그 파일 머리말 — 커밋이 문법에 숨지 않게). 그 자리를 이 함수로 바꾸지 않는다.
    """
    try:
        yield conn
    except BaseException:
        try:
            conn.rollback()
        except Exception as exc:  # noqa: BLE001 - 원래 예외를 덮지 않는다
            logger.warning("error ignored in rollback on %s: %s", conn, exc)
        raise
    conn.commit()


def db_connection() -> Iterator[Connection]:
    """HTTP 입구가 FastAPI `Depends` 로 받는 연결 — 요청 처리 동안 `connection()` 하나.

    ```python
    DbConnection = Annotated[Connection, Depends(db_connection, scope="function")]

    @router.post("/…")
    def handler(body: Body, conn: DbConnection) -> …:
        with transaction(conn):
            service.do(conn, …)
    ```

    ★ `scope="function"` 으로 쓴다 — 응답을 보내기 **전에** 연결을 돌려준다.
    ★ **commit 하지 않는다.** 요청 전체를 자동 commit 블록 하나로 감싸지 않는다 — 트랜잭션은
      핸들러(목표는 service)가 `transaction(conn)` 으로 연다.
    ★ `Depends` 는 FastAPI 가 부르는 함수에서만 동작한다. service 이하 · CLI · 마스터는 이 함수를
      쓰지 않고 `connection()` 으로 빌려 연결을 인자로 넘긴다.
    ⚠️ LLM · 외부 HTTP 를 기다리는 라우트에는 걸지 않는다 — 기다리는 동안 연결을 쥐게 된다.
    """
    with connection() as conn:
        yield conn
