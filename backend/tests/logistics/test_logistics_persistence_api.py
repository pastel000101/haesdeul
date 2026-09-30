from contextlib import nullcontext
from datetime import UTC, date, datetime
from typing import Any, Self
from unittest.mock import patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from app.logistics.readmodel.runs import list_logistics_agent_runs
from app.logistics.service.run_history import save_logistics_agent_run
from app.main import app


class _기록커서:
    def __init__(self, 연결: "_기록연결") -> None:
        self.연결 = 연결

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, query: object, params: Any = None) -> None:
        self.연결.실행.append((query, params))

    def fetchone(self) -> Any:
        return self.연결.한줄

    def fetchall(self) -> list[Any]:
        return list(self.연결.여러줄)


class _기록연결:
    """받은 SQL · 인자를 적고 정해 둔 행을 돌려주는 연결.

    ★ 2026-09-30 재구성 BL-015: 종전에는 `run_repository` 가 자기 연결을 여는 헬퍼
      (`execute_returning_one` · `fetch_all`)를 불러 그 헬퍼를 바꿔 끼웠다. 이제 저장은
      service 가 빌린 연결 · 트랜잭션 하나로, 조회는 readmodel 이 빌린 조회 연결로
      repository 에 넘기므로 빌려 주는 자리에 이 연결을 준다.
    """

    def __init__(self, *, 한줄: Any = None, 여러줄: tuple[Any, ...] = ()) -> None:
        self.실행: list[tuple[object, Any]] = []
        self.한줄 = 한줄
        self.여러줄 = 여러줄
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> _기록커서:
        return _기록커서(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _run_row() -> dict[str, object]:
    return {
        "run_id": UUID("00000000-0000-0000-0000-000000000001"),
        "cycle": "PROCUREMENT",
        "as_of": date(2026, 8, 21),
        "snapshot_id": None,
        "runtime_status": "RUNTIME_NOT_READY",
        "verdict": None,
        "request_payload": {},
        "response_payload": {"verdict": None},
        "created_at": datetime(2026, 8, 21, tzinfo=UTC),
    }


def test_run_repository_uses_jsonb_and_metadata():
    row = _run_row()
    conn = _기록연결(한줄=row)
    with (
        patch("app.logistics.repository.runs.get_db_schema", return_value="haetdeul"),
        patch("app.core.db.connection", lambda: nullcontext(conn)),
    ):
        saved = save_logistics_agent_run(
            cycle="PROCUREMENT",
            as_of=date(2026, 8, 21),
            snapshot_id=None,
            runtime_status="RUNTIME_NOT_READY",
            verdict=None,
            request_payload={},
            response_payload=row["response_payload"],
        )

    (_, params), = conn.실행
    assert saved == row
    assert (conn.commits, conn.rollbacks) == (1, 0), "저장 한 번 = 트랜잭션 하나"
    assert params[1:5] == ("PROCUREMENT", date(2026, 8, 21), None, "RUNTIME_NOT_READY")
    assert params[5] is None
    assert isinstance(params[6], Jsonb)
    assert isinstance(params[7], Jsonb)


def test_run_repository_filters_by_verdict():
    row = _run_row()
    conn = _기록연결(여러줄=(row,))
    with (
        patch("app.logistics.repository.runs.get_db_schema", return_value="haetdeul"),
        patch("app.core.db.read_connection", lambda: nullcontext(conn)),
    ):
        assert list_logistics_agent_runs(verdict="REVIEW_REQUIRED") == [row]

    (_, params), = conn.실행
    assert params == ["REVIEW_REQUIRED", 100]
    assert (conn.commits, conn.rollbacks) == (0, 0), "조회는 트랜잭션을 열지 않는다"


def test_run_repository_rejects_verdict_metadata_mismatch():
    with pytest.raises(ValueError, match="must match"):
        save_logistics_agent_run(
            cycle="PROCUREMENT",
            as_of=date(2026, 8, 21),
            snapshot_id=None,
            runtime_status="READY",
            verdict="PASS",
            request_payload={},
            response_payload={"verdict": "FAIL"},
        )


# ── 물류 HTTP 경계 ──────────────────────────────────────────────────────


def test_물류에는_자기_HTTP_라우터가_없다():
    """🔴 **물류 HTTP 경계는 `/api/logistics` 하나다** (2026-09-15 · 물류 문서 28).

    종전에는 `app/logistics/router.py` 가 `/logistics/…` 16 경로를 냈다. 그런데

    ```text
    화면    /api/logistics 를 친다 (app/api/logistics/routes.py)   ← 프론트 진입점
    마스터  adapter.logistics_port 를 **파이썬으로** 부른다          ← HTTP 가 아니다
            (master/bootstrap.py 의 register_agent("inventory", logistics_port))
    ```

    라서 그 16 경로를 **아무도 안 불렀다.** 같은 콘솔 조회가 두 주소로 나가면 어느 쪽이
    정본인지 갈리므로 걷어냈다.

    ⚠️ 종전 이 자리의 검사는 `/logistics/procurement` 등이 **등록돼 있는지**를 봤다.
       지금은 그 반대를 잠근다 — 되살아나면 경계가 다시 둘이 된다.
    """
    paths = TestClient(app).get("/openapi.json").json()["paths"]
    물류 = sorted(p for p in paths if "logistics" in p)

    assert 물류 == ["/api/logistics"], 물류
