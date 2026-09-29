"""프로세스 설정 — `.env` 위치, DB 접속 정보, 연결 풀 크기, SQL 이 쓸 스키마 이름.

2026-09-28 부서별 `db.py` 다섯 벌에 복제돼 있던 부분을 여기로 모았다.

```text
.env 위치      backend/.env (부서 db.py 들이 가리키던 곳과 같다)
읽는 시점      호출할 때마다 load_dotenv — 기본값. 이미 있는 환경변수는 덮지 않는다
값             적재 뒤 os.getenv 로 매번 읽는다 → 검사가 환경변수를 바꾸면 그대로 따라간다
빠진 값        MissingDatabaseEnvironment("Missing required database environment variables: …")
               (RuntimeError 의 하위 종류 — 종전 문구·종류 그대로 잡힌다)
```

★ **읽는 시점은 부서가 고른다.** 물류는 `.env` 를 프로세스에서 한 번만 읽는다
  (`app/logistics/db.py` · 대시보드 한 요청에 550회 불리던 비용). 그래서 적재 함수를
  `load` 인자로 받는다 — 한 벌로 합치면 어느 한쪽 동작이 바뀐다.

★ **접속 정보는 풀을 열 때 한 번 읽는다** (2026-09-29 · 풀 전환). 연결마다 새로 읽지 않으므로
  실행 중에 `DB_*` 를 바꿔도 이미 열린 풀에는 반영되지 않는다. 스키마 이름(`DB_SCHEMA`)은
  SQL 을 만들 때마다 그대로 읽는다.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

#: `backend/.env`. 부서 `db.py` 의 `Path(__file__).parent.parent.parent / ".env"` 와 같은 파일.
ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"

#: 서비스 DB 연결에 필요한 환경변수. `DB_SCHEMA` 는 연결이 아니라 SQL 에 쓰므로 따로 읽는다.
DB_CONNECTION_ENV_KEYS = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")

Load = Callable[[], None]


def load_env_file() -> None:
    """`backend/.env` 를 환경변수로 적재한다. 이미 있는 값은 덮지 않는다(`override=False`)."""
    load_dotenv(ENV_FILE)


class MissingDatabaseEnvironment(RuntimeError):
    """필수 DB 환경변수가 비었다. 문구는 종전 `RuntimeError` 와 같다.

    ★ 따로 이름을 둔 이유: 앱·CLI 가 시작할 때 풀을 미리 열다 **설정이 아예 없는 자리**
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
    """PostgreSQL 에 접속할 곳과 계정. `app.core.db.connect` 가 이 값으로 연결을 연다."""

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

#: 기본값과 그 이유 (2026-09-29 · 실 DB 로 잰 값이 아니라 실행 방식에서 고른 값이다).
#:
#: - `min_size=1` — 백엔드는 uvicorn 프로세스 하나로 돌고(`Dockerfile` CMD 에 workers 없음),
#:   CLI 는 하루 단계를 한 줄로 걷는다. 팀이 PostgreSQL 한 대를 같이 쓰므로 쉬는 동안
#:   붙잡아 두는 연결은 하나로 둔다. 나머지는 쓸 때 늘었다가 10분 쉬면 풀이 닫는다.
#: - `max_size=10` — 화면은 창 여러 개를 한꺼번에 부르고(동시 GET), 마스터 경계 함수는
#:   연결 하나를 쥔 채 이력 저장·조회로 하나를 더 빌린다(한 흐름에 2~3개). 상한이 그보다
#:   작으면 겹친 대여가 서로를 기다리다 시간 초과가 난다.
#: - `timeout_seconds=5` — 접속 타임아웃(`app/core/db.py::CONNECT_TIMEOUT_SECONDS`)과 같은 값.
#:   DB 가 답하지 않을 때 대여 한 번이 포기하는 시간이 종전(연결마다 5초)과 같고, 프론트
#:   읽기 20초보다 짧아 백엔드가 먼저 사유를 낸다.
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


def get_db_schema(*, load: Load = load_env_file) -> str:
    """SQL 이 쓸 PostgreSQL 스키마 이름(`DB_SCHEMA`). 연결이 아니라 SQL 문에 들어간다."""
    return required_database_environment(("DB_SCHEMA",), load=load)["DB_SCHEMA"]
