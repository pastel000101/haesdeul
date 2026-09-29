"""매입 탭이 읽는 저장된 실행 · 확정 매입 — **SELECT 뿐이다.**

★ 2026-09-29 재구성 BL-014: 화면 `api/purchase/query._read` 를 옮겼다(읽기 순서 · 날짜 창 ·
  돌려주는 모양 그대로). 화면은 재무 DB 입구를 더 부르지 않고 이 함수를 부른다. SQL 은
  `master/purchase_tab_repository.py`. 실행 고르기(`_pick`)와 표 · 문장 조립은 화면에 남았다
  (BL-019 에서 presenter 와 함께 정리).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.master.purchase_tab_repository import (
    read_approved_purchase_lines,
    read_arrival_runs,
    read_decisions,
    read_item_names,
    read_procurement_runs,
)

__all__ = ["read_purchase_tab"]


def read_purchase_tab(
    as_of: date, *, window_days: int | None = None, sim_run_id: str | None = None
) -> dict[str, Any]:
    """저장된 실행과 확정 매입을 읽는다. **SELECT 뿐이다.**

    🔴 **축(`sim_run_id`)으로 여기서 거르지 않는다.** 칸을 읽어 오기만 하고 고르는 것은
    화면의 ``_pick`` · ``_committed`` 가 한다. 이유 둘::

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
    runs = read_procurement_runs(as_of)
    buys = read_approved_purchase_lines(as_of)
    #  🔵 **그날 실행의 요청 ID 로 좁힌다** (2026-09-17). 전에는 조건이 없어 결정 표 전부
    #     (11,427행)를 읽었다. 읽은 결정을 쓰는 자리는 `build` 가 **그날 고른 실행의 요청**을
    #     찾는 것 하나이고, 그 요청은 전부 위 `runs` 안에 있다 — 그래서 결과가 같다.
    #  ★ 걷기 축으로 거르는 것이 아니다 — `runs` 는 모든 걷기의 행이다. docstring 의 이유 둘
    #    (건수를 센다 · 주입)은 여기 안 걸린다: 결정으로 세는 수가 없고, 주입 검사는 이 함수
    #    (화면이 부르는 `read_purchase_tab`)를 통째로 갈아 끼운다.
    request_ids = sorted({str(r["request_id"]) for r in runs if r["request_id"] is not None})
    decisions = read_decisions(request_ids)
    items = read_item_names()
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
    arrivals = read_arrival_runs(dates, sim_run_id) if dates else []
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
