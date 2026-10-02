"""inspection.py — 물류 점검 칸을 마스터가 부른다(#628).

```text
… → 입고 → 물류 점검 #1(AFTER_INBOUND) → 채권 → 수금 → [장부 관문]
  → 매입 판단 → 매입 승인 → 판매 판단 → 판매 승인 → 출고
  → 물류 점검 #2(AFTER_OUTBOUND) → 마감
```

이 칸이 있는 이유: 점검이 없으면 아무도 «안 팔리는 재고가 있다» 고 말하지 않는다.
실측: `SIM-WALK-2026-V4` 입고 39.5t 전량, `SIM-CHAIN-V8` 은 35.8t 중 9.2t 이 신선도
만료로 폐기됐고 그 사이 그 사실을 문제로 세운 행이 한 줄도 없었다.

업무 판단을 여기서 다시 적지 않는다. 무엇이 문제이고 무엇이 해소인지는 물류가 정한다
(`logistics/domain/monitoring.py` — 신선도 압박 · 용량 압박 · 중복 방지 · 해소 갈래).
이 파일이 지는 것은 커넥션 · 트랜잭션 · 어휘뿐이다. 신선도 식도 용량 식도 임계 비교도
여기 없다. `maintenance.py` 와 같은 모양이다 — 저쪽도 무엇을 버릴지 고르지 않고
트랜잭션과 사유·행위자 어휘만 진다.

트랜잭션의 주인이 이 파일이다. `detect_logistics_exceptions` 는 커밋도 롤백도 하지
않는다 — 그 약속의 반대편이 여기다. 한 점검이 한 커밋이고, Exception 마다 커밋하지
않는다. 한 번의 탐지가 «연 것 · 갱신한 것 · 닫은 것» 을 함께 내는데 중간에 커밋하면,
실패한 날 장부에 절반만 재탐지된 상태가 남는다.

실패 처리: 터져도 하루는 계속 간다. 예외를 값으로 옮긴다(`_stage` ·
`run_auto_maintenance` 와 같은 태도). 문제를 못 세운 것과 하루를 못 산 것은 다른
사실이다. 그래서 표(`logistics_exceptions`)가 없는 DB 에서도 걷기가 돈다 — 점검 두
칸이 `FAILED` 한 줄을 남기고 나머지 칸은 그대로 간다.

LLM 이 여기 없다. 걷기가 하는 것은 Observe · Assess · Detect · Resolve 결정론뿐이다
(상세설계 §17). 조사·제안은 사람이 부르는 별도 요청이고, 그래야 `--reset` 뒤 같은
걷기가 같은 결과를 낸다.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from datetime import date

from app.core import db as core_db
from app.logistics.schemas.monitoring import DetectOut, DetectPhase
from app.logistics.service.monitoring import detect_logistics_exceptions
from app.master.schemas.inspection import InspectionOut


def run_logistics_inspection(
    as_of: date,
    *,
    sim_run_id: str,
    phase: DetectPhase,
    borrow: core_db.Borrow | None = None,
    detect_fn: Callable[..., DetectOut] = detect_logistics_exceptions,
) -> InspectionOut:
    """그날 창고를 한 번 본다. 예외를 밖으로 내지 않는다.

    `receive_arrivals` · `run_auto_maintenance` 와 같은 모양이다 — `as_of` 를 받고,
    상태를 값으로 돌려주고, 하루의 진행을 자기 성공에 걸지 않는다.

    무엇이 문제인지 여기서 고르지 않는다. 탐지기도 임계도 `detect_fn` 의 것이다.

    커밋이 여기 있다. 물류가 하지 않는 그 일이다. 실패하면 롤백하고 `FAILED` 로
    돌아선다 — 절반만 재탐지된 장부를 남기지 않는다.

    :param sim_run_id: 어느 실행의 창고인가. 이번 실행 축으로만 돈다.
    :param phase: 하루의 어느 자리인가. 닫는 것은 `AFTER_OUTBOUND` 뿐이다 — 그 규칙의
        주인은 물류이고 여기서는 받은 값을 흘려보낸다.
    :param detect_fn: 물류 경계. 기본값이 실제 함수 자체다 — `None` 을 받지 않는다
        (`core/clock.py` · `maintenance.py` 와 같은 규율).
    """
    open_connection = core_db.connection if borrow is None else borrow
    with ExitStack() as stack:
        try:
            conn = stack.enter_context(open_connection())
        except Exception as exc:  # noqa: BLE001 - 연결 실패가 그날을 통째로 세우면 안 된다.
            return InspectionOut(
                as_of=as_of, phase=phase, status="FAILED", reason=f"연결 실패: {exc}"
            )

        try:
            result = detect_fn(conn, sim_run_id=sim_run_id, as_of=as_of, phase=phase)
        except Exception as exc:  # noqa: BLE001 - 점검이 터져도 하루는 계속 간다.
            conn.rollback()
            return InspectionOut(
                as_of=as_of,
                phase=phase,
                status="FAILED",
                reason=f"물류 점검이 터졌다: {type(exc).__name__}: {exc}",
            )

        # 한 점검이 한 커밋이다. 연 것과 닫은 것이 같은 재탐지의 두 면이라
        # 나눠 적으면 그 사이에 끊긴 날의 장부를 아무도 설명할 수 없다.
        conn.commit()
        return InspectionOut(
            as_of=as_of,
            phase=phase,
            status=result.status,
            reason=f"{result.reason}{_불확실(result)}",
            result=result,
        )


def _불확실(result: DetectOut) -> str:
    """못 본 것을 사유에서 지우지 않는다. 없으면 빈 문자열이다.

    "확인했고 문제 없음" 과 "기준이 없어 못 쟀다" 가 요약에서 같아 보이면, 정책
    미등재가 안전 신호로 둔갑한다.
    """
    if not result.uncertainties:
        return ""
    return f" · 미확인 {list(result.uncertainties)}"
