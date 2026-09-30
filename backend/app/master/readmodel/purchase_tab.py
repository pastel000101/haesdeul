"""매입 탭이 읽는 저장된 실행 · 확정 매입 — **SELECT 뿐이다.**

★ 2026-09-29 재구성 BL-014: 화면 `api/purchase/query._read` 를 옮겼다(읽기 순서 · 날짜 창 ·
  돌려주는 모양 그대로). 화면은 재무 DB 입구를 더 부르지 않고 이 함수를 부른다. SQL 은
  `master/purchase_tab_repository.py`. 실행 고르기(`_pick`)와 표 · 문장 조립은 화면에 남았다
  (BL-019 에서 presenter 와 함께 정리).

★ 2026-09-30 재구성 BL-018: SQL 은 `repository/purchase_tab.py`(받은 연결). 조회 연결은 여기서
  조회마다 하나씩 빌린다 — 종전 `fetch_all` 헬퍼와 같은 횟수 · 순서다(도착일 조회는 날짜가 있을
  때만).

★ 2026-09-30 재구성 BL-019: 화면 `_pick` 의 **고르는 규칙**을 `pick_runs` 로 옮겼다(조건 · 순서
  그대로). 무엇을 몇 건 골랐고 몇 건을 왜 뺐는지 센 수를 돌려주고, 그 수로 안내 문장을 짓는 것은
  화면(`api/purchase/presenter.py`)이다. 연결을 쓰지 않는다 — 위 조회 결과를 받아 고르기만 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from app.contracts.core import ITEMS
from app.core import db as core_db
from app.core.settings import get_db_schema
from app.master.repository.purchase_tab import (
    select_approved_purchase_lines,
    select_arrival_runs,
    select_decisions,
    select_item_names,
    select_procurement_runs,
)

__all__ = ["PickedRuns", "pick_runs", "read_purchase_tab"]


def read_purchase_tab(
    as_of: date, *, window_days: int | None = None, sim_run_id: str | None = None
) -> dict[str, Any]:
    """저장된 실행과 확정 매입을 읽는다. **SELECT 뿐이다.**

    🔴 **축(`sim_run_id`)으로 여기서 거르지 않는다.** 칸을 읽어 오기만 하고 고르는 것은
    ``pick_runs``(이 파일 · 화면이 이 함수 **뒤에** 부른다) · 화면의 ``_committed`` 가 한다.
    이유 둘::

        ① 화면이 «전체 몇 건 중 이 걷기 몇 건» 을 말하려면 전체를 봐야 한다.
           WHERE 로 걸러 오면 뺀 수를 셀 수 없고, 그러면 조용히 없애는 것이 된다
        ② 검사가 이 함수를 대신 세워 상황을 주입한다. WHERE 에 두면 그 주입이
           필터를 건너뛰어 **축이 도는지를 못 잰다** (규칙 8)

    🔵 **예외 하나 — 도착일 조회(``arrivals``)만 축을 SQL 에 건다** (2026-09-17).
    ① 은 그 조회에 해당이 없다 — 건수를 안 세고 도착일을 찾아 오기만 한다. ② 는
    ``_arrival_index`` 의 파이썬 축 필터를 **그대로 두어** 지킨다. ``sim_run_id=None``
    이면 지금까지처럼 안 건다. 이유는 실측 — 그 조회가 모든 걷기의 시나리오를 끌어와
    (REH-0914 08-31 · 13,220행 · 45.6MB) 한 판 ``1,285ms`` 중 ``1,150ms`` 를 먹었고,
    축을 걸면 ``164ms`` 에 **응답 본문 sha 가 같다** (REH · FINAL · V13 세 실행).

    🔵 **``window_days`` 는 도착일 조회(``arrivals``)의 날짜 창이다** (2026-09-16).

    ``N`` 은 **``as_of`` 를 포함한 최근 N 일**이다 — ``0`` 이면 0일이라 **한 날도 안 읽고**,
    ``1`` 이면 그날 하루, ``None`` 이면 **안 좁힌다**(지금까지의 동작).

    ⚠️ **이 인자는 다른 넷(``runs`` · ``buys`` · ``decisions`` · ``items``)에 안 닿는다.**
    좁히는 것은 도착일을 맞추려고 **다시 훑는** 실행 표 하나뿐이다.

    🔴 **왜 생겼나.** 대시보드가 이 함수를 통째로 재사용하는데, 물류가 재 보니 그 왕복이
    약 600ms 로 대시보드 단일 최대였다 (2026-09-16 회신). 우리 머신 실측으로도
    ``arrivals`` 가 ``829.6ms`` · 7,700행이고 나머지 셋을 합쳐야 ``162.5ms`` 다.

    ★★ **그런데 대시보드는 그 결과를 안 읽는다.** ``pu.plans`` · ``pu.source`` 만 읽고,
    ``arrivals`` 가 먹이는 곳은 ``_committed`` 하나다. 그래서 대시보드가 넘길 값은
    «12일» 이 아니라 **``0``** 이다 — 좁히는 게 아니라 **안 읽는 것**이 맞다.

    🔴 **좁히면 그 사실을 화면이 말해야 한다** — ``arrivals_complete`` 를 같이 돌려준다.
    도착일을 못 채운 채 「입고 예정 0kg」 을 적으면 «확정된 0» 과 «안 읽었다» 가 한 값이
    된다 (규칙 3). 그 칸을 쓰는 자리는 ``build`` 다.
    """
    schema = get_db_schema()
    with core_db.read_connection() as conn:
        runs = select_procurement_runs(conn, as_of, schema=schema)
    schema = get_db_schema()
    with core_db.read_connection() as conn:
        buys = select_approved_purchase_lines(conn, as_of, schema=schema)
    #  🔵 **그날 실행의 요청 ID 로 좁힌다** (2026-09-17). 전에는 조건이 없어 결정 표 전부
    #     (11,427행)를 읽었다. 읽은 결정을 쓰는 자리는 `build` 가 **그날 고른 실행의 요청**을
    #     찾는 것 하나이고, 그 요청은 전부 위 `runs` 안에 있다 — 그래서 결과가 같다.
    #  ★ 걷기 축으로 거르는 것이 아니다 — `runs` 는 모든 걷기의 행이다. docstring 의 이유 둘
    #    (건수를 센다 · 주입)은 여기 안 걸린다: 결정으로 세는 수가 없고, 주입 검사는 이 함수
    #    (화면이 부르는 `read_purchase_tab`)를 통째로 갈아 끼운다.
    request_ids = sorted({str(r["request_id"]) for r in runs if r["request_id"] is not None})
    schema = get_db_schema()
    with core_db.read_connection() as conn:
        decisions = select_decisions(conn, request_ids, schema=schema)
    schema = get_db_schema()
    with core_db.read_connection() as conn:
        items = select_item_names(conn, schema=schema)
    #  확정 매입의 도착일은 원장에 없다. 그날 실행의 시나리오에서 **금액으로**
    #  맞춰 온다 — purchase_id 문자열을 쪼개면 이름 규칙에 묶인다.
    #  🔵 축을 주면 **그 축의 원장 날짜만** 건다. 도착일이 필요한 줄은 `_committed` 가
    #     남기는 그 축의 줄뿐이다 — 다른 걷기만 산 날을 걸면 끌어온 행이 전부 버려진다.
    all_dates = sorted({
        row["purchase_date"] for row in buys
        if sim_run_id is None or row["sim_run_id"] == sim_run_id
    })
    #  🔵 날짜 창. `None` 이면 안 좁힌다 — 그때 `dates is all_dates` 라 아래 조회가
    #     지금까지와 **한 글자도 다르지 않다.**
    #
    #  ★ `window_days=0` 에 분기를 따로 안 만든다. 창이 0이면 이 목록이 비고, 아래
    #    `if dates else []` 가 **이미** 조회를 건너뛴다. 「0 이면 아예 안 돈다」는
    #    새로 짓는 동작이 아니라 있던 단락이 하는 일이다.
    dates = all_dates if window_days is None else [
        d for d in all_dates if 0 <= (as_of - d).days < window_days
    ]
    arrivals = _arrival_runs(dates, sim_run_id) if dates else []
    return {
        "runs": runs,
        "buys": buys,
        "decisions": decisions,
        "items": {row["item_id"]: row["item_name"] for row in items},
        "arrivals": arrivals,
        #  🔴 **「전부 읽었나」를 값으로 돌려준다.** 창을 좁히면 도착일이 비는데, 그
        #     공란이 «맞출 것이 없었다» 인지 «안 읽었다» 인지 여기서만 알 수 있다.
        #     읽는 쪽(`build`)이 다시 계산하면 두 벌이 되고, 한쪽만 고치는 날이 온다.
        #  ⚠️ 「전부」는 **그 축의** 날짜다. 다른 걷기만 산 날을 안 건 것은 «안 읽었다» 가
        #     아니다 — 그 줄은 `_committed` 가 어차피 뺀다.
        "arrivals_complete": dates == all_dates,
    }


def _arrival_runs(dates: list[date], sim_run_id: str | None) -> list[dict[str, Any]]:
    schema = get_db_schema()
    with core_db.read_connection() as conn:
        return select_arrival_runs(conn, dates, sim_run_id, schema=schema)


@dataclass(frozen=True)
class PickedRuns:
    """매입 탭이 보일 실행(품목마다 하나)과 **몇 건 중에서 무엇을 뺐는지** 센 수.

    ★ 뺀 것을 조용히 없애지 않으려고 수를 같이 돌려준다 — 화면이 이 수로 안내 문장을 짓는다.
    """

    chosen: list[dict[str, Any]]
    total: int
    ready: int
    with_plans: int
    #: 안을 냈는데 계약 품목이 아니라 뺀 품목 이름(정렬 · 품목이 없으면 ``"None"``).
    dropped_items: list[str]
    #: 계약 품목이지만 다른 걷기(축)라 뺀 실행 수.
    off_axis: int
    #: 고른 것 중 축이 없는 실행 수 — 손으로 돌렸거나 축이 생기기 전 기록.
    outside: int


def pick_runs(runs: list[dict[str, Any]], sim_run_id: str | None = None) -> PickedRuns:
    """품목마다 **하나씩** 고르고, 몇 개 중 무엇을 골랐는지 같이 돌려준다 (매입 탭 🔴 레슨 ①).

    🔴 **같은 날 실행이 여럿이다.** `2026-01-06` 배추는 아홉이고 그중 넷이
    승인이다. 아무 말 없이 하나를 고르면, 다음 사람이 다른 행을 보고 «값이
    다르다» 고 한다. 그래서 규칙을 코드에 박고 화면에 적는다.

    ::

        ① runtime_status = 'READY'   — 미가동(E4)은 안을 못 낸 날이다
        ② scenarios 가 비지 않은 것
        ③ 🔴 item 이 계약 품목일 것 (contracts.core.ITEMS)
        ④ 🔴 sim_run_id 가 그 축일 것 — **안 주면 안 거른다**
        ⑤ 품목별 created_at 최신 하나

    🔴 **④ 가 ⑤ 앞이어야 한다.** 뒤로 가면 «최신 하나» 가 먼저 다른 걷기의 행을 집고
    그 뒤에 축으로 떨어뜨려, 같은 축에 있던 조금 오래된 행이 **같이 사라진다.**

    ⚠️ 지금 DB 에서는 축 있는 행이 언제나 더 새것이라(축이 `2026-09-08` 에 생겼다)
    순서를 바꿔도 값이 안 갈린다 — 그래서 **검사가 상황을 주입한다** (규칙 8).

    🔴 **축 이름을 쪼개 뜻을 읽지 않는다.** `SIM-WALK-202601-BASE` 의 `BASE` 는 사람이
    목록에서 고를 때 쓰는 꼬리표이고, 뜻은 `sim_runs` 행이 답한다 (마스터 통보
    2026-09-10). 여기서는 **같은지만** 본다.

    🔴 **③ 이 없으면 화면에 계약 밖 품목이 뜬다.** 저장된 실행에 피마늘 행이
    **194건** 남아 있다 (2026-09-09 실측 · 종전 주석의 143건은 그 뒤 늘었다)
    — `#216` 으로 계약에서 뺐지만 **기록은 일부러 안 고쳤다**
    (`e63f990` *"고쳐 쓰면 기록이 거짓이 된다"*). 기록을 고칠 자리가 아니라
    **보일 때 거를 자리**다. 계약이 그렇게 적어 두었다::

        contracts/core.py  ITEMS 각주
        제안 축   "사자고 제안한 품목"   ITEMS 로 거른다
        재고 축   "창고에 있는 품목"     자유 문자열 — 좁히지 않는다

    이 화면은 **제안 축**이다.

    ⚠️ 거른 것을 조용히 없애지 않는다 — 몇 건을 왜 뺐는지 돌려주는 값에 싣는다.
    """
    ready = [r for r in runs if r["runtime_status"] == "READY"]
    with_plans = [r for r in ready if (r["payload"] or {}).get("scenarios")]
    ours = [r for r in with_plans if r["item"] in ITEMS]
    dropped = sorted({str(r["item"]) for r in with_plans if r["item"] not in ITEMS})

    #  ④ 축. `is None` 이라야 한다 — 빈 문자열은 «안 줬다» 가 아니라 **잘못 준 것**이고,
    #     그것을 «전부» 로 읽으면 오타가 조용히 전체 조회가 된다.
    mine = ours if sim_run_id is None else [r for r in ours if r["sim_run_id"] == sim_run_id]

    picked: dict[str, dict[str, Any]] = {}
    for run in mine:  # 이미 created_at DESC 라 처음 만난 것이 최신이다
        picked.setdefault(str(run["item"]), run)
    chosen = list(picked.values())

    return PickedRuns(
        chosen=chosen,
        total=len(runs),
        ready=len(ready),
        with_plans=len(with_plans),
        dropped_items=dropped,
        off_axis=len(ours) - len(mine),
        outside=sum(1 for r in chosen if r["sim_run_id"] is None),
    )
