"""하루 순서 검사가 **재지 않는 곁 단계**에 쓰는 대역.

★ 2026-10-01 재구성 BL-022 보완: 하루 순서(`run_scheduled_day`)를 부르는 검사 여럿이 물류 점검
  (`inspect_fn`)을 넘기지 않아, 점검이 늘 연결을 못 열고 «보려다 터졌다»(`FAILED`) 가지로 돌았다
  (2026-10-01 관찰 — 120건). 그 검사들이 재는 것은 다른 단계라, 점검은 «확인했고 손댈 것이
  없었다»(`NOTHING_DUE` — 점검의 정상 답)로 준다.

★ 점검 자체(자리 · 두 칸 · 터져도 마감까지 · 커밋 · 롤백)는 `test_logistics_inspection_stage.py` 가
  자기 대역으로 잰다. 이 파일은 그 검사를 대신하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.master.schemas.collection_seed import CollectionSeedOutcome
from app.master.schemas.inspection import InspectionOut


def collection_nothing_due(
    as_of: date, *, sim_run_id: str, **_kwargs: object
) -> CollectionSeedOutcome:
    """개장 뒤 수금 사건 만들기 — «그날 만들 수금 사건이 없었다»(`NOTHING_DUE`).

    ★ 2026-10-01 BL-022 보완: `open_day` 의 기본값(진짜 `seed_day`)은 재무 축을 DB 에서 읽는다 —
      넘기지 않은 개장 검사는 그 조회가 막혀 늘 «못 만들었다»(`UNREADABLE`) 가지로 돌았다. 수금 사건
      만들기 자체는 `test_collection_seed.py` 가 잰다.
    """
    return CollectionSeedOutcome(
        status="NOTHING_DUE", reason="검사 대역 — 이 검사는 수금 사건 만들기를 재지 않는다"
    )


@dataclass(frozen=True)
class SalesOutcome:
    """`SalesRunResponse` 의 최소 모양 — 하루 순서는 `end_code` 만 본다
    (`test_scheduler._SalesOut` 과 같은 모양)."""

    end_code: str


def sales_presented(request: object, verifier: object = None) -> SalesOutcome:
    """판매 판단 한 번 — «안을 냈다»(`SL1_PRESENTED`).

    ★ 2026-10-01 BL-022 보완: 기본값인 진짜 `run_sales` 는 **그 순간 등록소에 걸린 부서 어댑터**를
      부른다 — 전체 실행에서는 다른 검사가 들인 `app.main` 이 진짜 어댑터를 걸어 두어 판매 · 재무 ·
      물류 조회가 실 DB 쪽 실패로 새고, 파일 하나만 돌리면 다른 가지를 지났다. 판매를 재지 않는
      하루 순서 검사는 이 대역을 쓴다.
    """
    return SalesOutcome(end_code="SL1_PRESENTED")


def inspection_nothing_due(
    as_of: date, *, sim_run_id: str, phase: str, **_kwargs: object
) -> InspectionOut:
    """물류 점검 한 칸 — «확인했고 손댈 것이 없었다». 받은 날 · 칸을 그대로 싣는다."""
    return InspectionOut(
        as_of=as_of,
        phase=phase,
        status="NOTHING_DUE",
        reason="검사 대역 — 이 검사는 점검을 재지 않는다",
    )
