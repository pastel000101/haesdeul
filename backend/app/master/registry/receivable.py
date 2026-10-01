"""채권 발행 등록소 — 채권 파트 Protocol · 등록 · 조회.

★ 2026-09-30 재구성 BL-018: `master/receivable.py` 에서 옮겼다 — `ReceivablePart`, `PARTS`,
  `ReceivableSource`, `_RECEIVABLES`, `register_receivable`, `registered`, `missing`, `reset`.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any, Literal, Protocol

from app.contracts.parts import ReceivablePartOut

ReceivablePart = Literal["finance"]

#: 채권을 세우는 파트.
#:
#: ★ **재무 하나다.** 판매는 확정 사실의 원천이지 채권 원장을 갖지 않고, 물류·매입은
#:   채권에 손대지 않는다.
#:
#: ⚠️ **하나짜리 등록소가 과한 것이 아니다.** 이것이 있어야 *"구현이 없다"* 와
#:    *"오늘 확정된 판매가 없다"* 를 가를 수 있다. 둘은 다른 사실이고, 뭉치면 재무
#:    구현체가 빠진 날 **조용히 아무 일도 안 일어난다.**
PARTS: tuple[ReceivablePart, ...] = ("finance",)


class ReceivableSource(Protocol):
    """그날 확정된 판매를 채권으로 세우는 방법. **재무가 소유한다.**

    🔴 **마스터는 `as_of` 만 준다.** 어느 판매가 대상이고 얼마짜리 채권이 되는지는
      판매·재무 사실이다 — `CollectionSource` · `InboundExecution` 과 같은 이유다.

    ★ **`conn` 은 받기만 한다.** commit·rollback·close 를 하지 않는다 — 트랜잭션
      경계는 마스터가 쥔다.

    🔴 **멱등이어야 한다.** 같은 날을 두 번 걸어도 채권이 하나여야 한다.
      `confirm_receivable` 이 `ON CONFLICT (sale_id) DO NOTHING` 으로 그렇게 적어
      뒀지만, **믿는 것과 검사로 박는 것은 다르다** — `tests/master/test_receivable.py`
      가 두 번 불러 잰다.

    🔴 **정본 축은 `(sim_run_id, financing_mode)` 다.**

      ⚠️ **이 Protocol 이 그 둘을 안 받는다.** *"어느 실행의 장부인가"* 는 실행
        정체성이라 **어댑터 생성 인자**로 온다 — `FinanceCollectionAdapter` ·
        `LogisticsInboundExecution` 과 같은 자리이고, 배선(`app/master/registry/bootstrap.py`)
        에서 눈에 보이게 주입한다.

    :param as_of: 판매 확정일. **`sales.sale_date` 와 맞춘다** — 달력일이다.
    :returns: 무엇이 채권으로 섰는지. 그날 확정 판매가 없으면 `NOTHING_DUE` 이고
        그것은 정상이다.
    """

    def issue(self, conn: Any, *, as_of: date) -> ReceivablePartOut: ...


# ── 등록소 ──────────────────────────────────────────────────────────────
#
# 🔴 **여섯 번째 등록소다.** 전이(승인이 장부를 바꾸는 방법) · 하루 넘김(하루가
#    넘어가는 방법) · 취소(승인을 물리는 방법) · 입고(도착분을 받는 방법) ·
#    수금(돈이 들어오는 방법) · 여기(판매 확정이 채권을 만드는 방법).
#
# ⚠️ **앞의 다섯 중 아무 데도 합치지 않는다.** 합치면 *"수금은 되는데 채권은 안 서는"*
#    상태를 표현할 수 없고, **지금이 정확히 그 상태다.**

_RECEIVABLES: dict[ReceivablePart, Any] = {}


def register_receivable(part: ReceivablePart, impl: Any) -> None:
    """채권 발행 구현을 등록한다. 배선(`bootstrap.py`)이 부른다."""
    if part not in PARTS:
        raise ValueError(f"채권 발행 파트가 아니다: {part!r}. 가능: {', '.join(PARTS)}")
    _RECEIVABLES[part] = impl


def registered() -> Mapping[ReceivablePart, Any]:
    """지금 등록된 채권 발행. **읽기용 사본**이다."""
    return dict(_RECEIVABLES)


def missing() -> tuple[str, ...]:
    """아직 채권 발행 구현이 없는 파트. **`PARTS` 순서를 지킨다.**"""
    return tuple(part for part in PARTS if part not in _RECEIVABLES)


def reset() -> None:
    """등록을 비운다. 검사용이다."""
    _RECEIVABLES.clear()
