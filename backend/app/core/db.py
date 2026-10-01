"""PostgreSQL 연결 모듈 — 연결 풀을 **준비하고, 연결을 빌려 주고, 돌려받고, 정리한다.**

```text
준비      DatabasePool(name, settings)   접속 대상 하나에 풀 하나. 첫 대여(또는 open)에서 열린다
          SERVICE_POOL                   서비스 DB(DB_*) 풀. 부서 다섯과 마스터가 함께 쓴다
          ML_SOURCE_POOL                 ML 원본 창고(ML_SOURCE_DB_*) 풀. ML · 화면 예측 탭이 읽는다
빌려 준다 connection()                   블록 동안 빌려 주고 끝나면 돌려준다. commit 없음
          transaction(conn)              블록 하나 = 트랜잭션 하나. 정상 commit · 예외 rollback
          read_connection()              조회 전용 대여. 문장마다 바로 끝난다(autocommit)
          db_connection()                HTTP 입구의 Depends 용 — 요청 처리 동안 connection() 하나
정리      pool_lifespan()                앱 lifespan · CLI main 이 감싼다. 시작 때 서비스 풀을 열고
                                         끝날 때(정상 · 예외) 모든 풀을 닫는다
          close_pools()
가른다    is_unavailable(error)          DB 에 **닿지 못한** 실패인가(드라이버 예외 분류)
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

★ **빌려 줄 때 연결이 살아 있는지 확인한다** (`check_connection_within` · 빈 질의 한 왕복).
  종전에는 매번 새 연결이라 죽은 연결을 받을 일이 없었다 — DB 가 재시작되거나 쉬던 연결이
  끊겨도 요청이 그 연결로 실패하지 않게 풀이 바꿔 준다. 확인에 실패하면 쉬던 나머지 연결도 함께
  확인한다(`_new_pool` 의 `check` · 2026-10-01 실 DB 검증에서 고침).

★ **그 확인에는 시간 제한이 있다** (`DB_POOL_CHECK_TIMEOUT_SECONDS` · 기본 5초 · 2026-10-01
  사용자 결정). DB 프로세스가 멈추면(연결은 살아 있음) psycopg_pool 의 확인은 답을 끝없이
  기다렸다. 제한을 넘긴 연결은 닫아 버리고, 대여는 다음 연결을 찾거나 대기 시간이 지났으면
  `PoolTimeout` 으로 끝난다. **이 제한은 빌려 주기 전 확인에만 걸린다** — 빌린 연결로 실행하는
  SQL 에는 클라이언트 쪽 시간 제한이 없다(재구성 전과 같음).

★ **DB 에 못 닿을 때 풀의 재접속 시도는 15초 단위로 접는다** (`DB_POOL_RECONNECT_TIMEOUT_SECONDS`
  · psycopg_pool `reconnect_timeout` · 라이브러리 기본 300초에서 바꿈). 연결에는 TCP keepalive ·
  `tcp_user_timeout` 을 준다(`DB_TCP_*` · 망 단절을 OS 가 알아채는 시간 · Linux 에서 모두 효과).
  값과 단위는 `app/core/settings.py::ConnectionHealthSettings`.

★ **이 모듈은 SQL 을 실행하지 않는다.** SQL 은 연결을 받은 쪽(부서 repository 함수)이
  `conn.cursor()` 로 직접 실행한다 (`tests/core/test_core_db.py` 가 잠근다).

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
import selectors
import threading
import time
import weakref
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager, suppress
from typing import Any

import psycopg
from psycopg import pq, sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.core.settings import (
    ConnectionHealthSettings,
    DatabaseSettings,
    MissingDatabaseEnvironment,
    PoolSettings,
    connection_health_settings,
    database_settings,
    ml_source_database_settings,
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
#:
#: 아래는 옛 `app/ml/db.py` 가 이 값의 별칭 위에 적어 두었던 ML 쪽 기록이다 (2026-09-29 재구성
#: BL-017 에 그 파일을 없애며 옮겼다).
#:
#:   접속이 안 되면 이만큼 기다리고 포기한다 (초). 매입 파트 #81 · #358.
#:
#:     libpq 기본은 0 = **무제한**이다. DB 가 "안 됩니다" 라고 거절하면 즉시
#:     오류가 나지만, **아무 답도 안 하면** 계속 기다린다. 방화벽이 패킷을
#:     조용히 버리는 경우가 그렇다.
#:
#:     실측 (2026-09-07 · 응답 없는 주소로 접속)
#:         connect_timeout=3   3.1초 만에 ConnectionTimeout
#:         없음                60초가 지나도 안 끝남
#:
#:     그리고 프론트에도 타임아웃이 없어(`api.ts` 에 AbortController 0건)
#:     **끊는 쪽이 아무도 없다.** 화면은 오류도 없이 멈춘 채로 남는다.
#:
#:     5초는 매입이 `purchase_agent/db.py` 에 넣은 값과 맞춘 것이다 (#305).
#:     파트마다 다르면 어느 쪽이 먼저 끊겼는지 화면만 보고 알 수 없다.
#:
#: 아래는 옛 `app/purchase_agent/db.py` 가 이 값의 별칭 위에 적어 두었던 매입 쪽 기록이다
#: (2026-09-29 재구성 BL-016 에 그 파일을 없애며 옮겼다).
#:
#:   접속 시도를 포기하는 시각(초). 공통 풀이 새 연결을 만들 때 ``connect_timeout`` 으로 넘긴다.
#:
#:   ⚠️ **없으면 libpq 기본이 0(무제한)** 이라 TCP connect 가 커널 재시도 정책까지
#:     매달린다. 접속이 **거부**되는 경우와 **무응답**인 경우가 다르게 동작한다
#:     (``#81`` 실측 2026-08-28)::
#:
#:         접속 거부     16ms 에 E4_NOT_STARTED 로 정상 종료
#:         접속 무응답   180초 클라이언트 타임아웃까지 CONNECT 3회 · SQL 0건
#:
#:   🔴 ~~**화면이 멈춘다**~~ — **낡았다** (2026-09-11 실측 · ``origin/dev@1714aff``).
#:
#:     ~~전에 *"프론트가 15초에 끊고 폴백한다"* 고 적었는데 **틀렸다.** 실측하면
#:     ``frontend/src/lib/api.ts`` 에 ``AbortController`` · ``setTimeout`` ·
#:     ``signal`` 이 **0건**이고, ``fetch`` 는 기본 타임아웃이 없다.~~
#:
#:     🟢 **``#580`` 이 그것을 고쳤다** (2026-09-11 머지). 지금 프론트는 끊는다::
#:
#:         lib/api.ts      읽기 20초 · 실행 900초
#:         lib/screen.ts   읽기 20초
#:         lib/mlConsole.ts  🔴 아직 0건 — ML 소관 (``#587``)
#:
#:     ★ 이 절이 두 번 틀렸다 — 한 번은 *"막는다"* 로, 한 번은 *"안 막는다"* 로.
#:       프론트는 **우리 소유가 아니라서** 옮겨 적은 문장이 그쪽 판마다 낡는다.
#:       그래서 이제 **수를 안 옮겨 적고 누가 주인인지만 적는다.**
#:
#:   ⚠️ ~~**그래서 이 상수가 유일한 방어다.** 그리고 남의 ``db.py`` 다섯 곳에는 아직
#:     없다 (`#81` — ``sales`` · ``ml`` 둘 · ``finance`` · ``logistics``).~~
#:     🔴 **둘 다 낡았다** (2026-09-11). 다섯 곳에 **다 들어갔고**, 블랙홀 호스트
#:     (``10.255.255.1``)로 재면 전부 5초대에 ``ConnectionTimeout`` 이다::
#:
#:         purchase 5.06s · finance 5.05s · sales 5.05s · ml 5.05s · logistics 5.05s
#:
#:     ★ ``app/master/`` 는 자기 ``db.py`` 가 없고 ``app.finance.db.get_connection``
#:       을 빌려 쓴다 — 그래서 같이 덮인다. 🟢 ``#81`` 은 이것으로 닫혔다.
#:     ⚠️ 물리는 것이 **백엔드 워커**라는 것은 그대로다 — 요청이 쌓이면 고갈된다.
#:
#:   🔴 **이 사실은 검사가 안 지킨다.** 프론트에 타임아웃이 생기거나 없어져도 우리
#:     스위트는 아무 말도 안 한다 — 백엔드 검사가 ``frontend/`` 를 읽는 선례가
#:     저장소에 **0건**이고, 여기서 만들지 않았다. 프론트는 우리 소유가 아니라
#:     경계를 넘는 검사가 되고, 그 검사는 프론트 사정으로 깨질 때 **우리 CI 를
#:     빨갛게 만든다.** 대신 이 줄이 사실이고, 틀리면 사람이 고친다.
#:     ★★ **그리고 실제로 틀렸다** — 위 ``#580`` 이 그 증거다. 이 방식의 대가다.
#:
#:   **왜 5초인가**
#:
#:   - LAN(``192.168.0.38``)이고 정상 접속은 밀리초 단위다
#:   - ~~프론트 15초의 1/3 — 백엔드가 먼저 정리돼야 워커가 안 물린다~~
#:     ~~🔴 **이 근거는 위 정정으로 무너졌다** (2026-09-07). 프론트가 안 끊으므로
#:     비교 대상이 없다.~~
#:     🟢 **근거가 돌아왔다** (2026-09-11 · ``#580``). 프론트 읽기가 20초이므로
#:     **백엔드가 먼저 포기한다**(5 < 20)가 다시 참이다. 그 순서라야 프론트가
#:     *"서버가 느립니다"* 대신 백엔드가 낸 사유를 화면에 싣는다.
#:     ⚠️ **값은 그대로 5초로 둔다** — 근거가 돌아왔다고 값을 움직일 이유는 없고,
#:     나머지 근거 둘(LAN 접속은 밀리초 · 재시도 로직이 없어 너무 짧으면 0안)도
#:     그대로다
#:   - **재시도 로직이 우리 코드에 없다** (``quotes.py`` 에 retry 0건). 너무 짧으면
#:     일시 실패에 그대로 0안이 된다 — ``#81`` 본문이 *"``No route to host`` 로 한 번
#:     실패한 적이 있고 재시도에서 붙었다"* 를 적어 두었다
#:
#:   🟢 ``#81`` 은 **ⓐ(각자 자기 ``db.py``)로 정해졌다** (2026-09-07 · 재무·마스터
#:     합의). 공통 헬퍼(ⓑ)는 **발표 뒤**로 미뤘다 — 그때 이 인자가 헬퍼로 옮겨가고,
#:     이 줄은 지워도 되며 **값은 따라간다.**
#:   🟢 **ⓑ 로 옮겼다** (2026-09-28 · 재구성 BL-010). 값의 자리는
#:     ``app/core/db.py::CONNECT_TIMEOUT_SECONDS`` 이고, 이 이름은 그 값을 가리킨다.
CONNECT_TIMEOUT_SECONDS = 5

_IN_TRANSACTION = (pq.TransactionStatus.INTRANS, pq.TransactionStatus.INERROR)

#: 만들어진 풀 전부. `close_pools()` 가 여기를 돌며 닫는다.
_POOLS: "weakref.WeakSet[DatabasePool]" = weakref.WeakSet()


# ── 준비 · 정리 ──────────────────────────────────────────────────────────────


class DatabasePool:
    """접속 대상 **하나**의 연결 풀. 접속 정보가 다르면 풀도 따로 만든다.

    ```text
    서비스 DB   SERVICE_POOL (이 모듈)          DB_*            부서 다섯 · 마스터가 함께 쓴다
    ML 원본 DB  ML_SOURCE_POOL (이 모듈)        ML_SOURCE_DB_*  원자료 · 학습 테이블
    ```

    ★ **열기는 한 번이다.** 첫 대여(또는 `open()`)가 psycopg_pool 풀을 만들고, 그 뒤 대여는
      전부 그 풀을 쓴다. 요청 · 쿼리마다 풀을 새로 만들지 않는다. `close()` 뒤에 다시 빌리면
      새 풀을 연다 — psycopg_pool 은 닫힌 풀을 다시 열지 못한다(3.3.3 소스).

    ★ 접속 정보(`settings`) · 크기(`pool_settings`) · 연결 상태 확인(`connection_health_settings`)은
      **열 때** 읽는다. 설정이 없으면
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
                    self.name,
                    self._settings(),
                    pool_settings(),
                    connection_health_settings(),
                    self._connection_class,
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


