"""FastAPI 앱.

/health 는 배포 스크립트와 컨테이너 HEALTHCHECK 가 호출하는 엔드포인트입니다.
앱을 확장하더라도 이 경로는 유지하세요 — 배포 성공 판정의 기준입니다.
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import router as api_router
from app.core import db as core_db
from app.master.registry.bootstrap import wire_registries


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """앱이 떠 있는 동안 DB 연결 풀을 쓴다 — 시작 때 열고, 끝날 때 닫는다.

    - 풀만 다룬다. 테이블을 만들거나 고치지 않는다(정의는 `database/**/*.sql` 하나다).
    - DB 설정이 없으면 미리 열지 않고 뜬다 — `/health` 와 DB 를 안 쓰는 경로는 그대로 돌고,
      DB 경로는 첫 대여에서 `MissingDatabaseEnvironment` 로 실패한다
      (`app/core/db.py::pool_lifespan`).
    """
    with core_db.pool_lifespan():
        yield


app = FastAPI(title="mainproject", lifespan=lifespan)
# HTTP 입구 전부 — 화면 탭(`/api/…`) · 부서 · 마스터 · Critic · ML 라우트를 `app/api/router.py`
# 한 곳이 모은다. 등록 순서 · 주소로 가르는 규칙 · 물류에 자기 라우터가 없는 이유는 그 파일에
# 있다.
app.include_router(api_router)

# ── 등록소를 채운다 ────────────────────────────────────────────────────
#
# 등록 줄은 여기 없다. `app/master/registry/bootstrap.py` 하나에 있다.
#
#   이 모듈에 register_* 줄을 모듈 수준으로 늘어놓으면 이 모듈을 임포트한 쪽만 등록된
#   세상을 본다. FastAPI 는 `app/main.py` 를 임포트하지만 CLI 러너(`app/master/cli/`)는
#   거치지 않으므로, 등록소가 빈 채로 걷게 된다("하루 넘김 미등록: finance, logistics").
#   그래서 앱과 CLI 러너가 같은 `wire_registries()` 를 부른다.
#
# 임포트 시점에 부른다. 무엇을 왜 등록하는지는 그 파일의 각 줄 위에 있다.
wire_registries()

# 옛 `app/orchestrator/` 패키지와 `/orchestrator/…` 라우터는 없다. 마스터 에이전트가 그 역할을
# 맡는다. 폴더가 돌아오지 못하게 `tests/master/test_orchestrator_is_gone.py` 가 잠근다.


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def index():
    return {
        "message": "mainproject is running",
        "version": os.getenv("APP_VERSION", "dev"),
    }
