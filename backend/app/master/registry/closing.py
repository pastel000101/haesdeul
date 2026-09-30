"""마감 등록소 — 마감 파트 Protocol · 등록 · 조회.

★ 2026-09-30 재구성 BL-018: `master/closing.py` 에서 옮겼다 — `ClosingPart`, `PARTS`, `ClosingPort`,
  `_CLOSINGS`, `register_closing`, `registered`, `missing`, `reset`.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any, Literal, Protocol

from app.contracts.parts import ClosingPartOut

ClosingPart = Literal["finance"]

#: 하루를 닫는 파트.
#:
#: ★ **재무 하나다.** `daily_closings` 는 매입·판매·물류·현금·미수·재고를 **집계한**
#:   결과이고(DDL 주석), 그 집계의 주인은 재무 원장이다. 물류가 재고를 알고 매입이
#:   현금유출을 알지만, **그 셋을 한 줄로 만드는 것은 재무 계산**이다.
#:
#: ⚠️ **하나짜리 등록소가 과한 것이 아니다.** 이것이 있어야 *"마감 구현이 없다"* 와
#:    *"그날 닫을 움직임이 없었다"* 를 가를 수 있다. 둘은 다른 사실이고, 뭉치면
#:    구현이 안 붙은 채로 **매일 조용히 아무 일도 안 일어난다** — 지금이 그 상태다.
PARTS: tuple[ClosingPart, ...] = ("finance",)


class ClosingPort(Protocol):
    """그날을 닫는 방법. **재무가 소유한다.**

    🔴 **마스터는 `as_of` 와 `sim_run_id` 만 준다.** 그날 얼마가 나가고 얼마가
      들어왔는지, 잔액이 얼마인지는 **전부 재무 사실**이다 — `CollectionSource` ·
      `ReceivableSource` · `InboundExecution` 과 같은 이유다.

    ★ **그 둘이 곧 `daily_closings` 의 PK 다** (`(sim_run_id, close_date)`).
      마스터가 줄 것이 그 둘뿐인 이유가 그것이다 — 여기에 마스터가 지어낸 id 를
      더하면 같은 날이 두 벌 쌓인다.

    ★ **`conn` 은 받기만 한다.** commit·rollback·close 를 하지 않는다 — 트랜잭션
      경계는 마스터가 쥔다.

    🔴 **멱등이어야 한다.** 같은 날을 두 번 걸어도 마감 행이 하나여야 한다.
      표의 PK 가 그것을 받쳐 주지만, **믿는 것과 검사로 박는 것은 다르다** —
      `tests/master/test_closing.py` 가 하루를 두 번 걸어 잰다.

      ⚠️ 그리고 **마스터는 그 멱등에 기대지 않는다.** 파트마다 정확히 한 번만
        부른다 — 두 번 부르고 `ON CONFLICT` 가 받아 주기를 기대하는 것이
        `master_agent_runs` 에서 틀렸던 그 기대다.

    :param as_of: 닫을 날. **달력일이다** — 실행일 달력으로 밀지 않는다.
    :param sim_run_id: 어느 실행의 장부인가. **마스터가 정하고 흘려 준다.**
    :returns: 무엇이 닫혔는지. 그날 움직임이 없었으면 `NOTHING_DUE` 이고 정상이다.
    """

    def close(self, conn: Any, *, as_of: date, sim_run_id: str) -> ClosingPartOut: ...


# ── 등록소 ──────────────────────────────────────────────────────────────
#
# 🔴 **일곱 번째 등록소다.** 전이(승인이 장부를 바꾸는 방법) · 하루 넘김(하루가
#    넘어가는 방법) · 취소(승인을 물리는 방법) · 입고(도착분을 받는 방법) ·
#    수금(돈이 들어오는 방법) · 채권(판매 확정이 채권을 만드는 방법) ·
#    여기(**하루가 닫히는 방법**).
#
# ⚠️ **앞의 여섯 중 아무 데도 합치지 않는다.** 합치면 *"채권은 서는데 하루는 안 닫히는"*
#    상태를 표현할 수 없고, **지금이 정확히 그 상태다.**

_CLOSINGS: dict[ClosingPart, Any] = {}


def register_closing(part: ClosingPart, impl: Any) -> None:
    """마감 구현을 등록한다. 배선(`bootstrap.py`)이 부른다.

    🔴 **오늘은 아무도 이 함수를 안 부른다.** 재무 마감 어댑터가 아직 없어서다 —
      붙는 날 `bootstrap.wire_registries` 에 한 줄이면 된다. 그때까지
      `close_day` 는 매일 `NOTHING_DUE` + `missing=["finance"]` 를 낸다.
    """
    if part not in PARTS:
        raise ValueError(f"마감 파트가 아니다: {part!r}. 가능: {', '.join(PARTS)}")
    _CLOSINGS[part] = impl


def registered() -> Mapping[ClosingPart, Any]:
    """지금 등록된 마감 구현. **읽기용 사본**이다."""
    return dict(_CLOSINGS)


def missing() -> tuple[str, ...]:
    """아직 마감 구현이 없는 파트. **`PARTS` 순서를 지킨다.**"""
    return tuple(part for part in PARTS if part not in _CLOSINGS)


def reset() -> None:
    """등록을 비운다. 검사용이다."""
    _CLOSINGS.clear()
