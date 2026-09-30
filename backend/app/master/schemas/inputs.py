"""마스터 입력 모델 — 출처 등급이 붙은 입력값과 입력 묶음.

★ 2026-09-30 재구성 BL-018: `master/inputs.py` 에서 옮겼다 — `Grade`, `REQUEST_GRADE`,
  `DEFAULT_GRADE`, `PROCUREMENT_TARGET_KIND`, `SALES_TARGET_KIND`, `SourcedInput`, `MasterInputs`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

#: 값 하나의 출처 등급. **리포트에 그대로 나간다.**
Grade = Literal[
    "MEASURED",  # 실제 운영 DB 에서 그대로 읽었다
    "DERIVED",  # 실제 DB 값에서 규칙으로 파생했다 — 원식을 함께 남긴다
    "MOCK",  # 🔴 mock 파일에서 왔다 — 한시 조치
    "MISSING",  # 못 구했다. 지어내지 않고 비운다
]

#: 🔴 **요청 본문이 직접 준 값**의 등급 (매입 실측 2026-09-07).
#:
#: `Grade` 에 넣지 않는다 — `Grade` 는 *"적재층이 어디서 읽었는가"* 이고, 이것은
#: **적재층을 아예 안 탔다**는 사실이라 같은 축이 아니다. `SourcedInput` 이 생기지도
#: 않는 자리라 등급을 붙일 대상이 없다.
#:
#: ★ 그래도 출처표(`input_sources`)에는 같은 `등급:소스` 모양으로 나간다 — 화면과
#:   부서 payload 가 한 표를 읽기 때문이다.
REQUEST_GRADE = "REQUEST"

#: 🔴 **요청이 안 줘서 마스터 기본값으로 떨어진 값**의 등급 (`#531` 후속 · 2026-09-10).
#:
#: `REQUEST_GRADE` 와 같은 이유로 `Grade` 에 안 넣는다 — 적재층을 아예 안 탔다.
#: 다른 점은 **누가 값을 정했나** 하나다.
#:
#: ```text
#: REQUEST:<key>   요청 본문이 줬다
#: DEFAULT:<이름>  요청이 안 줘서 **마스터가 자기 기본값을 썼다**
#: ```
#:
#: 🔴 **왜 적나.** 기본값을 조용히 두면 *"말 안 하고 번인에 쌓는"* 길이 그대로
#: 남는다. `sim_run_id` 가 정확히 그 자리다 — 안 주면 번인 상수로 앉는데, 그 사실이
#: 아무 데도 안 적히면 나중에 읽는 사람이 **누가 그 실행을 골랐는지** 알 수 없다.
#:
#: ★ **키를 빼는 것으로 대신하지 않는다.** 빼면 「없다」와 「모른다」가 섞인다 —
#:   `_input_sources` 가 봉투에 대해 이미 같은 결론을 냈다 (`MISSING:-`).
DEFAULT_GRADE = "DEFAULT"


# ── 시세 계열 ───────────────────────────────────────────────────────────
#
# 🔴 **매입과 판매가 같은 시세를 보고 있었다** (2026-09-11 · 걷기 실측).
#
#   `load_forecast` 하나를 두 경로가 같이 쓰는데 조회에 계열이 박혀 있어서
#   **경매가로 사서 경매가로 팔았다.** 완주한 걷기 `SIM-CHAIN-V3`(1~3월)에서
#   `SALES_MARGIN_BELOW_MINIMUM` 이 511건 중 483건이고, 팔린 일곱 건이 전부 무였다.
#
#   ```text
#   품목    AUC(경매)   WHSL(중도매)   차이     단위
#   배추      643        1,152        +79%    둘 다 원/kg
#   무        558          854        +53%
#   양파      834        1,022        +23%
#   ```
#
# ★ **어휘의 주인은 ML 이다** (`app.contracts.forecast.TargetKind` — `AUC` · `WHSL` · `RTL`).
#   마스터는 **고르기만 하고 새로 만들지 않는다.**
#
# ⚠️ **`RTL`(소매)은 안 쓴다.** 단위가 `원/단위` 이고 `unit_weight_kg` 가 전부 비어
#   있어 kg 로 못 바꾼다. 사람이 그 사실을 보고 중도매로 정했다.

#: 매입이 읽는 계열. **경매에서 산다.**
PROCUREMENT_TARGET_KIND = "AUC"

#: 판매가 읽는 계열. **중도매로 판다 — 고객이 김치공장이다.**
#:
#: 🔴 같은 값을 여기 말고 다른 데 적지 않는다. 리터럴을 흩뿌리면 **왜 그 값인지**가
#:   사라지고, 한 자리만 고친 날 매입과 판매가 다시 같은 시세를 본다.
SALES_TARGET_KIND = "WHSL"


@dataclass(frozen=True)
class SourcedInput:
    """값 + 출처. **둘을 떼어 놓지 않는다.**"""

    key: str
    payload: Any | None
    grade: Grade
    source: str
    note: str = ""

    @property
    def usable(self) -> bool:
        return self.payload is not None and self.grade != "MISSING"

    def line(self) -> str:
        tail = f" — {self.note}" if self.note else ""
        return f"{self.key} [{self.grade}] {self.source}{tail}"


@dataclass(frozen=True)
class MasterInputs:
    """한 실행이 실어 주는 것 전부."""

    forecast: SourcedInput
    confirmed_orders: SourcedInput
    policy_values: SourcedInput

    def all(self) -> tuple[SourcedInput, ...]:
        return (self.forecast, self.confirmed_orders, self.policy_values)

    def sources(self) -> dict[str, str]:
        """리포트·응답에 싣는 출처표."""
        return {s.key: f"{s.grade}:{s.source}" for s in self.all()}

    @property
    def mocked(self) -> tuple[str, ...]:
        """🔴 mock 에서 온 것. **감추지 않고 위로 올린다.**"""
        return tuple(s.key for s in self.all() if s.grade == "MOCK")
