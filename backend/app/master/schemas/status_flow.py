"""상태 조회 Flow 의 결과 어휘.

결과 모델 `StatusOutcome` 은 `domain/status_flow.py`, 실행은 `service/status_flow.py` 에 있다.
"""

from __future__ import annotations

from typing import Literal

#: 조회 결과 상태. 매입의 `EndCode` 와 섞지 않는다.
StatusCode = Literal[
    "S1_ANSWERED",  # 물어본 부서가 전부 답했다
    "S2_PARTIAL",  # 일부만 답했다 — 못 답한 부서를 밝힌다
    "S3_UNAVAILABLE",  # 아무도 못 답했다
]