class ConnectionCheckTimeout(psycopg.OperationalError):
    """빌려 주기 전 연결 확인이 제한 시간 안에 끝나지 않았다. 그 연결은 닫았다.

    풀 안에서만 쓰인다 — 대여자에게는 풀이 다음 연결을 주거나 `PoolTimeout` 을 올린다.
    """


class _DeadlinePassed(Exception):
    """`_ping` 이 기다릴 시간을 다 썼다(이 모듈 안에서만 쓴다)."""


#: 쉬던 연결을 한꺼번에 다시 확인하는 동안(`_discard_broken_idle_connections`) 그 확인 전체가
#: 넘지 않을 시각. 그 확인은 부른 스레드에서 차례로 돌므로 스레드마다 따로 둔다.
_sweep = threading.local()


def check_connection_within(conn: Connection, timeout_seconds: float) -> None:
    """빈 질의 한 왕복으로 연결이 살아 있는지 확인한다. **`timeout_seconds` 안에 끝낸다.**

    psycopg_pool `ConnectionPool.check_connection` 과 같은 확인(빈 질의 — 트랜잭션을 열지 않는다)을
    libpq 비동기 API 로 보내고, 답을 기다리는 동안 소켓을 남은 시간만큼만 기다린다. 다른 스레드나
    뒤에서 계속 도는 작업을 만들지 않는다 — 기다림이 이 함수 안에서 끝난다.

    ```text
    답이 옴(빈 결과)             통과 — 연결은 그대로 쓸 수 있다
    제한 시간을 넘김             연결을 닫고 ConnectionCheckTimeout
    오류(끊김 · 서버 오류)       연결을 닫고 그 오류를 올린다
    ```

    🔴 **닫힌 연결은 풀이 버리고 새로 만든다**(풀은 돌려받은 연결의 상태가 «알 수 없음» 이면 버린다
       · psycopg_pool 3.3.3 `_return_connection`). 확인에 실패한 연결을 다시 빌려 주지 않는다.

    🔴 **빌려 주기 전 확인에만 쓴다.** SQL 실행의 시간 제한이 아니다.
    """
    pgconn = conn.pgconn
    if conn.closed or pgconn.status != pq.ConnStatus.OK:
        raise psycopg.OperationalError("the connection is closed")
    deadline = time.monotonic() + timeout_seconds
    sweep_deadline = getattr(_sweep, "deadline", None)
    if sweep_deadline is not None:
        deadline = min(deadline, sweep_deadline)
    try:
        with conn.lock:
            _ping(pgconn, deadline)
    except _DeadlinePassed:
        _close_quietly(conn)
        raise ConnectionCheckTimeout(
            f"connection check did not finish within {timeout_seconds:g} sec"
        ) from None
    except Exception:
        _close_quietly(conn)
        raise


