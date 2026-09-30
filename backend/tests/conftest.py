"""스위트 전체 공통 — **테스트가 전역 상태를 원래대로 두고 나가게 한다.**

2026-09-03 · `tests/master` 가 재무 테스트를 깨뜨리는 것을 실측하다 나왔다.

🔴 **전역 레지스트리가 파트 밖으로 샜다.**

```text
pytest tests/master tests/finance/test_finance_api.py   → 1 failed
pytest tests/finance/test_finance_api.py tests/master   → 통과
pytest (전체 · 기본 순서)                                → 통과
```

`app/master/wiring._REGISTRY` 는 프로세스 전역이고, 등록은 `app/main.py` 가
**import 시점에 한 번** 한다. `wiring.reset()` 이 그것을 비우면 그 모듈은 이미
import 돼 있어 **다시 등록되지 않는다.**

⚠️ **전체 스위트가 알파벳순이라 안 걸렸다.** 재무가 마스터보다 먼저 돌아서
우연히 통과했고, **부분 실행에서만 깨졌다.** 각 파트가 자기 스위트만 돌리면
평생 안 보인다.

★ **부르는 쪽 다섯을 고치지 않는다.**

```text
tests/finance/test_finance_boundary_history.py:478
tests/master/test_ask.py:142 · :144
tests/master/test_master_api.py:24 · :26
```

  다섯을 고쳐도 **여섯 번째가 생기면 같은 일이 난다.** 여기서 한 번 막으면
  누가 어디서 부르든 그 테스트 밖으로 안 샌다.

★ **`reset()` 을 금지하지 않는다.** 등록을 비우는 것은 마스터 API 가 *"어댑터
  미등록"* 경로를 재려면 반드시 필요하다 — 막을 것은 **비우는 것**이 아니라
  **비운 채로 나가는 것**이다.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.master.registry import day_open as registry_day_open
from app.master.registry import transition as registry_transition
from app.master.registry import wiring as registry_wiring


@pytest.fixture(autouse=True)
def 전역_에이전트_레지스트리를_되돌린다() -> Iterator[None]:
    """테스트가 등록을 어떻게 바꾸든 **끝나면 원래대로**.

    ★ 테스트 앞에서 비우지 않는다. 그러면 *"등록된 상태를 전제하는"* 테스트가
      전부 깨진다 — 되돌리는 것이지 초기화하는 것이 아니다.
    """
    saved = registry_wiring.snapshot()
    try:
        yield
    finally:
        registry_wiring.restore(saved)


@pytest.fixture(autouse=True)
def 전역_전이_등록소를_되돌린다() -> Iterator[None]:
    """전이 등록소도 같은 자리에서 샌다 — **끝나면 원래대로**.

    🔴 **위 레지스트리와 똑같은 함정이다.** `app/master/transition._TRANSITIONS` 도
       프로세스 전역이고 등록은 `app/main.py` 가 **import 시점에 한 번** 한다.
       `transition.reset()` 을 부른 테스트가 그대로 나가면 그 프로세스에서 **영영
       빈 채로** 남는다 — 이미 import 된 `app.main` 은 다시 등록되지 않는다.

    ⚠️ 전이 등록이 0건이던 동안에는 비워도 비운 티가 안 났다. 등록이 서는 순간
       (`#272`) 부터 이 새는 자리가 실제로 다른 파트 검사에 닿는다.

    ★ `wiring` 과 달리 `snapshot`/`restore` 가 없어 여기서 `registered()` 로 뜨고
      `reset()` + `register_transition()` 으로 되돌린다 — 마스터 모듈에 검사 전용
      함수를 더하지 않는다.
    """
    saved = dict(registry_transition.registered())
    try:
        yield
    finally:
        registry_transition.reset()
        for part, impl in saved.items():
            registry_transition.register_transition(part, impl)


@pytest.fixture(autouse=True)
def 전역_하루넘김_등록소를_되돌린다() -> Iterator[None]:
    """하루 넘김 등록소도 프로세스 전역이다 — **끝나면 원래대로**.

    ⚠️ **오늘은 함정이 아직 없다.** `app/master/day_open._OPENINGS` 는 전역이지만
       `app/main.py` 에 등록 줄이 없다 (재무 미회신 · 물류 파트 소유). 그래서
       `reset()` 을 부른 테스트가 그대로 나가도 지워질 등록 자체가 없다.

    ★ **그래도 지금 세워 둔다.** 전이 등록소가 정확히 그 순서로 물렸다 — 0건이던
      동안에는 비워도 티가 안 나다가, 등록이 서는 날(`#272`) 갑자기 다른 파트
      검사에 닿았다. 여기서 새는 방향은 반대다: 대역을 등록한 테스트가 그대로
      나가면 **가짜 구현이 다음 테스트로 흘러든다.**

    ★ 위 전이 fixture 와 같은 방식이다 — `registered()` 로 뜨고 `reset()` +
      `register_day_opening()` 으로 되돌린다.
    """
    saved = dict(registry_day_open.registered())
    try:
        yield
    finally:
        registry_day_open.reset()
        for part, impl in saved.items():
            registry_day_open.register_day_opening(part, impl)


# ── 실 DB 차단 — 스위트 전체 (2026-09-29 · 풀 전환) ─────────────────────────


class 실_DB_연결을_열었다(AssertionError):
    """`db` 마크가 없는 검사가 실 DB 연결을 열려 했다."""


@pytest.fixture(autouse=True)
def 실_DB_연결을_막는다(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """🔴 **`db` 마크가 없는 검사가 실 DB 에 닿으려 하면 그 자리에서 예외를 던진다.**

    ★ **문이 셋이다.** 연결은 이제 `app/core/db.py` 의 풀이 만든다.

    ```text
    psycopg.connect                  옛 문. 앱 안에는 부르는 곳이 없지만 스크립트 · 검사가 쓴다
    psycopg.Connection.connect       풀의 작업 스레드가 새 연결을 만들 때 부르는 문
    ConnectionPool.open (psycopg 연결)  풀을 여는 문 — 부른 자리에서 바로 멈추게 한다
    ```

    🔴 **`psycopg.connect` 만 막으면 풀은 샌다.** psycopg_pool 은
       `connection_class.connect()` 를 부르므로(3.3.3 소스) 모듈 이름 `psycopg.connect` 를
       바꿔 끼워도 닿지 않는다. 또 그 문은 **풀의 작업 스레드**에서 열려, 거기서 난 예외는
       검사로 올라오지 않고 대여가 시간 초과까지 기다린다. 그래서 풀을 **여는 자리**에서
       먼저 멈춘다 — 검사용 가짜 연결 종류(`tests/core/fake_pg_connection.py`)로 여는 풀은 통과한다.

    ★ **`.env` 가 있는 자리를 없는 자리와 같게 만든다.** 없는 자리에서는 환경변수
      확인이 먼저 터져 여기까지 안 온다. 있는 자리에서는 이 가드가 없으면 새는 검사가
      **팀 공용 DB 를 조용히 치고** 답이 그날 표에 따라 갈린다.

    ★ 2026-09-14 부터 `tests/master/conftest.py` 에만 있던 가드를 여기로 올렸다 — 설계서
      §기존 테스트 활용과 보완 ⑤. 실 DB 가 필요한 검사는 `db` 마크를 단다.

    ⚠️ **예외를 삼키는 경로는 이 가드로 빨개지지 않는다.** 삼키는 경로도 실 DB 에는
      닿지 않는다.
    """
    if request.node.get_closest_marker("db") is not None:
        return

    import psycopg
    import psycopg_pool

    def 막는다(*_args: object, **_kwargs: object) -> object:
        raise 실_DB_연결을_열었다(
            f"db 마크가 없는 검사가 실 DB 연결을 열었다: {request.node.nodeid}"
        )

    monkeypatch.setattr(psycopg, "connect", 막는다)
    monkeypatch.setattr(psycopg.Connection, "connect", classmethod(막는다))

    original_pool_open = psycopg_pool.ConnectionPool.open

    def refuse_real_connection_pool_open(
        self: psycopg_pool.ConnectionPool, *args: object, **kwargs: object
    ) -> None:
        if isinstance(self.connection_class, type) and issubclass(
            self.connection_class, psycopg.Connection
        ):
            raise 실_DB_연결을_열었다(
                f"db 마크가 없는 검사가 실 DB 연결 풀을 열었다: {request.node.nodeid}"
            )
        original_pool_open(self, *args, **kwargs)

    monkeypatch.setattr(psycopg_pool.ConnectionPool, "open", refuse_real_connection_pool_open)


@pytest.fixture(autouse=True, scope="session")
def close_pools_after_session() -> Iterator[None]:
    """스위트가 끝나면 열린 풀을 닫는다 — `db` 마크 검사가 연 풀의 작업 스레드를 남기지 않는다."""
    yield
    from app.core import db as core_db

    core_db.close_pools()
