"""승인 재검증 결과 모델.

★ 2026-09-30 재구성 BL-018: `master/revalidation.py` 에서 옮겼다 — `Revalidation`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.master.schemas.decision import RevalidationOutcome


@dataclass(frozen=True)
class Revalidation:
    """재검증 한 번의 결과. **결정 행에 그대로 실린다.**

    ★ **두 칸을 한 객체로 낸다.** `revalidation_request_id` 와
      `revalidation_outcome` 은 DB 의 짝 CHECK(`master_decisions_revalidation_pairing`)
      가 묶어 둔 한 사실이라, 따로 돌려주면 한쪽만 채워지는 날이 온다.
    """

    outcome: RevalidationOutcome

    #: 재검증이 **실제로 돈** 실행의 업무 키. 🔴 못 돌린 `ERROR` 에서는 `None` 이다 —
    #: 짝 CHECK 가 그 조합만 예외로 열어 두었고, **가짜 키를 지어 넣지 않기 위해서다.**
    request_id: str | None = None

    #: 사람이 읽는 한 줄. 부서가 쓴 문장을 옮기거나 못 돈 이유를 적는다.
    reason: str = ""

    #: 🔴 **원 실행에 없던 조건의 표지 원문** (`conditions_of` 가 만든 그대로).
    #:
    #: ★ **`reason` 과 다투는 칸이 아니다.** 둘은 묻는 사람이 다르다.
    #:
    #:   ```text
    #:   reason      사람이 읽는다     「물류: 수량을 7,470kg 로 조정 제안」
    #:   conditions  기계가 되만든다   adjust:{dept·axis·target_value·unit·…}
    #:   ```
    #:
    #: 🔴 **이 칸이 없으면 표지 원문이 영영 사라진다** (2026-09-16). 전에는 표지를
    #:   `reason` 문장에 이어 붙여 **그 문자열이 유일한 사본**이었는데, 그 문장을
    #:   사람 말로 고치는 순간 원문이 어디에도 안 남는다:
    #:
    #:   ```text
    #:   validations[cap]   `verdict_of` 가 담는 칸에 suggested_adjustments 가 없다
    #:                      (봉투에서 payload 의 **형제**라 payload 에도 안 들어온다)
    #:   plan(ExecutionStep) 조정 제안 칸 자체가 없다
    #:   master_decisions   revalidation_request_id · revalidation_outcome 뿐이다
    #:   로그                이 모듈에도 `runner` 에도 로거가 없다
    #:   ```
    #:
    #: 🔴 **발표 뒤 개발이 없다. 지금 안 남기면 영영 못 되만든다.**
    #:
    #: 🔴 **빈 자리가 아니다** — 30회 실행에 조정 45건이 실측됐다 (충환님 2026-09-16).
    #:   살아 있는 데이터를 사람 말로 덮는 것이라 잃으면 티가 난다.
    #:
    #: ★ **`verdict:` 표지도 같이 싣는다.** `validations[cap].business_status` 로
    #:   되만들 수는 있지만 **되만들 수 있다는 것과 남아 있다는 것은 다르다** —
    #:   되만드는 규칙(`conditions_of`)이 바뀌는 날 두 값이 갈린다.
    #:
    #: ★ **오늘 세 파트가 각자 세운 같은 선이다.** 매입은 「확정 입고 예정」을 `0` 이
    #:   아니라 `—` + `raw=None` 으로 냈고(`#740`), 재무는 운영비 축을 `None` =
    #:   「기록 없음」으로 두었다. **사람 말과 정본을 나란히 둔다** — 사람 말이
    #:   정본을 덮지 않는다.
    conditions: tuple[str, ...] = ()

    #: capability → 이번 호출의 판정. **원 실행 회신이 아니다** (S-1 금지).
    validations: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    #: 🔴 부를 대상이 없어 못 물어본 요구. 조용히 버리지 않는다 (§1.2-10).
    unroutable: tuple[str, ...] = ()
