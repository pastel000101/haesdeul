"""프로세스 설정 — `.env` 위치, DB 접속 정보(서비스 DB · ML 원본 창고), 연결 풀 크기,
SQL 이 쓸 스키마 이름, 화면이 보는 실행과 기준일(발표용 고정값 · 맨 아래).

부서들이 함께 쓰는 접속 설정을 한 자리에 둔다.

```text
.env 위치      backend/.env
읽는 시점      호출할 때마다 load_dotenv — 기본값. 이미 있는 환경변수는 덮지 않는다
값             적재 뒤 os.getenv 로 매번 읽는다 → 검사가 환경변수를 바꾸면 그대로 따라간다
빠진 값        MissingDatabaseEnvironment("Missing required database environment variables: …")
               (RuntimeError 의 하위 종류)
```

읽는 시점은 부서가 고른다. 물류는 `.env` 를 프로세스에서 한 번만 읽는다
  (`load_env_file_once` · 물류 스키마 이름 `app/logistics/repository/rows.py` — 대시보드 한 요청에
  550회 불리던 비용). 그래서 적재 함수를 `load` 인자로 받는다 — 한 벌로 합치면 어느 한쪽 동작이
  바뀐다.

접속 정보는 풀을 열 때 한 번 읽는다. 연결마다 새로 읽지 않으므로
  실행 중에 `DB_*` 를 바꿔도 이미 열린 풀에는 반영되지 않는다. 스키마 이름(`DB_SCHEMA`)은
  SQL 을 만들 때마다 그대로 읽는다.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

#: `backend/.env`.
ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"

#: 서비스 DB 연결에 필요한 환경변수. `DB_SCHEMA` 는 연결이 아니라 SQL 에 쓰므로 따로 읽는다.
DB_CONNECTION_ENV_KEYS = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")

Load = Callable[[], None]


def load_env_file() -> None:
    """`backend/.env` 를 환경변수로 적재한다. 이미 있는 값은 덮지 않는다(`override=False`)."""
    load_dotenv(ENV_FILE)


#: `load_env_file_once` 가 이미 적재했나. 검사는 이 값을 `False` 로 되돌려 처음 상태를 만든다.
_env_file_loaded_once = False


def load_env_file_once() -> None:
    """`backend/.env` 를 프로세스에서 한 번만 적재한다 — 물류 스키마 이름이 쓴다.

    대시보드 한 요청에 물류 스키마 이름이 550번 읽히며 매번 파일을 다시 파싱해 7.8초를 쓴
    적이 있다. 값은 적재 뒤 `os.getenv` 로 매번 읽으므로 검사가 환경변수를 바꿔도 그대로
    따라간다.
    """
    global _env_file_loaded_once
    if _env_file_loaded_once:
        return
    load_dotenv(ENV_FILE)
    _env_file_loaded_once = True


class MissingDatabaseEnvironment(RuntimeError):
    """필수 DB 환경변수가 비었다. `RuntimeError` 의 하위 종류다.

    따로 이름을 둔 이유: 앱·CLI 가 시작할 때 풀을 미리 열다 설정이 아예 없는 자리
    (DB 없이 도는 검사 · DB 없는 개발 PC)만 골라 넘기기 위해서다 (`app/core/db.py::
    pool_lifespan`). 그 밖의 오류는 시작을 막는다.
    """


def required_database_environment(
    keys: tuple[str, ...], *, load: Load = load_env_file
) -> dict[str, str]:
    """`load` 로 `.env` 를 적재한 뒤 `keys` 를 읽는다. 하나라도 비었으면 멈춘다.

    :param load: 적재 방식. 기본은 호출마다 적재. 물류는 한 번만 적재하는 함수를 넘긴다.
    """
    load()
    values = {key: os.getenv(key, "") for key in keys}
    missing = [key for key, value in values.items() if not value]
    if missing:
        raise MissingDatabaseEnvironment(
            f"Missing required database environment variables: {', '.join(missing)}"
        )
    return values


@dataclass(frozen=True)
class DatabaseSettings:
    """PostgreSQL 에 접속할 곳과 계정. `app.core.db.DatabasePool` 이 풀을 열 때 이 값을 쓴다."""

    host: str
    port: str
    name: str
    user: str
    password: str


def database_settings(*, load: Load = load_env_file) -> DatabaseSettings:
    """서비스 DB 접속 정보(`DB_HOST`·`DB_PORT`·`DB_NAME`·`DB_USER`·`DB_PASSWORD`)."""
    values = required_database_environment(DB_CONNECTION_ENV_KEYS, load=load)
    return DatabaseSettings(
        host=values["DB_HOST"],
        port=values["DB_PORT"],
        name=values["DB_NAME"],
        user=values["DB_USER"],
        password=values["DB_PASSWORD"],
    )


@dataclass(frozen=True)
class PoolSettings:
    """연결 풀 하나의 크기와 대여 대기 시간. `app.core.db.DatabasePool` 이 풀을 열 때 읽는다."""

    #: 쉬는 동안에도 열어 두는 연결 수.
    min_size: int
    #: 한꺼번에 빌려 줄 수 있는 연결 수의 상한.
    max_size: int
    #: 빌릴 연결이 없을 때 기다리는 시간(초). 넘기면 `psycopg_pool.PoolTimeout`.
    timeout_seconds: float


#: 풀 크기·대기 시간을 바꾸는 환경변수. 비었으면 `DEFAULT_POOL_SETTINGS` 를 쓴다.
DB_POOL_ENV_KEYS = ("DB_POOL_MIN_SIZE", "DB_POOL_MAX_SIZE", "DB_POOL_TIMEOUT_SECONDS")

#: 기본값과 그 이유 (실 DB 로 잰 값이 아니라 실행 방식에서 고른 값이다).
#:
#: - `min_size=1` — 백엔드는 uvicorn 프로세스 하나로 돌고(`Dockerfile` CMD 에 workers 없음),
#:   CLI 는 하루 단계를 한 줄로 걷는다. 팀이 PostgreSQL 한 대를 같이 쓰므로 쉬는 동안
#:   붙잡아 두는 연결은 하나로 둔다. 나머지는 쓸 때 늘었다가 10분 쉬면 풀이 닫는다.
#: - `max_size=10` — 화면은 창 여러 개를 한꺼번에 부르고(동시 GET), 마스터 경계 함수는
#:   연결 하나를 쥔 채 이력 저장·조회로 하나를 더 빌린다(한 흐름에 2~3개). 상한이 그보다
#:   작으면 겹친 대여가 서로를 기다리다 시간 초과가 난다.
#: - `timeout_seconds=5` — 접속 타임아웃(`app/core/db.py::CONNECT_TIMEOUT_SECONDS`)과 같은 값.
#:   DB 가 답하지 않을 때 대여 한 번이 포기하는 시간이 새 연결 하나의 접속 제한과 같고,
#:   프론트 읽기 20초보다 짧아 백엔드가 먼저 사유를 낸다.
DEFAULT_POOL_SETTINGS = PoolSettings(min_size=1, max_size=10, timeout_seconds=5.0)


def pool_settings(*, load: Load = load_env_file) -> PoolSettings:
    """풀 크기·대기 시간(`DB_POOL_MIN_SIZE`·`DB_POOL_MAX_SIZE`·`DB_POOL_TIMEOUT_SECONDS`).

    빈 값은 기본값이다. 숫자가 아니거나 범위가 틀리면 `ValueError` — 풀을 열기 전에 멈춘다.
    """
    load()
    raw = {key: os.getenv(key, "").strip() for key in DB_POOL_ENV_KEYS}
    default = DEFAULT_POOL_SETTINGS
    try:
        min_size = int(raw["DB_POOL_MIN_SIZE"]) if raw["DB_POOL_MIN_SIZE"] else default.min_size
        max_size = int(raw["DB_POOL_MAX_SIZE"]) if raw["DB_POOL_MAX_SIZE"] else default.max_size
        timeout = (
            float(raw["DB_POOL_TIMEOUT_SECONDS"])
            if raw["DB_POOL_TIMEOUT_SECONDS"]
            else default.timeout_seconds
        )
    except ValueError as exc:
        raise ValueError(f"DB_POOL_* 값이 숫자가 아니다: {raw}") from exc
    if min_size < 0 or max_size < 1 or max_size < min_size or not timeout > 0:
        raise ValueError(
            "DB_POOL_* 범위가 틀렸다 — 0 <= MIN_SIZE <= MAX_SIZE, MAX_SIZE >= 1, "
            f"TIMEOUT_SECONDS > 0: min={min_size} max={max_size} timeout={timeout}"
        )
    return PoolSettings(min_size=min_size, max_size=max_size, timeout_seconds=timeout)


@dataclass(frozen=True)
class ConnectionHealthSettings:
    """끊긴 · 응답 없는 연결을 알아채는 시간. 서비스 풀과 ML 원본 풀이 같은 값을 쓴다.

    설정으로 두는 이유: 장애 검증에서 «DB 가 뜬 뒤에도 재접속이 늦다» 와 «응답 없는 DB 앞에서
    빌려 주기 전 확인이 끝나지 않는다» 가 관찰됐다(사용자 결정).

    ```text
    connect_timeout (core/db.py)   새 연결 하나를 만드는 시간 — 5초
    풀 timeout_seconds             빌릴 연결을 기다리는 시간 — 5초(PoolSettings)
    reconnect_timeout_seconds      DB 에 못 닿을 때 풀이 새 연결을 다시 시도하는 한 번의 기간
    check_timeout_seconds          빌려 주기 전 연결 확인(빈 질의 한 왕복)을 기다리는 시간
    keepalive · tcp_user_timeout   OS 가 망 단절을 알아채는 시간(아래)
    ```

    주의: 어느 것도 SQL · 요청의 시간 상한이 아니다. 빌린 연결로 실행하는 질의에는 클라이언트
    쪽 시간 제한이 없다. TCP 설정은 OS 가 패킷에 응답하는 한 — 예: DB 프로세스만 멈춘 경우 —
    아무것도 알아채지 못한다.
    """

    #: 풀이 새 연결을 다시 시도하는 한 번의 기간(초) — psycopg_pool `reconnect_timeout`. 시도
    #: 간격은 1 · 2 · 4 · 8초로 늘다가 이 시간이 지나면 그 시도를 접고, 다음 대여가 새 시도를
    #: 시작한다. «이 간격마다 접속한다» 는 뜻이 아니다. 라이브러리 기본 300초에서는 DB 가 뜬 뒤에도
    #: 다음 예정 시도까지 대여가 실패했다(실측 — 101.7초 중단 뒤 57초).
    reconnect_timeout_seconds: float
    #: 빌려 주기 전 연결 확인의 시간 제한(초). 넘기면 그 연결을 닫고 버린다(`app.core.db`).
    check_timeout_seconds: float
    #: libpq `keepalives` — TCP keepalive 를 켠다(참) · 끈다(거짓).
    keepalives: bool
    #: libpq `keepalives_idle` — 아무것도 오가지 않은 뒤 첫 확인 패킷까지(초).
    keepalives_idle_seconds: int
    #: libpq `keepalives_interval` — 답이 없을 때 확인 패킷 간격(초).
    keepalives_interval_seconds: int
    #: libpq `keepalives_count` — 답 없는 확인 패킷 몇 번이면 끊긴 것으로 보나. Windows 에서는
    #: 효과가 없다(libpq 문서 — `TCP_KEEPCNT` 가 있는 시스템만).
    keepalives_count: int
    #: libpq `tcp_user_timeout` — 보낸 데이터가 이 시간(밀리초) 동안 확인 응답을 못 받으면
    #: 연결을 끊는다. 0 이면 OS 기본값. Linux 에서만 효과가 있다(`TCP_USER_TIMEOUT`).
    tcp_user_timeout_ms: int


DB_CONNECTION_HEALTH_ENV_KEYS = (
    "DB_POOL_RECONNECT_TIMEOUT_SECONDS",
    "DB_POOL_CHECK_TIMEOUT_SECONDS",
    "DB_TCP_KEEPALIVES",
    "DB_TCP_KEEPALIVES_IDLE_SECONDS",
    "DB_TCP_KEEPALIVES_INTERVAL_SECONDS",
    "DB_TCP_KEEPALIVES_COUNT",
    "DB_TCP_USER_TIMEOUT_MS",
)

#: 기본값과 이유.
#:
#: - 재접속 15초: 시도 간격이 8초를 넘지 않는다(1 · 2 · 4 · 8). 시험 한 번에서 약 100초
#:   중단 뒤 1.61초에 복구됐다.
#: - 확인 5초: 빌릴 연결 대기 · 접속 시간과 같은 값. 프론트 읽기 20초보다 짧다.
#: - keepalive 30 · 10 · 3: 쉬는 연결이 망에서 끊겼으면 약 60초 안에 OS 가 알아챈다(Linux).
#: - user timeout 30,000ms: 보낸 데이터가 30초 동안 확인 응답을 못 받으면 끊는다(Linux). 오래 도는
#:   질의는 서버 OS 가 패킷에 응답하므로 이 값에 걸리지 않는다.
DEFAULT_CONNECTION_HEALTH_SETTINGS = ConnectionHealthSettings(
    reconnect_timeout_seconds=15.0,
    check_timeout_seconds=5.0,
    keepalives=True,
    keepalives_idle_seconds=30,
    keepalives_interval_seconds=10,
    keepalives_count=3,
    tcp_user_timeout_ms=30_000,
)


def connection_health_settings(*, load: Load = load_env_file) -> ConnectionHealthSettings:
    """재접속 · 연결 확인 시간과 TCP 상태 확인 옵션(`DB_CONNECTION_HEALTH_ENV_KEYS`).

    빈 값은 기본값이다. 숫자가 아니거나 범위가 틀리면 `ValueError` — 풀을 열기 전에 멈춘다.
    """
    load()
    raw = {key: os.getenv(key, "").strip() for key in DB_CONNECTION_HEALTH_ENV_KEYS}
    default = DEFAULT_CONNECTION_HEALTH_SETTINGS
    try:
        values = ConnectionHealthSettings(
            reconnect_timeout_seconds=float(
                raw["DB_POOL_RECONNECT_TIMEOUT_SECONDS"] or default.reconnect_timeout_seconds
            ),
            check_timeout_seconds=float(
                raw["DB_POOL_CHECK_TIMEOUT_SECONDS"] or default.check_timeout_seconds
            ),
            keepalives=(raw["DB_TCP_KEEPALIVES"] or str(int(default.keepalives))) == "1",
            keepalives_idle_seconds=int(
                raw["DB_TCP_KEEPALIVES_IDLE_SECONDS"] or default.keepalives_idle_seconds
            ),
            keepalives_interval_seconds=int(
                raw["DB_TCP_KEEPALIVES_INTERVAL_SECONDS"] or default.keepalives_interval_seconds
            ),
            keepalives_count=int(raw["DB_TCP_KEEPALIVES_COUNT"] or default.keepalives_count),
            tcp_user_timeout_ms=int(raw["DB_TCP_USER_TIMEOUT_MS"] or default.tcp_user_timeout_ms),
        )
    except ValueError as exc:
        raise ValueError(f"DB 연결 상태 확인 값이 숫자가 아니다: {raw}") from exc
    if (
        not values.reconnect_timeout_seconds > 0
        or not values.check_timeout_seconds > 0
        or raw["DB_TCP_KEEPALIVES"] not in ("", "0", "1")
        or values.keepalives_idle_seconds < 1
        or values.keepalives_interval_seconds < 1
        or values.keepalives_count < 1
        or values.tcp_user_timeout_ms < 0
    ):
        raise ValueError(
            "DB 연결 상태 확인 값의 범위가 틀렸다 — RECONNECT · CHECK 초 > 0, KEEPALIVES 0|1, "
            f"IDLE · INTERVAL · COUNT >= 1, USER_TIMEOUT_MS >= 0: {values}"
        )
    return values


def get_db_schema(*, load: Load = load_env_file) -> str:
    """SQL 이 쓸 PostgreSQL 스키마 이름(`DB_SCHEMA`). 연결이 아니라 SQL 문에 들어간다."""
    return required_database_environment(("DB_SCHEMA",), load=load)["DB_SCHEMA"]


# ── ML 원본 창고 접속 정보 ─────────────────────────────────────────────────────
#
# 원본 창고 풀(`app/core/db.py::ML_SOURCE_POOL`)이 열 때 읽는다. 서비스 풀의 접속 정보와
# 나란히 여기 둔다.


def ml_source_database_settings() -> DatabaseSettings:
    """원본 창고 접속 정보. 원자료와 학습 테이블이 있는 곳이다.

    ``ML_SOURCE_DB_*`` 가 있으면 그것을, 없으면 기본 ``DB_*`` 를 쓰되
    데이터베이스 이름만 ``ML_SOURCE_DB_NAME`` 으로 바꾼다.
    같은 서버의 다른 데이터베이스이므로 접속 정보를 두 벌 관리할 이유가 없다.
    """
    load_env_file()
    base = database_settings()
    name = os.getenv("ML_SOURCE_DB_NAME", "").strip()
    if not name:
        raise RuntimeError(
            "ML_SOURCE_DB_NAME 이 필요합니다. 원본 데이터가 있는 데이터베이스 이름입니다."
        )
    return replace(
        base,
        host=os.getenv("ML_SOURCE_DB_HOST", base.host),
        port=os.getenv("ML_SOURCE_DB_PORT", base.port),
        name=name,
        user=os.getenv("ML_SOURCE_DB_USER", base.user),
        password=os.getenv("ML_SOURCE_DB_PASSWORD", base.password),
    )


# ── 화면이 읽는 실행과 기준일 ─────────────────────────────────────────────────
#
# `SHOWN_SIM_RUN_ID` 는 화면이 조회하고 업무를 실행할 때 쓰는 실행 ID 의 기준값이다. 화면
# 탭 · 채팅 · 판매 진행 패널 · 하루 시뮬레이션이 `GET /api/console/shown-run` 으로 이 값을
# 받아 요청에 싣는다. 요청이 실행 ID 를 비워 보내면 HTTP 입구가 `screen_sim_run_id` 로
# 이 값을 채운다. service · readmodel · repository 는 이 값을 직접 읽지 않고 넘겨받은 값만
# 쓴다. CLI 인자, 외부 요청 body, 승인 대상 실행 행의 실행 ID 는 이 값으로
# 바꾸지 않는다.
#
# `SHOWN_AS_OF` 는 화면의 기본 기준일이다. 앱 실행 경로는 기준일을 요청에서 받으므로 이
# 값을 읽지 않는다. 프론트 `frontend/src/lib/demo_as_of.ts` 의 코드 기본값과 같은 값이어야
# 한다 (`tests/api/test_shown_run.py` 가 잡는다).
#
# 화면에서 번인 상수(`app/master/domain/sim_run.py` 의 `BURN_IN_SIM_RUN_ID`)를 쓰지 않는다.
# 번인은 2025-12 한 달치라 화면 숫자와 다른 장부를 보여 준다.
#
# 환경변수로 덮어쓰지 않는다. 이 파일의 다른 설정과 달리 `os.getenv` 로 읽지 않는
# 상수다. 덮어쓸 길을 두면 값의 주인이 둘이 되어 화면과 검사가 서로 다른 실행을 보게
# 된다.

SHOWN_SIM_RUN_ID = "SIM-MENTOR-0918"
SHOWN_AS_OF = date(2026, 9, 17)


def shown_sim_run_id() -> str:
    """화면이 보는 실행 ID 의 기준값. 부를 때마다 위 상수를 읽는다 — 사본을 두지 않는다."""
    return SHOWN_SIM_RUN_ID


def screen_sim_run_id(given: str | None) -> str:
    """HTTP 입구가 쓸 실행 ID. 요청이 준 값이 있으면 그 값, 없으면 기준값."""
    return shown_sim_run_id() if given is None else given
