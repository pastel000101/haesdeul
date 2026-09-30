"""하루 넘김(개장) 등록소 — 개장 파트 Protocol · 등록 · 조회.

★ 2026-09-30 재구성 BL-018: `master/day_open.py` 에서 옮겼다 — `DayOpenPart`, `PARTS`, `DayOpening`,
  `_OPENINGS`, `register_day_opening`, `registered`, `missing`, `reset`.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any, Literal, Protocol

DayOpenPart = Literal["finance", "logistics"]

#: 하루 넘김에 참여하는 파트와 **호출 순서**. 사유 문장을 실행마다 같게 만든다.
#:
#: ★ `transition.PARTS` 와 달리 **순서에 의존성이 없다.** 두 파트는 서로 다른 표의
#:   서로 다른 행을 만들고 FK 로 엮이지 않는다 (C.1 — 파트마다 따로 걷는다).
#:   그래도 순서를 고정하는 이유는 사유·응답 문장이 실행마다 같아야 하기 때문이다.
PARTS: tuple[DayOpenPart, ...] = ("finance", "logistics")


class DayOpening(Protocol):
    """그날 상태 행을 보장한다. **파트가 소유한다.**

    ★ **한 메서드가 아니라 둘인 이유.** `open_day(conn, as_of) -> bool` 한 메서드로는
      마스터가 **어디서부터 채울지를 모른다.** 마지막으로 열린 날이 어디인지 물어볼
      자리가 없으면, 마스터는 as_of 하루만 만들거나 아니면 무한정 뒤로 걸어야 한다.
      앞은 구멍을 남기고 뒤는 상한을 못 건다.

      ```text
      is_open     그날 행이 이미 있는가 — 마스터가 달력을 걷는 데 쓴다
      open_day    carry_from 날 행을 물려받아 as_of 날 행을 만든다
      ```

    🔴 **`commit` 하지 않는다. 자기 커넥션을 열지 않는다.** 커넥션은 마스터가 주고
       커밋은 두 파트가 모두 끝난 뒤 `open_day` 가 한 번 한다.

    ★ **`build` / `persist` 로는 안 나눈다.** `transition.py` 의 두 Protocol 은 순수
      계산과 write 를 갈랐는데, 여기서는 그럴 수 없다.

      ```text
      apply_approval   build 가 순수 계산 — 약정만 있으면 된다. 그래서 나눌 수 있다
      open_day         "전날 행" 을 읽어야 계산이 된다 — DB 없이 못 한다
      ```

    🔴 **물류 구현자에게 — `in_transit` 을 물려받으면 `confirmed_inbound` 도 짝으로
       물려받아야 한다.**

       한쪽만 물려받으면 B-1(`tools.py` `find_in_transit_schedule_gap`)이
       `IN_TRANSIT_NOT_IN_CONFIRMED_SCHEDULE` 로 다음 날을 세운다.

       ★ **실측으로 겪은 자리다 (2026-09-04).** 승인 전이가 `in_transit` 만 채웠더니
         다음 날 물류가 경계를 못 냈고, `#275` 로 `confirmed_inbound` 를 병합해 풀었다.
         **하루 넘김에서 같은 일이 다시 난다.**

    :param as_of: 행이 설 날.
    :param carry_from: 물려받을 날. **언제나 `as_of` 바로 전날이다** — 마스터가 하루씩
        걸으며 구멍을 남기지 않는다.
    """

    def is_open(self, conn: Any, *, as_of: date) -> bool: ...
    def open_day(self, conn: Any, *, as_of: date, carry_from: date) -> None: ...


# ── 등록소 ──────────────────────────────────────────────────────────────
#
# `transition.py` 의 전이 등록소와 같은 결이다. 다른 점은 담는 것이다 — 저쪽은
# **승인이 장부를 바꾸는 방법**을 담고 여기는 **하루가 넘어가는 방법**을 담는다.
# 한 사전에 섞으면 전이가 없는 것과 하루 넘김이 없는 것이 같은 문장으로 나가고,
# 둘은 다른 사실이다.

_OPENINGS: dict[DayOpenPart, Any] = {}


def register_day_opening(part: DayOpenPart, impl: Any) -> None:
    """하루 넘김 구현을 등록한다. 재무·물류 모듈이 임포트 시점에 부른다.

    ⚠️ **오늘은 부르는 곳이 없다.** 재무는 미회신이고 물류는 파트 소유다.
    """
    if part not in PARTS:
        raise ValueError(f"하루 넘김 파트가 아니다: {part!r}. 가능: {', '.join(PARTS)}")
    _OPENINGS[part] = impl


def registered() -> Mapping[DayOpenPart, Any]:
    """지금 등록된 하루 넘김. **읽기용 사본**이다 — 밖에서 넣지 못하게 한다."""
    return dict(_OPENINGS)


def missing() -> tuple[str, ...]:
    """아직 하루 넘김 구현이 없는 파트. **`PARTS` 순서를 지킨다.**

    ★ 순서를 지키는 이유는 사유 문장 때문이다. 집합 순서로 적으면 같은 상황이
      실행마다 다른 문장으로 나가 로그를 비교할 수 없다.
    """
    return tuple(part for part in PARTS if part not in _OPENINGS)


def reset() -> None:
    """테스트 전용 — 등록을 비운다."""
    _OPENINGS.clear()
