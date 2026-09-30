"""승인 재검증의 판정 · 문장 — 조건 대조, 안 찾기, 부서 회신을 판정과 문장으로 접는다.

★ 2026-09-30 재구성 BL-018: `master/revalidation.py` 에서 옮겼다 — `REQUIRED_CAPABILITIES`,
  `_REVALIDATION_KEY_PREFIX`, `make_revalidation_request_id`, `PROCUREMENT_REVALIDATION_MODE`,
  `procurement_validation_payload`, `capabilities_for`, `verdict_of`, `conditions_of`,
  `conditions_of_original`, `find_scenario`, `_top_level`, `_candidate_scenarios`, `_labels_match`,
  `_VERDICT_PREFIX`, `_ADJUST_PREFIX`, `_VERDICT_LABEL`, `_AXIS_LABEL`, `_spoken`,
  `_spoken_verdict`, `_dept_label`, `_spoken_adjust`, `_what_from_fields`, `_grouped_amount`,
  `_grouped`, `_particle`, `revalidation_verdict`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from app.contracts.envelope import (
    PASSING_VERDICTS,
    AgentReply,
    Capability,
    Mode,
    route_capability,
    wire_payload,
)
from app.master.domain.answer import agent_label
from app.master.schemas.decision import RevalidationOutcome

REQUIRED_CAPABILITIES: tuple[Capability, ...] = (
    "SELLABLE_SUPPLY_CONTEXT",
    "FINANCIAL_VALIDATION",
)
"""재검증이 **반드시** 받아야 하는 검증 (설계 §1 · 2026-09-04 판매 합의).

```text
SELLABLE_SUPPLY_CONTEXT   아직 팔 수 있는 물건이 있는가
FINANCIAL_VALIDATION      아직 돈이 되는가
```

★ **후보가 요구했든 안 했든 부른다.** 후보의 `required_validations` 는 *"이 안을
  제시하려면 무엇이 필요한가"* 이고, 이 둘은 *"승인 직전에 무엇을 다시 봐야 하는가"*
  다 — 물음이 다르므로 목록도 다르다.

⚠️ **`REQUIRED_FOR_SALES`(어댑터 목록)와 다른 것이다.** 저쪽은 `(sales, finance)` 로
  **제안자**를 포함한다. 재검증은 후보를 다시 만들지 않으므로 제안자를 안 부른다 —
  부르면 사용자가 고른 안이 아닌 다른 안이 나온다.
"""


_REVALIDATION_KEY_PREFIX = "REV"
"""재검증 실행의 업무 키 접두. **`REQ` 와 다른 글자여야 한다.**

🔴 **`make_request_id` 를 그대로 쓰면 원 실행과 같은 키가 나온다.** 저쪽은
  `REQ-{날짜}-{순번}` 인데, 같은 날 첫 실행을 승인하면 `REQ-20260907-0001` 이 되어
  **재검증 키가 원 실행을 가리킨다.** 그러면 `revalidation_request_id` 가 무엇을
  가리키는지 아무도 못 푼다.

★ 조회와 매입이 같은 업무 키를 써서 `cycle` 로 갈라야 했던 자리
  (`persistence.record_status` 의 ⚠️)를 되풀이하지 않는다 — 여기는 애초에 안 겹치게
  둔다.
