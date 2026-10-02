from fastapi.testclient import TestClient

from app.main import app

# ── 물류 HTTP 경계 ──────────────────────────────────────────────────────


def test_물류에는_자기_HTTP_라우터가_없다():
    """🔴 **물류 HTTP 경계는 `/api/logistics` 하나다** (2026-09-15 · 물류 문서 28).

    종전에는 `app/logistics/router.py` 가 `/logistics/…` 16 경로를 냈다. 그런데

    ```text
    화면    /api/logistics 를 친다 (app/api/logistics/routes.py)   ← 프론트 진입점
    마스터  adapter.logistics_port 를 **파이썬으로** 부른다          ← HTTP 가 아니다
            (master/registry/bootstrap.py 의 register_agent("inventory", logistics_port))
    ```

    라서 그 16 경로를 **아무도 안 불렀다.** 같은 콘솔 조회가 두 주소로 나가면 어느 쪽이
    정본인지 갈리므로 걷어냈다.

    ⚠️ 종전 이 자리의 검사는 `/logistics/procurement` 등이 **등록돼 있는지**를 봤다.
       지금은 그 반대를 잠근다 — 되살아나면 경계가 다시 둘이 된다.
    """
    paths = TestClient(app).get("/openapi.json").json()["paths"]
    물류 = sorted(p for p in paths if "logistics" in p)

    assert 물류 == ["/api/logistics"], 물류