def _ping(pgconn: Any, deadline: float) -> None:
    """빈 질의를 보내고 결과를 모두 읽는다. `deadline` 을 넘기면 `_DeadlinePassed`."""
    pgconn.send_query(b"")
    while pgconn.flush():  # 1: 아직 보낼 것이 남았다(psycopg 연결은 nonblocking 이다)
        _wait_socket(pgconn, selectors.EVENT_WRITE, deadline)
    while True:
        pgconn.consume_input()
        if pgconn.is_busy():
            _wait_socket(pgconn, selectors.EVENT_READ, deadline)
            continue
        result = pgconn.get_result()
        if result is None:
            return
        if result.status == pq.ExecStatus.FATAL_ERROR:
            message = (result.error_message or b"").decode("utf-8", "replace").strip()
            raise psycopg.OperationalError(f"connection check failed: {message}")


def _wait_socket(pgconn: Any, event: int, deadline: float) -> None:
    """소켓이 `event` 할 수 있게 될 때까지, 남은 시간만큼만 기다린다."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _DeadlinePassed
    with selectors.DefaultSelector() as selector:
        selector.register(pgconn.socket, event)
        selector.select(remaining)


def _close_quietly(conn: Connection) -> None:
    with suppress(Exception):
        conn.close()


class HealthCheckedPool(ConnectionPool[Connection]):
    """psycopg_pool 풀 — 쉬던 연결을 한꺼번에 확인할 때(`check()`)도 시간 제한 있는 확인을 쓴다.

    psycopg_pool 의 `check()` 는 정적 메서드 `check_connection` 으로 확인하는데, 그 확인에는 시간
    제한이 없다(3.3.3 소스). 이 자리만 `check_connection_within` 으로 바꾼다. 대여 · 반환 · 재접속은
    그대로다.
    """

    def __init__(self, *args: Any, check_timeout_seconds: float, **kwargs: Any) -> None:
        self.check_timeout_seconds = check_timeout_seconds
        super().__init__(*args, **kwargs)

    def check_connection(self, conn: Connection) -> None:  # type: ignore[override]
        check_connection_within(conn, self.check_timeout_seconds)


def _new_pool(
    name: str,
    settings: DatabaseSettings,
    sizes: PoolSettings,
    health: ConnectionHealthSettings,
    connection_class: type[Connection],
) -> ConnectionPool[Connection]:
    opened: list[ConnectionPool[Connection]] = []

    def check(conn: Connection) -> None:
        """빌려 주기 전 확인(`check_connection_within`). 끊긴 연결이면 쉬던 나머지 연결도 확인한다.

        🔴 2026-10-01 재구성 BL-010 실 DB 검증에서 고쳤다. psycopg_pool 은 확인에 실패한
           연결 하나만 버리고 다음 쉬던 연결을 꺼내는데, 두 번째 실패부터 1 · 2 · 4초를 쉰다
           (3.3.3 `AttemptWithBackoff`). DB 재시작처럼 쉬던 연결이 한꺼번에 끊기면, 살아 있는
           DB 앞에서도 대여가 대기 시간(5초)을 넘겼다(끊긴 연결 5개 → 첫 대여 실패, 10개 →
           두 번 실패). 하나가 끊겼으면 나머지도 같은 이유로 끊겼을 수 있으니, 풀 공개 API
           `check()` 로 쉬던 연결을 모두 확인해 끊긴 것을 버리고 새로 채운다. 살아 있는 연결은
           그대로 둔다.

        🔴 **확인이 시간 제한에 걸린 경우에는 나머지를 확인하지 않는다** (같은 날 사용자 결정 뒤).
           DB 가 멈춘 것이라 나머지도 제한 시간까지 기다릴 뿐이고, 한 대여가 제한 시간을 연결
           수만큼 이어 기다리게 된다. 남은 연결은 각자 빌려 줄 때 같은 제한으로 확인된다. 끊김으로
           시작한 재확인도 전체가 확인 제한 한 번을 넘지 않는다(`_discard_broken_idle_connections`).
        """
        try:
            check_connection_within(conn, health.check_timeout_seconds)
        except ConnectionCheckTimeout:
            raise
        except Exception:
            if opened:
                _discard_broken_idle_connections(opened[0], health.check_timeout_seconds)
            raise

    pool = HealthCheckedPool(
        connection_class=connection_class,
        kwargs={
            "host": settings.host,
            "port": settings.port,
            "dbname": settings.name,
            "user": settings.user,
            "password": settings.password,
            "row_factory": dict_row,
            "connect_timeout": CONNECT_TIMEOUT_SECONDS,
            **_tcp_health_arguments(health),
        },
        min_size=sizes.min_size,
        max_size=sizes.max_size,
        timeout=sizes.timeout_seconds,
        reconnect_timeout=health.reconnect_timeout_seconds,
        check=check,
        check_timeout_seconds=health.check_timeout_seconds,
        name=f"haesdeul-{name}",
        open=False,
    )
    opened.append(pool)
    pool.open(wait=False)
    return pool


def _tcp_health_arguments(health: ConnectionHealthSettings) -> dict[str, int]:
    """libpq 연결 인자 — TCP keepalive · `tcp_user_timeout`(밀리초). 서비스 · ML 원본 풀이 같다.

    ⚠️ libpq 가 지원하지 않는 OS 에서는 효과 없이 받아들인다: `keepalives_count` 는 Windows 에서,
      `tcp_user_timeout` 은 Linux 밖에서 효과가 없다(libpq 문서). 운영 백엔드는 Linux 컨테이너다.
    """
    if not health.keepalives:
        return {"keepalives": 0, "tcp_user_timeout": health.tcp_user_timeout_ms}
    return {
        "keepalives": 1,
        "keepalives_idle": health.keepalives_idle_seconds,
        "keepalives_interval": health.keepalives_interval_seconds,
        "keepalives_count": health.keepalives_count,
        "tcp_user_timeout": health.tcp_user_timeout_ms,
    }


def _discard_broken_idle_connections(
    pool: ConnectionPool[Connection], limit_seconds: float
) -> None:
    """쉬던 연결을 모두 확인해 끊긴 것을 버리고 새로 채운다(psycopg_pool `check()`).

    확인 전체가 `limit_seconds` 를 넘지 않는다 — 넘긴 뒤의 연결은 확인 없이 시간 초과로 닫혀
    버려지고 풀이 새로 채운다. 여기서 난 오류는 올리지 않는다. 대여를 막는 것은 확인에 실패한
    원래 오류다.
    """
    _sweep.deadline = time.monotonic() + limit_seconds
    try:
        pool.check()
    except Exception:
        logger.warning("쉬던 연결을 다시 확인하지 못했다: %s", pool.name, exc_info=True)
    finally:
        _sweep.deadline = None


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

#: ML 원본 창고(`ML_SOURCE_DB_*`) 풀. 접속 대상이 서비스 DB 와 달라 따로 둔다. 접속 정보는
#: 처음 열 때 읽는다(`app/core/settings.py::ml_source_database_settings`).
#:
#: 🟢 **자리 (2026-09-29 · 재구성 BL-017).** 전에는 `app/ml/db.py::SOURCE_POOL` 이었다. ML 의
#:    SQL 이 `app/ml/repository/` 로 옮겨 가 그 파일에 남은 일이 풀 하나뿐이 되어, 풀을 준비 ·
#:    정리하는 이 모듈로 옮겼다. 풀 이름 · 접속 정보 · 여는 시점(첫 대여)은 그대로다.
#:
#: 아래는 옛 `app/ml/db.py` 머리말의 «왜 연결이 두 개인가» 를 그대로 옮긴 것이다.
#:
#:   ML 파이프라인은 **창고를 두 개** 쓴다.
#:
#:       원본 창고 (SOURCE)  경락가·중도매가·소매가 원자료와 학습 테이블이 있는 곳
#:       서비스 창고 (기본)   다른 Agent 와 같은 곳. 예측 결과를 여기에 넣는다
#:
#:   두 창고는 **같은 서버의 다른 데이터베이스**다. 원본 창고에는 원자료가
#:   수백만 행 쌓여 있어 서비스 창고와 섞지 않는다.
#:
#:   ★ **창고가 둘이라 풀도 둘이다.** 서비스 창고는 다른 부서와 같은 `SERVICE_POOL` 을
#:     쓰고, 원본 창고는 이 풀을 쓴다. 섞으면 서비스 질의가 원본 창고로 가서 «표가
#:     없다» 가 된다. 원본 풀은 처음 원본을 읽을 때 열리고, 앱 · CLI 가 끝날 때 함께 닫힌다.
ML_SOURCE_POOL = DatabasePool("ml-source", ml_source_database_settings)


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


# ── 가른다 ────────────────────────────────────────────────────────────────


#: DB 에 **닿지 못한** 실패. 값이 틀린 것이 아니라 지금 못 읽는 상태다.
#:
#: 🔴 `psycopg.OperationalError` 하나만 여기 둔다(풀 대기 시간 초과 `PoolTimeout` 도 그 하위
#:    종류다). 그 밑에 `ProgrammingError`(SQL 잘못) · `IntegrityError` 는 **우리 코드가 깨진
#:    것**이라 «다시 오면 될 수 있다» 로 읽으면 안 된다.
_UNAVAILABLE: tuple[type[BaseException], ...] = (psycopg.OperationalError,)


def is_unavailable(error: BaseException) -> bool:
    """이 예외가 **DB 에 닿지 못한** 실패인가 — 드라이버 예외를 아는 곳은 이 모듈이다.

    ★ 2026-09-30 재구성 BL-019: 화면 물류 탭(`app/api/logistics/presenter.py`)이 503/500 을
      가르려고 들고 있던 `psycopg.OperationalError` 분류를 옮겼다(대상 그대로). 화면은
      psycopg 를 들이지 않고 이 함수로 묻는다.
    """
    return isinstance(error, _UNAVAILABLE)
