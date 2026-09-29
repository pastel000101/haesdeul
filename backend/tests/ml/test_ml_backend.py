"""ML 백엔드 HTTP 호출 — 운영 콘솔 프록시(`/ml/console/*`)와 재학습 대기 조회.

★ **진짜 ML 백엔드에 붙지 않는다.** httpx 의 가짜 전송(`MockTransport`)으로 저쪽 답을 흉내 내고,
  이 서버가 **무엇을 넘기고 무엇을 돌려주는지**만 잰다.

재는 것 (2026-09-29 재구성 BL-017 — 호출이 `console_proxy.py` · `qa_tools.py` 에서
`ml_backend.py` 로 모이며, 그 전에는 이 경로를 재는 검사가 없었다)

```text
전달     경로 · 쿼리 · 본문 · 머리글(POST 만 Content-Type) · 주소(ML_CONSOLE_ORIGIN)
돌려줌   저쪽 상태 · 본문 그대로, JSON 이 아니면 {"detail": 앞 500자}
실패     닿지 못함 502 · 늦음 504 · 목록 밖 경로 404(저쪽을 부르지 않음)
대기     /retrain/pending — 모르는 종류는 버림 · 못 읽으면 터짐 · 짧은 타임아웃
```
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.ml import ml_backend

ORIGIN = "http://ml.test:8102"


@pytest.fixture(autouse=True)
def _origin(monkeypatch: pytest.MonkeyPatch) -> None:
    """`.env` 를 읽지 않고 주소를 환경변수로 준다."""
    monkeypatch.setattr(ml_backend, "load_dotenv", lambda *_a, **_k: False)
    monkeypatch.setenv("ML_CONSOLE_ORIGIN", ORIGIN + "/")


class _Seen(list):
    """받은 요청 목록 + 클라이언트를 만들 때 넘긴 인자."""

    client_kwargs: dict


def _backend(monkeypatch: pytest.MonkeyPatch, handler) -> _Seen:
    """비동기 클라이언트를 가짜 전송으로 바꿔 끼운다. 받은 요청을 돌려준다."""
    seen = _Seen()
    seen.client_kwargs = {}
    real = httpx.AsyncClient

    def _handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    def factory(**kwargs):
        seen.client_kwargs.update(kwargs)
        return real(transport=httpx.MockTransport(_handle), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return seen


def test_get_passes_path_and_query_and_returns_the_backend_reply_unchanged(monkeypatch):
    seen = _backend(monkeypatch, lambda _r: httpx.Response(400, json={"detail": "bad kind"}))

    got = TestClient(app).get("/ml/console/quality", params={"kind": "auc", "n": "3"})

    assert got.status_code == 400                        # 저쪽 상태 그대로
    assert got.json() == {"detail": "bad kind"}
    (request,) = seen
    assert request.method == "GET"
    assert str(request.url) == f"{ORIGIN}/quality?kind=auc&n=3"
    assert request.content == b""
    assert "content-type" not in request.headers
    assert seen.client_kwargs["timeout"] == ml_backend.TIMEOUT


def test_post_passes_the_body_as_json(monkeypatch):
    seen = _backend(monkeypatch, lambda _r: httpx.Response(202, json={"ok": True}))
    body = json.dumps({"kind": "rtl", "action": "apply"}).encode()

    got = TestClient(app).post("/ml/console/retrain/graph/act", content=body)

    assert got.status_code == 202
    assert got.json() == {"ok": True}
    (request,) = seen
    assert request.method == "POST"
    assert str(request.url) == f"{ORIGIN}/retrain/graph/act"
    assert request.content == body
    assert request.headers["content-type"] == "application/json"


def test_a_non_json_reply_becomes_a_detail_of_its_first_500_characters(monkeypatch):
    _backend(monkeypatch, lambda _r: httpx.Response(500, text="x" * 700))

    got = TestClient(app).get("/ml/console/batch/recent")

    assert got.status_code == 500
    assert got.json() == {"detail": "x" * 500}


@pytest.mark.parametrize(
    ("method", "path", "detail"),
    [
        ("GET", "meta", "넘길 수 있는 경로가 아닙니다: meta"),
        (
            "POST",
            "retrain/apply",
            (
                "쓰기로 넘길 수 있는 경로가 아닙니다: retrain/apply. 가능: "
                "ops/rerun, retrain/graph/act, retrain/graph/reset"
            ),
        ),
    ],
)
def test_paths_outside_the_list_are_404_and_never_reach_the_backend(
    monkeypatch, method, path, detail
):
    seen = _backend(monkeypatch, lambda _r: httpx.Response(200, json={}))

    got = TestClient(app).request(method, f"/ml/console/{path}")

    assert got.status_code == 404
    assert got.json() == {"detail": detail}
    assert seen == []


def test_an_unreachable_backend_is_502_with_the_address_and_where_it_came_from(monkeypatch):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _backend(monkeypatch, refuse)

    got = TestClient(app).get("/ml/console/quality")

    assert got.status_code == 502
    detail = got.json()["detail"]
    assert detail.startswith(f"ML 백엔드에 닿지 못했습니다 ({ORIGIN}). ")
    assert "ML_CONSOLE_ORIGIN 에서 왔습니다" in detail
    assert detail.endswith("— connection refused")


def test_a_slow_backend_is_504_with_the_path(monkeypatch):
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    _backend(monkeypatch, slow)

    got = TestClient(app).get("/ml/console/ops/job")

    assert got.status_code == 504
    assert got.json() == {
        "detail": f"ML 백엔드가 제때 답하지 않았습니다 ({ORIGIN}/ops/job) — timed out"
    }


def test_the_origin_falls_back_to_the_local_default_and_says_so(monkeypatch):
    monkeypatch.delenv("ML_CONSOLE_ORIGIN")

    origin, hint = ml_backend.console_origin()

    assert origin == "http://127.0.0.1:8102"
    assert hint.startswith("★ backend/.env 에 ML_CONSOLE_ORIGIN 이 없어 기본값을 썼습니다.")


def test_the_origin_is_read_from_the_backend_env_file(monkeypatch):
    """`.env` 는 요청마다 `backend/.env` 를 읽는다 (옛 `console_proxy._ENV_FILE` 과 같은 파일)."""
    from app.core.settings import ENV_FILE

    read: list[object] = []
    monkeypatch.setattr(ml_backend, "load_dotenv", lambda path, **kw: read.append((path, kw)))

    ml_backend.console_origin()

    assert read == [(ENV_FILE, {"override": False})]
    assert ENV_FILE.parent.name == "backend"


def test_timeouts_are_unchanged():
    """🔴 채팅 길목(재학습 대기)은 짧게, 콘솔 전달은 길게 — 둘을 섞지 않는다."""
    assert ml_backend.TIMEOUT == httpx.Timeout(connect=3.0, read=30.0, write=10.0, pool=3.0)
    assert ml_backend.PENDING_TIMEOUT == httpx.Timeout(connect=2.0, read=4.0, write=2.0, pool=2.0)


# ── 재학습 대기 (채팅 «모델 성능» 갈래) ─────────────────────────────────────


def _sync_backend(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    seen: list[httpx.Request] = []
    real = httpx.Client

    def _handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    def factory(**kwargs):
        assert kwargs["timeout"] == ml_backend.PENDING_TIMEOUT
        return real(transport=httpx.MockTransport(_handle), **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)
    return seen


def test_pending_keeps_only_kinds_a_button_can_be_drawn_for(monkeypatch):
    rows = [{"kind": "RTL", "state": "wait"}, {"kind": "garlic"}, "not-a-row", {"kind": "auc"}]
    seen = _sync_backend(monkeypatch, lambda _r: httpx.Response(200, json={"pending": rows}))

    assert ml_backend.retrain_pending() == [{"kind": "RTL", "state": "wait"}, {"kind": "auc"}]
    (request,) = seen
    assert str(request.url) == f"{ORIGIN}/retrain/pending"


def test_pending_that_cannot_be_read_raises_instead_of_saying_none(monkeypatch):
    """🔴 «못 읽었다» 를 «후보 없음» 으로 돌려주지 않는다."""
    _sync_backend(monkeypatch, lambda _r: httpx.Response(503, json={"detail": "down"}))
    with pytest.raises(httpx.HTTPStatusError):
        ml_backend.retrain_pending()

    _sync_backend(monkeypatch, lambda _r: httpx.Response(200, json=["not", "a", "dict"]))
    with pytest.raises(TypeError):
        ml_backend.retrain_pending()
