"""**`app/` 에서 벽시계를 읽는 곳은 `core/clock.py` 하나다.**

🔴 **왜 검사로 지키는가.** 벽시계는 한 줄이면 다시 생긴다. `datetime.now()` 를
어딘가에 적는 것은 아무 마찰도 없고, 스위트도 안 깨진다 — **오늘 날짜로 답해도
오늘 도는 검사는 통과하기 때문이다.** 그러다 백테스트를 돌리는 날, 그 한 줄부터
아래로 전부 오늘로 답하기 시작하고 성적이 조용히 무효가 된다.

```text
지금   벽시계를 새로 적는다 → 이 파일이 그 자리에서 운다
없으면 벽시계를 새로 적는다 → 아무 일도 안 난다 → 백테스트에서 뒤늦게 안다
```

★ **막는 파일이 아니라 세우는 파일이다.** 스케줄러는 `as_of` 를 아무한테서도 못
  받으므로 시계를 읽어야만 시작한다. 그 예외를 **한 파일이 혼자 지게** 두고, 그
  아래로는 `as_of` 를 인자로만 흘린다 (`clock.py` docstring).

🔴 **자기 생존 검사를 같이 둔다.** 스캐너가 `clock.py` 의 그 호출을 **실제로
  찾는지** 부터 본다. 안 그러면 스캐너가 망가져 0건을 세는 날 **공짜 초록**이 나고,
  그때는 벽시계가 열 군데 생겨도 이 파일이 통과한다.

🔴 **스캐너가 둘이다** (2026-09-09 · `#452`).

```text
① 벽시계를 읽는 줄     date.today() · datetime.now() · datetime.utcnow()
② 그 줄을 가져가는 줄   clock.today_in_seoul · clock.seoul_now 임포트
```

  ★ **①만으로는 샌다.** `revalidation.py` 가 `today_in_seoul()` 을 불러 벽시계를
    읽고 있었는데, 그것은 `date.today()` 도 `datetime.now()` 도 아니라 ①에 안
    걸렸다 — 이 파일이 그때 초록이었다. ②가 그 자리를 잡는다.

🔴 **훑는 범위가 `app/` 전체다** (2026-09-29). 시계가 `app/master/clock.py` 에서
  `app/core/clock.py` 로 옮겨 가면서 부서도 가져다 쓸 수 있게 됐다 — 전처럼 `app/master/`
  만 훑으면 부서에서 새는 자리를 못 본다. 옮기던 날 앱 전체를 훑은 결과가 아래 목록
  그대로였다(벽시계 호출은 `core/clock.py` 하나, 시계 함수를 가져가는 곳 셋, 시간대를
  따로 만드는 곳은 ML 고정 오프셋 둘). 이 파일은 그날 `tests/master/` 에서 옮겨 왔다.

⚠️ **`tests/` 는 대상이 아니다.** 검사가 고정 시각을 만드는 것은 벽시계가 아니다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import app

_APP = Path(app.__file__).parent

#: 벽시계를 읽는 호출 이름.
#:
#: ★ `now` 하나로는 못 잡는다 — `date.today()` 도 `datetime.utcnow()` 도 같은 일을
#:   하고, 셋 중 하나만 열려 있으면 그리로 샌다.
_WALL_CLOCK_ATTRS = frozenset({"today", "now", "utcnow"})

#: 그 호출을 받는 이름. `datetime.datetime.now()` 처럼 점이 더 붙어도 끝만 본다.
_CLOCK_RECEIVERS = ("date", "datetime")

#: 벽시계를 읽어도 되는 **단 하나의 파일** (`app/` 기준 경로).
_ALLOWED = "core/clock.py"

#: 그 파일의 import 이름. 시계 함수를 가져가는 자리를 이 이름으로 찾는다.
_CLOCK_MODULE = "app.core.clock"

#: 🟡 **날짜가 아니라 사건 시각을 찍는 자리.** 축이 달라 이 규율의 대상이 아니다.
#:
#: ```text
#: as_of        "오늘이 며칠인가"        판단이 그 위에 선다   → clock.py 하나
#: ended_at     "이 실행이 언제 끝났나"   지나간 일의 기록      → 여기
#: ```
#:
#: ★ `app/contracts/core.py:1715` 의 `started_at: datetime = field(default_factory=
#:   datetime.utcnow)` 와 **짝**이다. 한쪽만 timezone-aware 로 바꾸면 두 칸의 뜻이
#:   갈리고, 그것은 FROZEN 계약을 건드리는 판이라 여기서 하지 않는다.
#:
#: 🔴 **목록이지 면제가 아니다.** 여기 없는 자리가 새로 생기면 아래 검사가 운다.
#:   그리고 `test_예외_자리는_날짜를_안_만든다` 가 이 자리가 정말 날짜로 안 새는지
#:   따로 확인한다.
#:
#: ⚠️ **줄 번호는 안 적는다** — 편집마다 흔들린다
#:   (`test_external_use_of_master_internals.py` 와 같은 규율).
#:
#: 🟢 **2026-09-08 에 비었다.** 유일한 자리였던 `cycle_graph.py` 의 `node_t4_commit`
#:   이 파일과 함께 지워졌다 (`test_master_legacy_cycle_is_gone.py`). 목록이 빈 것은
#:   *"예외가 없다"* 이고, 새 자리가 생기면 아래 검사가 그날 운다.
_TIMESTAMP_ONLY: dict[str, set[str]] = {}


#: `clock.py` 가 내주는 것 중 **실제로 시계를 읽는** 두 이름.
#:
#: 🔴 **위 스캐너로는 이 새는 자리를 못 잡는다.** `today_in_seoul()` 은 `date.today()`
#:   도 `datetime.now()` 도 아니라 `_WALL_CLOCK_ATTRS` 에 안 걸린다 — `revalidation.py`
#:   가 정확히 그 모양으로 벽시계를 읽고 있었고(2026-09-09 · `#452`), 이 파일은 그때
#:   초록이었다. **읽는 줄만 세는 것으로는 부족하고, 그 줄을 가져가는 것도 세야 한다.**
#:
#: ★ **상수는 시계가 아니다.** `SEOUL` 은 값이라 가져가도 답이 안 움직인다
#:   (`sim_time.py` · 물류 출고·과거 조회가 그것만 가져간다).
_CLOCK_READERS = frozenset({"seoul_now", "today_in_seoul"})

#: 그 둘을 가져가도 되는 파일 — **실제 오늘이 곧 입력인 자리뿐이다** (`app/` 기준 경로).
#:
#: ```text
#: master/scheduler.py   아무도 as_of 를 안 넘겨 준다. 09:30 에 깨어나는 것이 유일한 입력
#: master/backfill.py    자동 승인의 실제 오늘 가드 — 걷기가 넘기는 날짜로 재면 풀린다
#: ml/service/qa_graph.py  ML 질의응답이 기준일(as_of) 없이 왔을 때 「오늘」로 대체한다
#: ```
#:
#: 🔴 **깊은 자리는 여기 못 들어온다.** 들어오는 순간 백테스트가 그 지점부터 오늘로
#:   답한다 — `revalidation.py` 를 여기 다시 적으려면 걷기가 승인 경로를 탈 때
#:   무슨 일이 나는지부터 설명해야 한다.
#: 🔴 **스케줄러 하나다** (2026-09-09). 전에는 `router.py` 도 있었는데, 승인 재검증이
#: 서는 날을 **실행 이력 행**이 정하게 바꾸면서 진입점이 시계를 안 읽게 됐다.
#: 🔴 **`backfill.py` 가 둘째다** (2026-09-14). 경계를 미래(09-18)로 옮기면서 **실제
#: 서울 오늘 이후는 자동 승인 금지** 가드를 함께 뒀다. 그 가드는 `as_of` 가 아니라
#: 실제 시계를 재야 뜻이 선다 — 걷기가 넘기는 날짜로 재면 걷기가 고른 날짜 하나로
#: 가드가 풀린다. 그래서 `backfill_decisions(today=today_in_seoul)` 기본 인자로만
#: 가져가고, 판단 · 재검증 쪽으로는 안 흘린다 (가드 한 줄만 쓴다).
#: 🔴 **`ml/qa_graph.py` 가 셋째다** (2026-09-29 · 범위를 `app/` 전체로 넓히며 적었다 —
#: 그 전부터 가져가고 있었고, 마스터만 훑던 검사 밖이었다). 화면(`/ml/qa`)이나 마스터
#: 봉투(`ml_port`)가 기준일을 실어 보내면 그 날을 쓰고, 없을 때만 서울 오늘로 대체한다
#: (해석 노드와 `_asked_on`). 2026-09-29 재구성 BL-017 에 그래프가 `ml/service/qa_graph.py` 로
#: 옮겼다 — 판단(`ml/domain/`) · 답 조립(`ml/readmodel/`)은 시계를 가져가지 않고 그래프가
#: 정한 날을 인자로 받는다.
_CLOCK_READER_IMPORTERS = frozenset(
    {"master/scheduler.py", "master/backfill.py", "ml/service/qa_graph.py"}
)

#: 🟡 **서울 지역 시간대가 아니라 고정 오프셋 UTC+9 를 따로 드는 자리** (2026-09-29 확인).
#:
#: ★ `SEOUL` 과 지금은 같은 값이지만 **뜻이 다르다** — `clock.py` 의 `SEOUL` 주석 그대로
#:   *"UTC+9"* 와 *"서울"* 은 다른 말이다. ML 예측 시각을 적재 · 표시하는 두 자리가 쓴다.
#:   `SEOUL` 로 바꿀지는 ML 쪽을 정리할 때 정한다 — 시계를 옮기면서 시간대의 뜻까지
#:   바꾸지 않았다.
#: ★ 2026-09-29 재구성 BL-017 판단: **고정 오프셋 그대로 둔다.** 적재가 채우는 «기준일 06:00
#:   KST» 와 답에 적는 «… KST» 는 ML 파이프라인의 표기 규약이고, 1988 년 뒤로는 값도 같아
#:   바꿀 까닭이 없다. 대신 두 벌(`qa_graph.py` · `repository.py`)을 `ml/config.py::KST`
#:   한 자리로 모았다 — 적재(`ml/domain/forecast_calendar.py`)와 답(`ml/readmodel/qa_answer.py`)
#:   이 그 값을 가져간다.
#:
#: 🔴 **목록이지 면제가 아니다.** 여기 없는 자리가 시간대를 새로 만들면 아래 검사가 운다.
#:   물류 출고 · 과거 조회가 따로 들던 `ZoneInfo("Asia/Seoul")` 은 같은 날 `SEOUL` 로 옮겼다.
_FIXED_OFFSET_OWNERS = frozenset({"ml/config.py"})


def _clock_module_names(tree: ast.AST) -> set[str]:
    """그 파일에서 **시계 모듈을 가리키는 이름들.** `clock` 은 늘 넣는다.

    ```text
    from app.core import clock               → clock
    from app.core import clock as 시계        → 시계
    import app.core.clock                     → app.core.clock
    ```

    ⚠️ **별칭을 안 보면 별칭으로 샌다.** 이 저장소는 `from app.core import db as core_db`
      처럼 core 모듈에 별칭을 붙여 쓴다 — `core_clock.seoul_now` 가 생기는 날 `clock`
      이라는 이름만 보는 스캐너는 초록이다.
    """
    names = {"clock"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.core":
            names |= {alias.asname or alias.name for alias in node.names if alias.name == "clock"}
        elif isinstance(node, ast.Import):
            names |= {
                alias.asname or alias.name for alias in node.names if alias.name == _CLOCK_MODULE
            }
    return names


def _clock_readers_in(source: str) -> set[str]:
    """그 원문이 `clock` 에서 **가져다 쓰는 시계 이름들.** 두 모양을 다 본다.

    ```text
    from app.core.clock import today_in_seoul   → ImportFrom 으로 잡는다
    from app.core import clock ... clock.seoul_now  → Attribute 로 잡는다
    ```

    ⚠️ **한 모양만 보면 다른 모양으로 샌다.** 저장소에 둘 다 실제로 있다 —
      `backfill.py` 가 앞의 것이고 `scheduler.py` 가 뒤의 것이다.
    """
    tree = ast.parse(source)
    module_names = _clock_module_names(tree)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == _CLOCK_MODULE:
            found |= {alias.name for alias in node.names} & _CLOCK_READERS
        elif (
            isinstance(node, ast.Attribute)
            and node.attr in _CLOCK_READERS
            and ast.unparse(node.value) in module_names
        ):
            found.add(node.attr)
    return found


def _scan_clock_readers() -> dict[str, set[str]]:
    """`app/` 전체를 훑는다. 파일 경로 → 가져다 쓰는 시계 이름."""
    hits: dict[str, set[str]] = {}
    for path in sorted(_APP.rglob("*.py")):
        name = path.relative_to(_APP).as_posix()
        if name == _ALLOWED:
            continue  # 자기 안에서 자기를 쓰는 것은 새는 것이 아니다
        used = _clock_readers_in(path.read_text(encoding="utf-8"))
        if used:
            hits[name] = used
    return hits


def _clock_nodes(path: Path) -> list[tuple[ast.Call, str]]:
    """그 파일의 벽시계 호출 노드와 이름. `(Call, "datetime.now")`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[ast.Call, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in _WALL_CLOCK_ATTRS:
            continue
        receiver = ast.unparse(func.value)
        if receiver.split(".")[-1] not in _CLOCK_RECEIVERS:
            continue
        found.append((node, f"{receiver}.{func.attr}"))
    return found


def _wall_clock_calls(path: Path) -> set[str]:
    """그 파일이 벽시계를 읽는 이름들. `{"datetime.now"}`."""
    return {name for _, name in _clock_nodes(path)}


def _scan() -> dict[str, set[str]]:
    """`app/` 전체를 훑는다. 파일 경로 → 벽시계 호출 이름."""
    hits: dict[str, set[str]] = {}
    for path in sorted(_APP.rglob("*.py")):
        calls = _wall_clock_calls(path)
        if calls:
            hits[path.relative_to(_APP).as_posix()] = calls
    return hits


def _timezone_calls(path: Path) -> set[str]:
    """그 파일이 시간대를 만드는 호출. `{"ZoneInfo('Asia/Seoul')"}`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and ast.unparse(node.func).split(".")[-1] in ("ZoneInfo", "timezone")
    }


def test_스캐너가_clock의_벽시계를_실제로_찾는다():
    """🔴 **자기 생존 검사.** 이게 없으면 아래 검사는 0건을 세고도 초록이 된다.

    ★ 스캐너가 죽으면 *"벽시계가 하나도 없다"* 와 *"스캐너가 아무것도 못 본다"* 가
      **같은 결과**로 보인다. 둘을 가르는 것이 이 검사다.
    """
    calls = _wall_clock_calls(_APP / _ALLOWED)

    assert calls, (
        "스캐너가 clock.py 의 벽시계 호출을 못 찾았다 —"
        " 스캐너가 망가졌거나 clock.py 가 시계를 안 읽는다."
        " 어느 쪽이든 아래 검사는 공짜 초록이다"
    )
    assert "datetime.now" in calls, f"찾은 것이 벽시계가 아니다: {calls}"


def test_스캐너가_예외_자리도_실제로_찾는다():
    """🔴 **자기 생존 검사 두 번째.** 예외 목록이 실재하는 자리를 가리키는지 본다.

    ★ 예외 목록이 **없어진 자리**를 가리키면, 그 목록은 아무것도 안 봐 주면서
      *"봐 주고 있다"* 는 인상만 남긴다. 아래 검사는 그때도 통과한다.

    🟢 목록이 비어 있으면 **봐 줄 자리가 없다는 주장**이다. 그때는 빈 반복으로
      공짜 초록이 되지 않게, 스캐너가 실제로 예외 없는 상태를 보고 있는지 잰다.
    """
    if not _TIMESTAMP_ONLY:
        assert set(_scan()) <= {_ALLOWED}, (
            f"예외 목록이 비었는데 {_ALLOWED} 밖에서 벽시계를 읽는다: {_scan()}"
        )
        return

    for name, expected in _TIMESTAMP_ONLY.items():
        assert _wall_clock_calls(_APP / name) >= expected, (
            f"{name} 에 {expected} 가 없다 — 예외 목록이 유령을 가리킨다."
            " 그 자리가 사라졌으면 목록에서도 지워야 한다"
        )


def test_벽시계를_읽는_곳은_clock_하나뿐이다():
    """**`core/clock.py` 밖에서 벽시계를 읽으면 여기서 운다.**

    ⚠️ 고치는 법은 그 자리에서 `datetime.now()` 를 지우고 **`as_of` 를 인자로 받는
      것**이다. `clock.today_in_seoul()` 을 그 자리에서 부르는 것도 답이 아니다 —
      그러면 백테스트가 그 지점부터 오늘로 답한다. 시계를 읽는 것은 **진입점 하나**
      이고, 나머지는 받은 `as_of` 를 흘리기만 한다.

    🟡 `_TIMESTAMP_ONLY` 는 **날짜가 아니라 사건 시각**을 찍는 자리다. 목록에 없는
      새 자리가 생기면 여기서 운다 — 면제가 아니라 고정이다.
    """
    hits = _scan()
    others = {name: calls for name, calls in hits.items() if name != _ALLOWED}

    assert others == _TIMESTAMP_ONLY, (
        f"clock.py 밖에서 벽시계를 읽는다: {others}. 이 자리는 as_of 를 인자로 받아야 한다"
    )


def test_예외_자리는_날짜를_안_만든다():
    """🔴 **예외가 `as_of` 로 새지 않는지 따로 본다.**

    `ended_at` 은 지나간 일의 기록이라 이 규율의 대상이 아니지만, 같은 호출에
    `.date()` 를 붙이는 순간 **그것이 곧 "오늘이 며칠인가" 가 된다.** 목록에 있다는
    이유로 그 변신까지 통과시키면 예외가 구멍이 된다.
    """
    for name in _TIMESTAMP_ONLY:
        clock_calls = {ast.unparse(node) for node, _ in _clock_nodes(_APP / name)}
        tree = ast.parse((_APP / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # `<벽시계 호출>.date()` 를 찾는다 — 시각이 날짜로 바뀌는 유일한 모양이다.
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "date"
                and ast.unparse(node.func.value) in clock_calls
            ):
                raise AssertionError(
                    f"{name}:{node.lineno} 의 {ast.unparse(node)} 가 날짜를 만든다 —"
                    " 그것은 as_of 이고, as_of 의 주인은 clock.py 하나다"
                )


def test_스캐너가_시계를_가져가는_진입점을_실제로_찾는다():
    """🔴 **자기 생존 검사 세 번째.** 아래 둘이 공짜 초록이 되는 것을 막는다.

    ★ 이 스캐너가 죽으면 *"아무도 시계를 안 가져간다"* 와 *"스캐너가 아무것도 못
      본다"* 가 **같은 결과**로 보인다. 그래서 **있는 것이 잡히는지**부터 잰다 —
      두 임포트 모양이 저장소에 실제로 하나씩 있으므로 각각 그 파일에서 짚는다.
      별칭 모양은 저장소에 아직 없어서 원문 한 조각으로 잰다.
    """
    hits = _scan_clock_readers()

    assert hits, "시계를 가져가는 파일을 하나도 못 찾았다 — 스캐너가 고장 났다"
    assert "seoul_now" in hits.get("master/scheduler.py", set()), (
        f"`from app.core import clock` + `clock.seoul_now` 모양을 못 잡았다: {hits}"
    )
    assert "today_in_seoul" in hits.get("master/backfill.py", set()), (
        f"`from app.core.clock import today_in_seoul` 모양을 못 잡았다: {hits}"
    )
    assert _clock_readers_in(
        "from app.core import clock as 시계\nx = 시계.seoul_now()\n"
    ) == {"seoul_now"}, "`from app.core import clock as …` 별칭 모양을 못 잡았다"


def test_시계를_가져가는_곳은_진입점뿐이다():
    """**`clock` 의 시계 함수를 가져가도 되는 것은 진입점뿐이다.**

    🔴 위 `test_벽시계를_읽는_곳은_clock_하나뿐이다` 는 이것을 못 잡는다.
      `today_in_seoul()` 은 `datetime.now()` 가 아니라서 그 스캐너 밖이다 — 깊은
      자리가 `clock` 을 임포트하면 **저 검사는 초록인 채로 벽시계가 돌아온다.**

    ⚠️ 고치는 법은 그 자리에서 임포트를 지우고 **`as_of` 를 인자로 받는 것**이다.
      진입점이 정해서 넘기고, 아래로는 흐르기만 한다.
    """
    assert set(_scan_clock_readers()) == _CLOCK_READER_IMPORTERS, (
        f"진입점 아닌 곳이 clock 의 시계를 가져간다: {_scan_clock_readers()}."
        " 그 자리는 as_of 를 인자로 받아야 한다"
    )


def test_재검증은_시계를_안_가져간다():
    """🔴 **`#452` 이 고친 그 자리를 이름으로 못 박는다.**

    `revalidation.py` 는 `today()` 안에서 `today_in_seoul()` 을 불렀고, 부르는 곳이
    승인 경로 하나뿐이라 **아무 검사도 안 울었다.** 걷기가 정책으로 안을 고르기
    시작하면 `2026-03-10` 을 걷는 실행에서 재검증만 오늘로 답한다 — 곡선에 벽시계가
    섞이고, 성적이 조용히 무효가 된다.

    ★ 위 검사가 이미 이것을 덮지만, **이름을 적어 둔다.** 목록이 넓어지는 날
      `revalidation.py` 가 슬쩍 끼는 것과 진입점이 하나 느는 것은 다른 일이다.
    """
    assert "master/revalidation.py" not in _scan_clock_readers(), (
        "재검증이 clock 을 다시 가져간다 — as_of 는 진입점이 정해서 넘겨야 한다"
    )


def test_clock_말고는_ZoneInfo_로_시간대를_안_만든다():
    """★ 시간대의 주인도 하나다.

    🔴 `timezone(timedelta(hours=9))` 를 각자 들고 있으면 조용히 갈린다. 실제로
      `revalidation.py` 가 `_KST` 를 따로 들고 있었고(2026-09-08 정리), 값이 같아서
      아무도 못 봤다 — **같아서 못 본 것이지 안 갈릴 이유가 있던 것이 아니다.**

    🟡 ML 한 자리는 고정 오프셋이라 목록으로 적어 둔다(`_FIXED_OFFSET_OWNERS`). 그 자리가
      지역명 시간대를 따로 만들기 시작하면 그것도 여기서 운다.
    """
    owners = {
        path.relative_to(_APP).as_posix(): calls
        for path in sorted(_APP.rglob("*.py"))
        if (calls := _timezone_calls(path))
    }

    assert owners.get(_ALLOWED) == {"ZoneInfo('Asia/Seoul')"}, (
        f"스캐너가 clock.py 의 시간대를 못 찾았다 — 공짜 초록이 된다: {owners}"
    )
    assert set(owners) == {_ALLOWED} | _FIXED_OFFSET_OWNERS, (
        f"시간대를 만드는 곳이 clock.py 말고 또 있다: {owners}"
    )
    for name in _FIXED_OFFSET_OWNERS:
        assert owners[name] == {"timezone(timedelta(hours=9))"}, (
            f"{name} 가 고정 오프셋 말고 다른 시간대를 만든다: {owners[name]}"
        )