"""


def make_revalidation_request_id(sim_run_id: str, as_of: date, decision_seq: int) -> str:
    """`REV-SIM-WALK-2026-V4-20260907-0001`. **실행 축 + 날짜 + 결정 회차다.**

    ★ `make_request_id` 와 같은 규율이다 (§1.2-11) — 같은 날 재검증을 구분하되
      **재현 가능해야 한다.** 순번을 결정 회차로 두면 번복(`decision_seq` 2, 3 …)이
      각자 다른 키를 받고, 같은 승인을 두 번 처리해도 같은 키가 나온다.

    🔴 **축이 키에 실린다** (2026-09-11). 전에는 `REV-20260907-0001` 이라 **실행이
      달라도 같은 날 같은 회차면 키가 겹쳤다.** 실측에서 업무 키
      `REV-20260106-0001` 하나에 **여러 실행의 행 20건**이 쌓여 있었다 — 그 키로는
      *"어느 실행의 재검증인가"* 를 아무도 못 푼다.

    ★ **축을 첫 위치 인자로 둔다.** 인자가 하나 늘었으므로 옛 호출부가 조용히
      통과하지 않고 그 자리에서 터진다.

    ★ **자리는 머리 쪽(접두 바로 뒤)이다.** 고를 수 있었던 이유가 실측이다 —
      저장소 전체에 `REV-` 키의 **접두 조회도 꼬리 조회도 한 곳도 없다**
      (2026-09-11 전수 확인). 그래서 `run_repository.build_request_id` 의
      `{머리}-{실행}-{날짜}-{꼬리}` 와 **같은 모양**으로 맞췄다. 🔴 다음 사람이
      `REV-` 조회를 더한다면 이 자리가 이미 정해져 있다는 것을 먼저 보라.

    ⚠️ `master_decisions.revalidation_request_id` 는 `text` 라 길이 제한이 없다
      (2026-09-11 실 DB 확인) — 키가 길어져도 마이그레이션이 필요 없다.
    """
    return f"{_REVALIDATION_KEY_PREFIX}-{sim_run_id}-{as_of.strftime('%Y%m%d')}-{decision_seq:04d}"


PROCUREMENT_REVALIDATION_MODE: Mode = "SCENARIO_VALIDATION"
"""매입 안 재검증이 조언자에게 묻는 mode. **매입 Flow ④ 와 같은 물음이다** (`flow._validate`)."""


def procurement_validation_payload(
    proposal: Mapping[str, Any], scenario: Mapping[str, Any]
) -> dict[str, Any]:
    """조언자에게 보내는 **매입 제안 한 벌.** 제안 최상위 + 고른 안 하나다.

    ★ **두 부서가 이것을 `PurchaseProposal` 로 되살린다** (`finance/adapter._purchase_proposal`
      · `logistics/adapter._as_proposal`). 그래서 `meta` · `situation` · `confidence`
      같은 최상위 칸이 빠지면 판정 대신 입력 오류가 온다.

    🔴 **이름이 붙은 이유는 검사다** (2026-09-16). 실매입 기록값 사본이 이 모양으로
      안 갈 때 두 부서가 동시에 떨어졌는데, 검사가 이 조립을 손으로 다시 적으면
      **검사와 운영이 다른 모양을 볼 수 있다.** 주인을 하나 둔다.
    """
    return {**proposal, "scenarios": [dict(scenario)]}


# ---------------------------------------------------------------------------
# 무엇을 부를 것인가
# ---------------------------------------------------------------------------


def capabilities_for(scenario: Mapping[str, Any]) -> tuple[str, ...]:
    """이번에 물어볼 검증. **필수 둘이 먼저, 그다음 후보가 요구했던 나머지.**

    ★ **중복을 지운다.** 후보가 `FINANCIAL_VALIDATION` 을 요구했어도 필수로 이미
      들어 있으므로 한 번만 부른다 — 두 번 부르면 예산만 태우고 답은 같다.

    ★ **순서를 지킨다.** 판매가 적은 차례를 뒤에 그대로 붙인다
      (`sales_flow._required_validations` 와 같은 규율).
    """
    out: list[str] = list(REQUIRED_CAPABILITIES)
    raw = scenario.get("required_validations")
    if isinstance(raw, (str, bytes, Mapping)) or not isinstance(raw, Sequence):
        return tuple(out)
    for item in raw:
        if isinstance(item, str) and item not in out:
            out.append(item)
    return tuple(out)


def verdict_of(reply: AgentReply) -> dict[str, Any]:
    """회신 하나를 판정 칸에 담는 모양으로.

    ★ `sales_flow._verdict_of` 와 **무엇이 같고 무엇이 왜 다른지** (2026-09-11).

      ```text
      같다   agent · mode · business_status · runtime_status · payload
             · reasoning · missing_data
      다르다 run_id — sales_flow 에만 있다
      ```

      🔴 **`run_id` 를 따라 넣지 않는다.** 저쪽의 `run_id` 는 되먹임에 실을 때
        `SalesFlow.replies_by_ref` 에서 회신 원본을 찾는 **포인터**다. 재검증에는 그
        등록소가 없다 — 없는 것을 가리키는 포인터를 만들면 다음 사람이 그것을
        쓰려다 빈손이 된다.

    🔴 **`payload` 는 2026-09-11 에 열었다. 그전에는 버렸다.**

      ```text
      전  agent · mode · business_status · runtime_status · reasoning · missing_data
      후  + payload
      ```

      ★★ **두 파일이 서로를 가리키며 「같은 모양」이라고 적어 두었는데 두 칸이
        달랐다.** 이 문단의 옛 문장이 *"`sales_flow._verdict_of` 와 같은 모양이다"*
        였고, 저쪽은 *"`revalidation._verdict_of` 가 이미 목록으로 적고 있어 두
        경로가 같아진다"* 였다 — **둘 다 상대를 근거로 대며 같다고 주장했다.**
        매입이 `#588` 에서 고친 것과 같은 병이다: 우리가 소유하지 않은 파일의 사실을
        베껴 와 근거로 삼았다.

      🔴 **실측된 피해.** 재검증이 받은 재무 회신의 `financial_summary` 가 여기서
        사라져, 확정이 기여이익을 못 찾아 `sales` 가 0행이었다. 통과한 안은 되먹임을
        안 받으므로(계약 `C-1`) 후보에도 그 값이 없었고, **고리가 닫혀 있었다.**

      ⚠️ `ExecutionPlan.record` 도 `reply.payload` 를 안 담는다. 그래서 이 칸을 열기
        전에는 회신 내용이 **어디에도** 안 남았다.

    ★ **`wire_payload` 로 편다** (#175 · `sales_flow` 와 같은 규율). payload 는
      마스터가 모양을 모르는 중첩 dict 라, 튜플이 하나라도 있으면 JSON 왕복 전후로
      같은 칸이 두 모양이 된다.

    ★ **조건 비교는 안 바뀐다.** `conditions_of` 는 `business_status` 하나만 보고,
      그 문서화 문자열이 *"판정은 닫힌 어휘 하나만 쓴다 — `reasoning` 은 설명이지
      조건이 아니라 넣지 않는다"* 고 적어 두었다. `payload` 도 같은 쪽이다.
      `tests/master/test_finance_margin_carried.py` 가 그것을 잠근다.
    """
    return {
        "agent": reply.agent,
        "mode": reply.mode,
        "business_status": reply.business_status,
        "runtime_status": reply.runtime_status,
        "payload": wire_payload(dict(reply.payload)),
        "reasoning": reply.reasoning,
        "missing_data": list(reply.missing_data),
    }


# ---------------------------------------------------------------------------
# 조건 비교 — 🔴 M-4 에서 가장 틀리기 쉬운 자리
# ---------------------------------------------------------------------------


def conditions_of(
    validations: Mapping[str, Mapping[str, Any]],
    adjustments: Sequence[Mapping[str, Any]],
) -> frozenset[str]:
    """그 안에 붙은 **조건 표지 집합.** 두 종류를 한 집합에 담는다.

    ```text
    verdict:{capability}={business_status}   그 검증이 `conditional` 로 답했다
    adjust:{표준형 JSON}                     부서가 낸 조정 제안 하나
    ```

    🔴 **`business_status == "conditional"` 만 보면 틀린다.** *"원래도 조건부였던 안이
      같은 조건으로 다시 통과한 것"* 과 *"새 조건이 생긴 것"* 은 다르다. 그래서 상태
      하나가 아니라 **집합**을 만들어 원 실행 것과 견준다 (설계 §3).

    ★ **비교 단위를 이렇게 고른 근거.**

      ```text
      판정(validations)   닫힌 어휘 하나만 쓴다 — (capability, business_status)
      조건(adjustments)   부서 표준형 **전부** 를 쓴다 — 마스터가 안에서 고르지 않는다
      ```

      판정은 `Verdict` 네 값으로 닫힌 어휘라 그 값 자체가 비교 단위가 된다. `reasoning`
      은 **설명**이지 조건이 아니라 넣지 않는다 — 넣으면 문장만 다듬어도 `CONDITIONAL`
      이 되어 사용자가 같은 안을 계속 다시 승인하게 된다.

      반대로 조정 제안은 `SuggestedAdjustment` **표준형이 곧 조건**이다. 그 안에서
      *"무엇이 중요한 칸인가"* 를 마스터가 고르면 그것이 판단이 된다 (§3.2.2) — 골라
      담지 않고 `wire_adjustment` 가 편 것을 통째로 표지로 쓴다. `reason` 문장까지
      들어가므로, 같은 숫자에 다른 문장이면 **조건이 바뀐 것으로 본다.**

    ⚠️ **판매가 조건을 어떤 모양으로 내는지 아직 확인 전이다** (설계 §6-①). 그래서
      **보수적으로 기울였다** — 비교 단위가 확실히 같지 않으면 `CONDITIONAL` 이다.
      통과 쪽으로 기울면 *사용자가 본 적 없는 조건이 사용자 승인으로 기록된다.*
    """
    out: set[str] = set()
    for capability, verdict in validations.items():
        business = str(verdict.get("business_status") or "")
        if business != "ok":
            # `ok` 만 "조건 없음" 이다. `conditional` 은 물론 `reject` · `skipped` 도
            # 표지로 남긴다 — 판정은 아래 `revalidation_verdict` 가 따로 하고, 여기는 **원 실행보다
            # 나빠졌는가**만 잰다.
            out.add(f"verdict:{capability}={business}")
    for adjustment in adjustments:
        out.add(f"adjust:{json.dumps(adjustment, sort_keys=True, ensure_ascii=False)}")
    return frozenset(out)


def conditions_of_original(
    response_payload: Mapping[str, Any], scenario_label: str
) -> frozenset[str]:
    """원 실행에서 **그 후보에 붙어 있던** 조건 표지 집합.

    ```text
    판정   candidates[].validations   판매 응답
           verdicts                   매입 응답 (후보가 없을 때만 본다)
    조건   adjustments[]              🔴 그 안의 라벨을 밝힌 것만 센다
    ```

    🔴 **라벨을 안 밝힌 조정은 그 안의 조건으로 세지 않는다.** `scenario_labels` 는
      *"비어 있어도 된다 — 안 채운 것과 해당 없는 것을 여기서 가르지 않는다"*
      (`SuggestedAdjustment`). 뜻이 둘인 값을 **있는 쪽으로 읽으면** 원 조건 집합이
      커지고, 커진 만큼 재검증 결과가 `PASSED` 로 접힌다.

      **그 방향으로는 기울이지 않는다.** 안 센다 → 원 조건 집합이 작다 →
      `CONDITIONAL` 쪽으로 기운다 → 사용자에게 되돌아간다. 되돌아가는 것은 되돌릴 수
      있지만, 없던 조건이 승인으로 기록되는 것은 되돌릴 수 없다.

    ⚠️ **재검증 쪽은 라벨로 거르지 않는다** (`revalidate_scenario`). 그쪽은 후보 하나만
      돌리므로 나온 조정이 전부 그 안의 것이다. 이 비대칭도 보수적인 방향이다 — 재검증
      집합이 더 크게 잡히므로 `PASSED` 로 접히기 어렵다.

    ★ **못 읽으면 빈 집합이다.** 판정을 실은 칸이 아예 없는 모양이면 원 조건을 0 으로
      두고, 그러면 재검증에 조건이 하나라도 있는 순간 `CONDITIONAL` 이다.
      *"모르면 통과"* 가 아니라 *"모르면 되돌린다"* 로 둔다.

    🔴 **매입 응답의 판정은 최상위 `verdicts` 에 있다** (2026-09-16). 전에는 이 함수가
      `candidates[].validations` **하나만** 읽어서, 매입에서는 원 조건 집합이 **항상 빈
      집합**이었다. *"모르면 되돌린다"* 가 매입에서는 *"매번 되돌린다"* 가 된 것이다.

      실측 (`dev@983c85b` · 실행 `SIM-CHECK-HOLIDAY-0916` · 2026-04-13 배추):

      ```text
      원 실행  verdicts.inventory.business_status = "conditional"   (ZONE_CAPACITY_UNRESOLVED)
      재검증   validations.inventory.business_status = "conditional"  ← 원 실행과 똑같다
      결과     added = {"verdict:inventory=conditional"} → CONDITIONAL
      ```

      **그 조건은 원 실행에도 똑같이 있었다.** 재고 판정은 구조적으로 매일
      `conditional` 이라, `POST /master/runs/{id}/purchase-record` 가 기록값이 선정안과
      하나라도 다르면 **항상 422** 로 막혔다 (`_revalidate_or_reject` 는 `PASSED` 만
      받는다).

    ⚠️ **매입의 `verdicts` 는 안 라벨로 거르지 않는다.** 그 칸은 안별로 갈라져 있지 않고
      **그 실행 전체의 판정**이다. 사람이 승인할 때 화면에서 본 것이 바로 그 판정이라,
      그대로 견주는 것이 맞다. 라벨로 거르면 셀 것이 하나도 안 남아 고치기 전과 같아진다.
    """
    candidates = [
        candidate
        for candidate in response_payload.get("candidates") or ()
        if isinstance(candidate, Mapping)
    ]
    validations: Mapping[str, Mapping[str, Any]] = {}
    for candidate in candidates:
        scenario = candidate.get("scenario")
        if not isinstance(scenario, Mapping) or not _labels_match(scenario, scenario_label):
            continue
        raw = candidate.get("validations")
        if isinstance(raw, Mapping):
            validations = {k: v for k, v in raw.items() if isinstance(v, Mapping)}
        break

    if not candidates:
        # 매입 응답. 후보가 있는 응답(판매)에서는 이 칸을 보지 않는다 — 판매의 판정은
        # 후보마다 갈라져 있어, 최상위로 올라가면 다른 안의 조건까지 세게 된다.
        raw = response_payload.get("verdicts")
        if isinstance(raw, Mapping):
            validations = {k: v for k, v in raw.items() if isinstance(v, Mapping)}

    adjustments = [
        adjustment
        for adjustment in response_payload.get("adjustments") or ()
        if isinstance(adjustment, Mapping)
        and scenario_label in (adjustment.get("scenario_labels") or ())
    ]
    return conditions_of(validations, adjustments)


def find_scenario(
    response_payload: Mapping[str, Any], scenario_label: str
) -> Mapping[str, Any] | None:
    """사용자가 고른 **그 안 하나.** 두 응답 모양을 다 본다.

    ```text
    매입 응답   scenarios[]              label 로 찾는다
    판매 응답   candidates[].scenario    scenario_id 로 찾는다 (label 이 없다)
    ```

    ★ **`decision_service._scenarios_of` 와 다른 물음이다.** 저쪽은 *"약정을 만들
      매입 안"* 을 찾아 `build_commitment` 에 넘기므로 매입 응답 모양만 본다. 여기는
      *"오늘 다시 검증할 안"* 이라 판매 후보도 찾아야 한다 — 같은 함수로 묶으면 매입
      약정 조립이 판매 후보를 받는 날이 온다.

    🔴 **라벨이 겹치면 `None` 이다.** 첫 것을 조용히 고르면 **어느 안을 재검증했는지가
      운에 걸린다** (`_commitment_parts` 가 같은 이유로 그렇게 한다).

    ⚠️ **두 모양을 한 목록으로 합치지 않는다.** 합치면 같은 안이 두 칸에 다 실린 응답에서
      *"라벨이 둘"* 로 읽혀 재검증이 통째로 못 돈다. **`scenarios` 가 먼저다** —
      승인 검사(`check_scenario_exists`)가 보는 칸이 그쪽이라, 그 칸이 있으면 사용자가
      승인한 안은 정의상 거기 있는 것이다.
    """
    for scenarios in (_top_level(response_payload), _candidate_scenarios(response_payload)):
        matches = [s for s in scenarios if _labels_match(s, scenario_label)]
        if len(matches) == 1:
            return matches[0]
        if matches:
            return None  # 그 칸 안에서 겹쳤다 — 뒤 칸으로 넘어가 다른 안을 고르지 않는다
    return None


def _top_level(response_payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """매입 응답의 안 목록 (`scenarios[]`)."""
    return [
        scenario
        for scenario in response_payload.get("scenarios") or ()
        if isinstance(scenario, Mapping)
    ]


def _candidate_scenarios(response_payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """판매 응답의 안 목록 (`candidates[].scenario`)."""
    out: list[Mapping[str, Any]] = []
    for candidate in response_payload.get("candidates") or ():
        if not isinstance(candidate, Mapping):
            continue
        scenario = candidate.get("scenario")
        if isinstance(scenario, Mapping):
            out.append(scenario)
    return out


def _labels_match(scenario: Mapping[str, Any], scenario_label: str) -> bool:
    """후보가 그 라벨의 것인가.

    ★ **두 칸을 다 본다.** 매입 응답의 안은 `label` 이고 판매 후보는 `scenario_id` 다
      (`app/sales/schemas/proposal.py` `SalesScenario`). 승인 요청이 실어 보내는 것은 한
      칸(`scenario_label`)뿐이라, 받는 쪽이 두 이름을 다 알아야 한다.
    """
    return scenario_label in {
        str(scenario.get("label") or ""),
        str(scenario.get("scenario_id") or ""),
    }


# ---------------------------------------------------------------------------
# 조건 표지를 사람 말로 — 🔴 **비교는 표지로, 문장은 사람 말로** (2026-09-16)
# ---------------------------------------------------------------------------
#
# 🔴 **실측된 피해** (2026-09-16 · 실 서버 · `POST /master/runs/{id}/purchase-record`
#   에 130kg → 50,000kg 를 넣음). 거부 사유가 이렇게 나갔다:
#
#   ```text
#   기록값으로 다시 검증했더니 통과하지 못해 기록하지 않았습니다 (CONDITIONAL):
#   통과했으나 원 실행에 없던 조건이 1건 붙었다:
#   adjust:{"axis": "quantity", "dept": "inventory", "reason": "수량을 7470kg 로 조정 제안",
#   "ref_ids": [...], "scenario_labels": ["기본"], "split_date": "2026-09-14",
#   "target_value": 7470.0, "unit": "kg"}
#   ```
#
#   **조정 제안 JSON 이 통째로 화면에 뜬다.** 그런데 그 JSON 안에 「수량을 7470kg 로
#   조정 제안」이라는 **부서가 쓴 좋은 한국어**가 이미 있다 — 꺼내 쓰면 된다.
#
# 🔴 **`conditions_of` 는 한 줄도 안 바꿨다.** 표지 집합이 비교의 정본이고, 그것을
#   건드리면 *"원래도 있던 조건"* 과 *"새로 붙은 조건"* 을 가르는 기준이 흔들린다.
#   여기서 바뀌는 것은 `revalidation_verdict` 가 만드는 **문장뿐**이고, 판정 값
#   (`PASSED`·`CONDITIONAL`·`FAILED`)과 `added` 집합은 그대로다.

_VERDICT_PREFIX = "verdict:"
_ADJUST_PREFIX = "adjust:"

#: 판정 어휘 한국어. **닫힌 어휘 넷**(`Verdict`)이고 `skipped` 는 표에 없다 —
#: `answer._VERDICT_LABEL` · `report._VERDICT_LABEL` 과 **같은 세 낱말**이다.
#:
#: ★ 두 곳 다 비공개라 임포트할 공개 주인이 없다. 어휘를 늘리지 않고 그 셋을 그대로
#:   쓰고, 표 밖은 `report._verdict_line` 과 같은 말(「판정 없음」)로 떨어뜨린다.
_VERDICT_LABEL: dict[str, str] = {"ok": "통과", "conditional": "조건부", "reject": "거절"}

#: 조정 축 한국어. 어휘의 주인은 `contracts.core.AdjustAxis` 이고, 한국어는 화면
#: `frontend/src/lib/vocab.ts` 의 `DEPT_AXIS_LABEL` 과 **같은 낱말**이다.
_AXIS_LABEL: dict[str, str] = {
    "quantity": "수량",
    "timing": "시점",
    "channel_mix": "채널 배분",
    "amount": "금액",
}


def _spoken(marker: str) -> str:
    """조건 표지 **하나**를 사람이 읽는 한 조각으로.

    ```text
    verdict:{capability}={business_status}   →  「재무 판정이 조건부」
    adjust:{부서 표준형 JSON}                 →  「물류: 수량을 7,470kg 로 조정 제안」
    ```

    🔴 **모르는 표지는 그대로 흘린다.** 여기서 지어내면 *"무엇이 조건이었나"* 가
      사라진다 — 지금 표지는 둘뿐이고, 셋째가 생기면 그 낱말이 그대로 화면에 뜬다.
    """
    if marker.startswith(_VERDICT_PREFIX):
        return _spoken_verdict(marker[len(_VERDICT_PREFIX) :])
    if marker.startswith(_ADJUST_PREFIX):
        return _spoken_adjust(marker[len(_ADJUST_PREFIX) :])
    return marker


def _spoken_verdict(body: str) -> str:
    """`{capability}={business_status}` 를 「{부서} 판정이 {상태}」로.

    ★ **capability 이름을 화면에 안 쓴다.** 재검증이 담는 키는 경로에 따라 두 어휘다 —
      판매 안은 capability(`FINANCIAL_VALIDATION`), 매입 안은 조언자 이름(`finance`).
      **둘 다 부서로 옮긴다**: capability 는 라우팅 표(`route_capability`)가 이미 부서를
      알고, 조언자 이름은 그 자체가 부서다. 여기서 표를 새로 적지 않는다.
    """
    name, _, status = body.partition("=")
    label = _VERDICT_LABEL.get(status)
    dept = _dept_label(name)
    # ⚠️ 여기 올 수 있는 상태는 사실상 `conditional` 하나다 — 그 밖은 위에서
    #   `FAILED` 로 떨어진다 (`PASSING_VERDICTS`). 그래도 표 밖을 말로 받는다.
    return f"{dept} 판정이 {label}" if label else f"{dept} 판정이 없다"


def _dept_label(name: str) -> str:
    """capability 든 조언자 이름이든 **부서 한국어**로. 모르면 받은 이름 그대로."""
    route = route_capability(name)
    return agent_label(route[0] if route is not None else name)


def _spoken_adjust(body: str) -> str:
    """부서 표준형 JSON 을 「{부서}: {무엇}」으로. **`dept` 와 `reason` 만 쓴다.**

    🔴 **`reason` 이 비면 지어내지 않는다.** `axis` · `target_value` · `unit` 로 짓고,
      셋 다 없으면 「부서가 대안을 냈다」까지만 말한다. 조정의 나머지 칸(`ref_ids` ·
      `split_date` · `scenario_labels`)은 **비교에는 쓰이지만 문장에는 안 나간다** —
      사람이 읽을 것이 아니다.

    ⚠️ **표지는 언제나 `json.dumps` 가 만든 것이다** (`conditions_of`). 그래도 못 읽는
      경우를 값으로 받는다 — 여기서 터지면 거부 사유 대신 스택이 화면에 뜬다.
    """
    try:
        raw = json.loads(body)
    except ValueError:
        raw = None
    adjustment: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}

    dept = agent_label(str(adjustment.get("dept") or "")) or "부서"
    reason = str(adjustment.get("reason") or "").strip()
    무엇 = _grouped(reason, adjustment) if reason else _what_from_fields(adjustment)
    if not 무엇:
        return f"{dept}{_particle(dept, '이', '가')} 대안을 냈다"
    return f"{dept}: {무엇}"


def _what_from_fields(adjustment: Mapping[str, Any]) -> str:
    """`reason` 이 빈 조정을 **칸으로만** 편다. 🔴 없는 값을 지어내지 않는다."""
    axis = _AXIS_LABEL.get(str(adjustment.get("axis") or ""), "")
    값 = _grouped_amount(adjustment.get("target_value"), str(adjustment.get("unit") or ""))
    if axis and 값:
        return f"{axis}{_particle(axis, '을', '를')} {값} 로 조정 제안"
    if axis:
        return f"{axis} 조정 제안"
    if 값:
        return f"{값} 로 조정 제안"
    return ""


def _grouped_amount(value: Any, unit: str) -> str:
    """수량·금액을 **자리를 끊어** 찍는다 (`report._kg` · `report._won` 과 같은 방식)."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return ""
    return f"{round(value):,}{unit}"


def _grouped(reason: str, adjustment: Mapping[str, Any]) -> str:
    """부서 문장 안 **그 숫자 하나만** 자리를 끊어 찍는다 (`7470kg` → `7,470kg`).

    🔴 **문장을 훑어 숫자를 고치지 않는다.** 훑으면 물류의 시점 조정 문장
      「도착일을 2026-09-14 로 조정 제안」의 연도가 `2,026` 이 된다. 그래서 **칸이
      말해 주는 값과 단위로 만든 토큰**(`{target_value:g}{unit}`)이 문장에 그대로
      있을 때만 바꾼다 — 시점 조정은 단위가 `d`(D+N)라 그 토큰이 문장에 없고,
      따라서 날짜는 손대지 않는다.

    ★ **못 찾으면 부서 문장 그대로다.** 부서가 표기를 바꾸는 날 이 자리는 조용히
      아무것도 안 한다 — 문장이 안 예뻐질 뿐 **틀린 숫자가 나가지 않는다.**
    """
    target = adjustment.get("target_value")
    unit = str(adjustment.get("unit") or "")
    if not isinstance(target, (int, float)) or isinstance(target, bool) or not unit:
        return reason
    토큰 = f"{float(target):g}{unit}"
    끊은_것 = f"{round(target):,}{unit}"
    if 토큰 == 끊은_것:
        return reason
    # 앞자리가 숫자면 다른 수의 꼬리를 자르는 것이라 안 바꾼다.
    return re.sub(rf"(?<!\d){re.escape(토큰)}", 끊은_것, reason)


def _particle(word: str, 받침: str, 모음: str) -> str:
    """받침 유무로 조사를 고른다 (`report._object_particle` 과 같은 규칙)."""
    if not word:
        return 받침
    code = ord(word[-1]) - 0xAC00
    if code < 0 or code > 11171:
        return 받침
    return 모음 if code % 28 == 0 else 받침


def revalidation_verdict(
    validations: Mapping[str, Mapping[str, Any]],
    unroutable: tuple[str, ...],
    adjustments: Sequence[Mapping[str, Any]],
    original_conditions: frozenset[str],
) -> tuple[RevalidationOutcome, str, tuple[str, ...]]:
    """네 값 중 무엇인가 (설계 §3).

    ```text
    FAILED       필수 중 하나라도 통과 어휘 밖이다
    CONDITIONAL  통과했으나 조건이 원 실행보다 늘었다
    PASSED       통과했고 조건이 같거나 줄었다
    ```

    🔴 **셋째 칸이 표지 원문이다** (2026-09-16 · `Revalidation.conditions`).
      **`reason` 문장이 접은 바로 그 표지**를 정렬 그대로 돌려준다 — 문장과 원문이
      같은 것을 가리켜야 한 행 안에서 서로를 풀 수 있다. 여기서 한 번 만든 것을
      두 곳이 나눠 쓰는 것이라, 부르는 쪽이 `conditions_of` 를 다시 돌리지 않는다.

    ★ **통과 판정은 허용목록으로 한다** (`PASSING_VERDICTS`). *"reject 가 아니면
      통과"* 로 정하면 봉투 어휘가 늘 때마다 새 값이 통과 쪽으로 샌다 (#173).
      그래서 `skipped`(판정을 안 낸 것)도 여기서는 못 통과한 것이다 — 필수 검증에서
      *"판정을 안 냈다"* 는 통과가 아니다.

    🔴 **`unroutable` 로는 `FAILED` 를 내지 않는다.** 지금 그 자리는
      `ADDITIONAL_SUPPLY_CONTEXT` 하나이고 **원 실행에서도 똑같이 못 불렀다** — 그
      사이 나빠진 것이 아니다 (설계 §5). 못 물어봤다는 사실은 결과에 그대로 실린다.
    """
    blocked = [
        f"{capability}({verdict.get('runtime_status')}/{verdict.get('business_status')})"
        for capability, verdict in validations.items()
        if str(verdict.get("business_status") or "") not in PASSING_VERDICTS
    ]
    if blocked:
        # ★ **여기는 조건을 비교하지도 않았다** — 표지가 없는 것이 사실 그대로다.
        return "FAILED", f"재검증에서 막혔다: {', '.join(blocked)}", ()

    now = conditions_of(validations, adjustments)
    added = sorted(now - original_conditions)
    if added:
        # 🔴 **비교는 표지로, 문장은 사람 말로** (2026-09-16). `added` 는 그대로 표지
        #   집합이고 세는 방식도 그대로다 — 펴는 것은 이 한 줄뿐이고, **편 것과 원문을
        #   나란히 돌려준다** (`Revalidation.conditions`).
        return (
            "CONDITIONAL",
            (
                f"통과했으나 원 실행에 없던 조건이 {len(added)}건 붙었다: "
                f"{'; '.join(_spoken(표지) for 표지 in added)}"
            ),
            tuple(added),
        )

    꼬리 = f" (못 물어본 요구: {', '.join(unroutable)})" if unroutable else ""
    return "PASSED", f"재검증 통과 — 조건이 원 실행보다 나빠지지 않았다{꼬리}", ()
