"""마스터 사용자 결정 — 스키마와 판단 규칙.

회의 미결정 12번("사용자 선택 이후 실제 실행 여부 기록")에 대한 답이다.

★ **LLM 이 없다.** 사람이 고른 것을 그대로 적는다. 해석할 것이 없다.

★ **`flow.py` 는 이 모듈을 임포트하지 않는다.**
  승인 게이트가 마스터가 부를 수 있는 툴 목록 안에 있으면 마스터가 스스로 통과시킬 수
  있다. 8/26 회의가 "승인 게이트를 툴 바깥에 두어 우회 불가하게" 로 정한 이유다.

★ **적재 실패를 삼키지 않는다.**
  `persistence.record` 는 실패를 삼킨다 — 이력이 없는 것보다 결과를 못 주는 것이 나쁘기
  때문이다. **결정은 반대다.** 안 남았는데 남았다고 하면 승인 없이 실행된 것과 같아진다.

★ 2026-09-30 재구성 BL-018: `master/decision.py` 에서 옮겼다. 역할이 다른 부분은 갈랐다 —
  `schemas/decision.py`; `schemas/purchase_record.py`. 무엇이 어디로 갔는지는 설계서 대응표
  `master/` 절. 함께 모은 것: `master/decision_service.py` 의 `end_code_of`, `cycle_of`,
  `policy_version_of`, `run_sim_run_id_of`, `commitment_parts`, `reject_repeat_approval`,
  `scenarios_labelled`, `item_of`, `as_of_of`, `lead_days_of`, `payment_days_of`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from app.contracts.commitment import ApprovedCommitment, CommitmentNotBuildable
from app.master.domain.commitment import build_commitment
from app.master.schemas.decision import (
    CommitmentOut,
    Decision,
    DecisionIn,
    DecisionOut,
    DecisionRejected,
)

#: 최종 승인 클릭 시점에 **그때 선택된 1안을 다시 검증한 결과** (2026-09-04 · 판매 합의).
#:
#:   ```text
#:   PASSED       재검증 통과. 승인 기록 · Write 진행
#:   CONDITIONAL  통과했으나 새 조건이 붙었다 → 승인 기록 안 함
#:   FAILED       재검증에서 막혔다           → 승인 기록 안 함, 실패 이력은 남긴다
#:   ERROR        재검증 자체를 못 돌렸다     → 승인 기록 안 함 (RUNTIME_NOT_READY 등)
#:   ```
#:
#: 🔴 **`CONDITIONAL` 은 통과가 아니다.** 사용자가 승인한 대상은 **그때 화면에 있던
#:   그 안**이다. 새 조건이 붙으면 그것은 다른 안이라, `PASSED` 로 접으면 *사용자가
#:   본 적 없는 조건이 사용자 승인으로 기록된다.*
#:
#: 🔴 **`Decision` 과 섞지 않는다.** 저쪽은 **사람이 무엇을 눌렀나**이고 이쪽은 **그
#:   뒤 재검증이 어떻게 됐나**다. `decision` 에 `REVALIDATION_FAILED` 같은 값을 더하면
#:   *"승인하려다 막혔다"* 가 *"승인하지 않았다"* 로 뭉개지고, `decision` 값으로
#:   판단하는 번복 규칙(`next_seq` · `mark_current`)까지 흔들린다.
#:
#: 🟢 **이제 실제로 돈다** (M-4 · 2026-09-07 · `revalidation.revalidate_scenario`).
#:   `decision == "APPROVE"` 일 때만 채워진다 — 나머지 셋은 승인이 아니라 재검증할
#:   대상이 없고, 그때의 `None` 은 **"재검증에 실패했다"가 아니라 "재검증을 하지
#:   않았다"** 이다. 2026-09-07 이전 결정 전부도 그 `None` 이다.
#:
#: 🔴 **`PASSED` 여도 아직 아무 일도 안 일어난다.** 승인의 효력을 도메인 Write 로
#:   흘리는 것은 M-5 다 — `decision` 은 **의도**의 칸이고 이쪽은 **결과**의 칸이며,
#:   효력은 또 다른 것이다.

#: 승인은 통과안이 있는 날에만 성립한다.
#:
#: ★ **취소도 같다.** 물릴 승인이 있으려면 그날 통과안이 있었어야 한다 — `E2_HELD` 인
#:   날에 "취소" 를 받으면 물릴 것이 없는데 이력에는 취소가 남는다.
_APPROVE_END_CODES: frozenset[str] = frozenset({"E1_APPROVED"})

#: 사람이 결정할 것이 있는 종료 코드.
#:
#: `E4_NOT_STARTED` 는 뺀다 — 부서가 못 돈 날은 **회사의 판단이 아니라 실행 환경 문제**라
#: 사람이 고를 것이 없다. 그날의 재시도는 결정이 아니라 새 요청이다.
_DECIDABLE_END_CODES: frozenset[str] = frozenset(
    {"E1_APPROVED", "E2_HELD", "E3_REJECTED", "E5_NO_FEASIBLE_PLAN"}
)

SALES_CYCLE = "SALES"
"""판매 사이클 실행 행의 `master_agent_runs.cycle` 값 (`persistence._SALES_CYCLE`).

🔴 **어느 어휘로 검사할지는 이 값이 정한다 — 요청 본문이 아니다** (2026-09-08 계약).
   본문으로 받으면 매입 실행에 `cycle="SALES"` 를 실어 보내 `SL1_PRESENTED` 어휘로
   검사받을 수 있고, 그 순간 승인 게이트가 **부르는 쪽 손에** 들어간다.
"""

PROCUREMENT_CYCLE = "PROCUREMENT"
"""매입 사이클 실행 행의 `master_agent_runs.cycle` 값 (`persistence._CYCLE`).

★ **`SALES_CYCLE` 옆이 이 값의 자리다.** 같은 칸의 같은 종류의 사실이라, 한쪽만
  이름을 갖고 다른 쪽은 호출부마다 문자열로 적혀 있던 것이 갈림의 씨앗이었다.

🔴 **매입 원장에 닿을 수 있는 승인은 이 사이클의 것뿐이다.** 판매 승인은
  `sales_approval` 이 `sales` 표로 흘리므로, *"승인됐는데 `purchases` 에 없다"* 가
  판매 행에는 **늘 참**이다 — 축을 안 가르면 미적용을 찾는 식이 판매 승인을
  영영 재시도한다.
"""

AUTO_BACKFILL = "AUTO-BACKFILL"
"""자동으로 채운 승인의 `decided_by`.

🔴 **사람 이름을 안 쓴다.** `master_decisions.decided_by` 는 지금 전부 사람 이름이라,
  자동으로 채우면서 거기 사람 이름을 적으면 **사람이 안 눌렀는데 눌렀다고 기록**되고
  그 표는 append-only 라 못 지운다.

★ `ask_service` 가 적어 둔 *"승인자가 없는 승인은 승인이 아니다"* 를 지키는 길이
  이것이다 — 자동일 때도 **「누가」를 정직하게** 적는다.

★ **자리를 `backfill.py` 에서 여기로 옮겼다** (2026-09-15 · 실매입 기록 안 A).
  이 값이 이제 *"승인이 전이를 바로 부르나"* 를 가르고, 그 판정을 `decision_service` ·
  `pending_transition` 이 읽는다. `backfill` 은 `decision_service` 를 들여오므로 거기
  두면 순환이 된다. `backfill` 은 여기서 들여와 그대로 쓴다 — 주인은 하나다.
"""


def awaits_purchase_record(decided_by: str | None) -> bool:
    """이 승인이 **실매입 기록을 기다리나** (설계 260915 안 A §2).

    ```text
    AUTO-BACKFILL   규칙 승인   승인 즉시 · 계획값으로 전이 (지금 그대로)
    그 밖           사람 승인   실매입 기록 뒤 · 기록값으로 전이
    ```

    🔴 **날짜로 가르지 않는다. 승인 경로로 가른다.** 09-10 이전 날짜라도 사람이
       콘솔로 승인하면 기록을 기다린다.

    ⚠️ **`decided_by` 가 없으면 사람으로 본다.** 계획값이 원장에 자동으로 앉는 쪽이
      기록을 기다리는 쪽보다 되돌리기 어렵다.
    """
    return decided_by != AUTO_BACKFILL

#: 판매 승인이 성립하는 종료 코드. 🔴 **`_APPROVE_END_CODES` 와 섞지 않는다.**
#:
#: `sales_flow.SalesEndCode` 가 적어 둔 D-3 합의가 그대로 여기에도 걸린다 —
#:
#:   > 매입 `EndCode`(E1~E5) 에 값을 더하지 않는다. 층이 다르다. 한 어휘에 두
#:   > 사이클을 담으면 `E2_HELD` 가 *"매입 보류"* 와 *"판매 보류"* 를 동시에 뜻하게 된다.
#:
#: ⚠️ 두 집합을 한 `frozenset` 으로 합치면 **매입 실행에 `SL1_PRESENTED` 를 우겨도
#:   통과한다.** 코드가 어느 층의 것인지를 집합이 더 이상 구분하지 못하기 때문이다.
_SALES_APPROVE_END_CODES: frozenset[str] = frozenset({"SL1_PRESENTED"})

#: 판매에서 사람이 결정할 것이 있는 종료 코드.
#:
#: `SL4_NOT_STARTED` 는 뺀다 — `E4_NOT_STARTED` 와 같은 이유다. 시작조차 못 한 날은
#: 회사의 판단이 아니라 실행 환경 문제라 사람이 고를 것이 없다.
#: ★ `SL6_VALIDATION_UNRESOLVED` 는 `SL3` 이 쪼개져 나온 자리라 **여기 있어야 한다** —
#:   빼면 예전에 결정을 받던 실행이 조용히 결정 불가가 된다. 승인은 여전히 막힌다:
#:   승인 어휘(`_SALES_APPROVE_END_CODES`)는 `SL1` 하나뿐이다.
_SALES_DECIDABLE_END_CODES: frozenset[str] = frozenset(
    {
        "SL1_PRESENTED",
        "SL2_NO_CANDIDATE",
        "SL3_ALL_REJECTED",
        "SL5_BUDGET_EXHAUSTED",
        "SL6_VALIDATION_UNRESOLVED",
    }
)


def approve_end_codes(cycle: str) -> frozenset[str]:
    """그 사이클에서 **승인이 성립하는** 종료 코드.

    🔴 **`cycle` 이 정한다.** 두 어휘를 따로 두는 이상, 어느 것을 볼지도 실행 행이
      정해야 한다 — 부르는 쪽이 정하면 어휘를 나눈 뜻이 없어진다.
    """
    return _SALES_APPROVE_END_CODES if cycle == SALES_CYCLE else _APPROVE_END_CODES


def decidable_end_codes(cycle: str) -> frozenset[str]:
    """그 사이클에서 **사람이 결정할 것이 있는** 종료 코드."""
    return _SALES_DECIDABLE_END_CODES if cycle == SALES_CYCLE else _DECIDABLE_END_CODES


# ── 판단 ────────────────────────────────────────────────────────────────


def scenario_labels_of(response_payload: Mapping[str, Any]) -> tuple[str, ...]:
    """그 실행이 실제로 내놓은 안의 label 목록.

    ★ 라벨을 열거로 박지 않고 **응답에서 읽는** 이유 — '보수·기본·공격' 은 매입의
      계약이다. 여기에 복제하면 매입이 라벨을 바꿀 때 조용히 어긋난다.
    """
    scenarios = response_payload.get("scenarios") or []
    out: list[str] = []
    for scenario in scenarios:
        if not isinstance(scenario, Mapping):
            continue
        label = scenario.get("label")
        if isinstance(label, str) and label:
            out.append(label)
    return tuple(out)


def scenario_ids_of(response_payload: Mapping[str, Any]) -> tuple[str, ...]:
    """판매 실행이 실제로 내놓은 후보의 `scenario_id` 목록.

    ```text
    매입 응답   scenarios[].label              "보수" · "기본"
    판매 응답   candidates[].scenario.scenario_id   "SALES-001-A-R1"  ← 여기
    ```

    ★ **`scenario_labels_of` 와 합치지 않는다.** 한 함수가 두 칸을 다 훑으면 매입
      실행에 판매 모양의 후보가 섞여 들어와도 그대로 통과한다 — 어느 층의 안을
      승인했는지가 응답 모양에 따라 갈린다.

    🔴 **탈락 후보도 목록에 든다.** 판매 응답은 `passed=False` 인 후보를 사유와 함께
      같이 내보내므로 (`SalesOutcome.rejected`), 여기서 거르면 *"제시되지 않은 안"*
      과 *"제시했으나 탈락한 안"* 이 같은 422 로 접힌다. 탈락안 승인을 막는 것은
      후보 판정(`CandidateVerdict.passed`)과 재검증이지 이 목록이 아니다.
    """
    return tuple(
        scenario_id
        for scenario in _sales_scenarios(response_payload)
        if isinstance(scenario_id := scenario.get("scenario_id"), str) and scenario_id
    )


def _sales_scenarios(response_payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    """판매 응답이 실은 후보의 `scenario` 칸들.

    ★ **`candidates[].scenario` 를 훑는 자리를 하나로 둔다.** 판매가 그 칸의 모양을
      바꾸는 날 고칠 곳이 하나여야 한다.
    """
    out: list[Mapping[str, Any]] = []
    for candidate in response_payload.get("candidates") or ():
        if not isinstance(candidate, Mapping):
            continue
        scenario = candidate.get("scenario")
        if isinstance(scenario, Mapping):
            out.append(scenario)
    return tuple(out)


def scenario_ids_of_type(
    response_payload: Mapping[str, Any], scenario_type: str
) -> tuple[str, ...]:
    """그 `scenario_type` 인 판매 후보의 `scenario_id` **전부.**

    ```text
    scenario_id     후보 Identity        "SALES-001-A-R1"
    scenario_type   후보의 의미          축 이름 하나            ← 이것으로 찾는다
    ```

    ★ **판매가 정한 계약이다** (2026-09-10). 판매 후보에는 `label` 이 없고, 같은
      뜻을 두 칸에 복제하지 않기로 했다 — 그래서 후보를 **의미로 찾고** 가리킬 때는
      **Identity 로 가리킨다.**

    🔴 **축 이름을 코드에 열거하지 않는다.** `scenario_labels_of` 가 적어 둔 규율
      그대로다 — 어느 축인지는 부르는 쪽이 말한다. 여기가 축 이름을 알면 판매가
      축을 바꾸는 날 조용히 어긋난다.

    🔴 **하나로 좁히지 않는다.** 몇 개인지가 부르는 쪽의 판단 재료다 — 여기서 첫
      번째를 돌려주면 **배열 순서가 선택 규칙**이 되고, 판매가 *"배열 순서 기반
      선택은 쓰지 않는다"* 고 명시한 것을 이 함수가 혼자 뒤집는다.
    """
    return tuple(
        scenario_id
        for scenario in _sales_scenarios(response_payload)
        if scenario.get("scenario_type") == scenario_type
        and isinstance(scenario_id := scenario.get("scenario_id"), str)
        and scenario_id
    )


def available_scenario_names(
    response_payload: Mapping[str, Any], cycle: str
) -> tuple[str, ...]:
    """그 실행이 내놓은 안을 **가리키는 이름** 전부.

    🔴 **어느 칸을 읽을지도 `cycle` 이 정한다.** 승인 검사와 승인 어휘가 같은 것을
      보고 있어야 *"승인은 되는데 안을 못 찾는다"* 가 안 생긴다.
    """
    if cycle == SALES_CYCLE:
        return scenario_ids_of(response_payload)
    return scenario_labels_of(response_payload)


def check_decidable(end_code: str, decision: Decision, *, cycle: str) -> None:
    """지금 상태에서 이 결정을 받을 수 있나.

    ★ `E4` 에는 아무 결정도 받지 않는다. 부서가 못 돈 날을 사람이 "승인" 하면
      **아무도 판단하지 않은 계획이 승인된 것으로 남는다.**

    🔴 **`cycle` 에 기본값을 두지 않는다.** 안 주면 터져야 한다 — 기본값은 곧
      업무 규칙이고, 여기서는 *"안 밝히면 매입으로 본다"* 가 조용한 규칙이 된다.
      부르는 쪽은 실행 행에서 읽은 값을 그대로 넘긴다.

    :param cycle: 그 **실행 이력 행**의 `cycle`. 🔴 요청 본문에서 오지 않는다.
    """
    decidable = decidable_end_codes(cycle)
    approvable = approve_end_codes(cycle)
    if end_code not in decidable:
        raise DecisionRejected(
            f"{end_code} 인 실행에는 결정을 받지 않는다 — "
            "부서가 못 돈 날은 사람이 고를 것이 없다. 재시도는 새 요청이다.",
            conflict=True,
        )
    if decision == "APPROVE" and end_code not in approvable:
        raise DecisionRejected(
            f"{end_code} 에는 승인할 안이 없다 "
            f"(통과안은 {', '.join(sorted(approvable))} 에만 있다).",
            conflict=True,
        )
    if decision == "CANCEL" and end_code not in approvable:
        # ★ **물릴 승인이 있으려면 그날 통과안이 있었어야 한다.** 없는 승인을 취소하면
        #   이력에는 취소가 남고 장부에는 아무 일도 안 일어난다 — 그 둘이 갈리면
        #   나중에 *"왜 취소했는데 그대로지"* 를 아무도 못 푼다.
        raise DecisionRejected(
            f"{end_code} 에는 물릴 승인이 없다 "
            f"(승인은 {', '.join(sorted(approvable))} 에만 선다).",
            conflict=True,
        )


def check_scenario_exists(
    scenario_label: str | None,
    available: Sequence[str],
) -> None:
    """고른 안이 그 실행에 실제로 있었나.

    ★ **이 검사가 이 모듈의 핵심이다.** 없는 안을 승인하면 이력에는 승인이 남고
      대조할 대상은 없다 — 나중에 "무엇을 승인했나" 에 답할 수 없다.
    """
    if scenario_label is None:
        return
    if scenario_label not in available:
        shown = ", ".join(available) if available else "(없음)"
        raise DecisionRejected(
            f"'{scenario_label}' 은 이 실행이 내놓은 안이 아니다. 제시된 안: {shown}",
        )


def next_seq(existing: Sequence[DecisionOut]) -> int:
    """번복은 덮어쓰지 않고 회차를 올린다."""
    return max((row.decision_seq for row in existing), default=0) + 1


def mark_current(rows: Sequence[DecisionOut]) -> list[DecisionOut]:
    """최신 회차 하나만 `is_current=True` 로.

    ★ DB 에 플래그를 두지 않는다. 플래그는 UPDATE 를 부르고, UPDATE 는 append-only 를
      깬다. 최대 회차에서 **파생**하면 이력이 그대로 남는다.
    """
    if not rows:
        return []
    top = max(row.decision_seq for row in rows)
    return [row.model_copy(update={"is_current": row.decision_seq == top}) for row in rows]


def current_decisions(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """업무 키마다 **지금 유효한 결정 하나** — 최대 `decision_seq` 행 (2026-09-17).

    🔴 **`mark_current` 와 같은 규칙이다.** 미적용 전이 조회
    (`master/repository/pending_transitions.py`)가 `DISTINCT ON (request_id) … ORDER BY
    decision_seq DESC` 로, 위 `mark_current` 가 요청 하나의 결정 목록에서 같은 일을 한다.
    이 함수는 여러 요청의 결정 행(매입 탭 조회 결과)을 한 번에 가른다. 결정 표는
    append-only 라 번복도 새 행이다.

    ⚠️ 전에는 순서를 안 봐서, 승인 뒤 「승인 되돌리기」(`REQUEST_CHANGE` · 안 이름 없음)를
    적어도 옛 `APPROVE` 행이 남아 **「승인됨」 · 「매입 기록됨」으로 떴다.** 실측 — FINAL-0918
    09-14 배추(`REQ-20260914-0001` · 05:50 승인 → 06:08 되돌림)가 「매입 기록됨」이었다.
    같은 요청에 안을 바꿔 여러 번 승인한 경우도 **전부** 「승인됨」이었다.

    ★ 되돌린 요청은 안 이름 붙은 유효 결정이 없으므로 「후보」 · 대기로 돌아간다 —
      낱말은 `master/domain/plan_state.py` 가 정한다.

    ⚠️ 검사 대역은 `decision_seq` 를 안 넣기도 한다 — 그때는 **목록 순서**를 회차로 읽는다
      (뒤에 온 행이 새것). 실 조회는 늘 그 칸을 싣는다.

    ★ 2026-09-30 재구성 BL-019: 매입 탭 화면(`api/purchase/query._current_decisions`)에서
      옮겼다 — 본문 그대로. 화면은 이 결과로 「승인됨」 · 대기를 가른다.
    """
    current: dict[Any, tuple[Any, Mapping[str, Any]]] = {}
    for index, row in enumerate(rows):
        seq = row.get("decision_seq")
        order = index if seq is None else seq
        kept = current.get(row["request_id"])
        if kept is None or order >= kept[0]:
            current[row["request_id"]] = (order, row)
    return [row for _order, row in current.values()]


def end_code_of(response_payload: dict[str, Any]) -> str:
    """실행 응답에서 종료 코드를 읽는다.

    ★ 행의 `runtime_status` 를 쓰지 않는다 — 그건 `E4` 만 구분하는 3값 어휘라
      `E2`(보류)·`E3`(반려)·`E5`(계획 없음)가 전부 `READY` 로 접혀 있다.
      결정 규칙은 다섯을 구분해야 한다.
    """
    end_code = response_payload.get("end_code")
    if not isinstance(end_code, str) or not end_code:
        raise DecisionRejected(
            "실행 이력에 종료 코드가 없다 — 결정을 걸 기준이 없다.", conflict=True
        )
    return end_code


def cycle_of(row: Mapping[str, Any]) -> str:
    """그 실행이 무슨 사이클이었나. **실행 이력 행이 정본이다.**

    ★ **응답 payload 의 모양으로 짐작하지 않는다.** `candidates` 가 있으면 판매라고
      읽으면, 매입 응답에 그 키가 섞이는 날 어휘가 조용히 갈린다. `cycle` 은 표의
      컬럼이고 CHECK 이 어휘를 잠근다 (`master_agent_runs.sql`).
    """
    cycle = row.get("cycle")
    return cycle if isinstance(cycle, str) else ""


def policy_version_of(row: Mapping[str, Any]) -> str | None:
    """원 실행이 돈 정책판. **실행 이력 행의 요청 원문에서 읽는다.**

    ★ **재검증이 새 정책판을 고르지 않는다.** `as_of` 는 고르는 날로 옮기지만 정책판까지
      바뀌면 *"그 사이 무엇이 바뀌었나"* 에 축이 둘 섞인다 — 재검증이 재는 것은
      **시장과 장부**이지 회사가 규칙을 바꿨는지가 아니다.
    """
    request_payload = row.get("request_payload")
    if not isinstance(request_payload, Mapping):
        return None
    value = request_payload.get("policy_version")
    return value if isinstance(value, str) and value.strip() else None


def run_sim_run_id_of(row: Mapping[str, Any]) -> str | None:
    """이 결정이 걸린 실행의 축. **실행 이력 행이 정본이다.**

    ★★ **`ledger.sim_run_id_for` 가 요구한 계약이 이것이다.** 그 함수는 *"마스터
      실행 이력이 `sim_run_id` 를 싣도록 계약을 세우고 이 함수가 그것을 읽어야
      한다"* 고 적어 두었고, `master_agent_runs.sim_run_id` 가 그 칸이다
      (`Refs #150` · 2026-09-08 · `run_repository._COLUMNS`).

    🔴 **없으면 메우지 않는다.** `BURN_IN_SIM_RUN_ID` 로 채우면 축이 안 실린 옛
       실행의 승인이 조용히 번인 장부에 앉고, 재무는 자기 축을 읽으므로 **채무와
       매입 원장이 서로 다른 실행에 앉는다.** `None` 을 그대로 흘리면 원장 계산이
       터지고 그 사실이 `TransitionOut.reason` 에 남는다.
    """
    value = row.get("sim_run_id")
    return value if isinstance(value, str) and value.strip() else None


# ★ `_commitment_for` 는 없앴다 (2026-09-11). 재조립 경로가 **객체도** 쓰게 되면서
#   (`current_approved_commitment`) 응답 모양만 떼어 주던 겉면이 할 일이 없어졌고,
#   `_current_approval_parts` 가 둘을 한 번에 낸다.


def commitment_parts(
    request_id: str,
    decision_seq: int,
    payload: DecisionIn,
    response_payload: Mapping[str, Any],
) -> tuple[CommitmentOut | None, ApprovedCommitment | None]:
    """승인이면 **확정 입고 약정**을 같이 낸다 (H1).

    ★ 응답 모양(`CommitmentOut`)과 **약정 객체**를 함께 돌려준다. 상태전이는 객체가
      있어야 걸리는데, 응답 모양에서 되만드는 것은 **같은 사실을 두 번 만드는 것**이라
      둘이 갈리는 날이 온다. 만든 자리에서 그대로 넘긴다.

    ★ **적재 뒤에 만든다.** 약정을 못 만들어도 결정은 남아야 한다 — 사람이 승인한
      것은 사실이고, 그 사실을 약정 조립 실패가 지우면 안 된다.

    ★ **못 만들면 이유를 싣는다.** `None` 을 조용히 돌려주면 물류가 *"입고 예정이
      없다"* 로 읽는다. 없는 것과 못 만든 것은 다르다 (§1.2-10).

    ★ **오케 `cycle.py` 를 부르지 않는다** (지시 2026-09-01). 같은 변환이 거기에도
      있지만 그 경로는 M-1 에서 안 돌고, 회차 수량을 합쳐 **품목을 없앤다.**
    """
    if payload.decision != "APPROVE":
        return None, None

    matches = scenarios_labelled(response_payload, payload.scenario_label)
    if not matches:
        return CommitmentOut(buildable=False, reason="승인한 안을 실행 응답에서 찾지 못했다."), None
    if len(matches) > 1:
        # 🔴 첫 것을 조용히 고르면 **어느 안을 약정했는지가 운에 걸린다** (자기 리뷰).
        return CommitmentOut(
            buildable=False,
            reason=f"라벨 '{payload.scenario_label}' 이 {len(matches)}개다 — 유일하지 않다.",
        ), None
    scenario = matches[0]

    as_of = as_of_of(response_payload)
    if as_of is None:
        return CommitmentOut(
            buildable=False, reason="실행 이력에 기준일이 없어 도착일을 걸 수 없다."
        ), None

    try:
        commitment = build_commitment(
            request_id=request_id,
            as_of=as_of,
            item=item_of(response_payload),
            scenario=scenario,
            inbound_lead_days=lead_days_of(response_payload),
            decision_seq=decision_seq,
            purchase_payment_days=payment_days_of(response_payload),
        )
    except CommitmentNotBuildable as exc:
        return CommitmentOut(buildable=False, reason=str(exc)), None
    return CommitmentOut.of(commitment), commitment


def reject_repeat_approval(existing: list[DecisionOut], payload: DecisionIn) -> None:
    """이미 승인이 서 있으면 **어느 안이든** 다시 승인하지 못한다.

    ★ 전에는 *"번복 자체는 막지 않는다 — '기본' 을 승인했다가 '보수' 로 바꾸는 것은
      정상적인 업무다"* 였고, **그때는 맞았다.** 승인이 이력에만 남고 장부를 바꾸지
      않던 때의 판단이다. 같은 안 재승인만 막으면 됐다 (버튼 두 번).

    🔴 **지금은 승인이 장부를 바꾼다.** 번복은 `decision_seq` 를 올리므로
      `purchase_id` 도 달라져 `ON CONFLICT` 가 안 걸린다 — 앞 승인이 만든
      `purchases` · `payables` · `unsettled` 가 **그대로 남고 뒤 승인이 얹힌다.**
      되돌리는 경로가 저장소에 없다 (`purchases.CANCELLED` 는 CHECK 에만 있고 쓰는
      코드가 0곳이다). 어제까지는 번복이 `finance_state_ambiguous` 로 다음 날을
      막아 우연히 드러났는데, #285 가 한 행에 누적하게 되면서 **다음 날이 정상으로
      서고 조용히 틀린다.**

    ★ **언제 푸나** — 취소 경로(`purchases.CANCELLED` 쓰기 · payable 역분개 ·
      `confirmed_inbound` 정리)가 생기면 이 조건을 도로 라벨 비교로 좁힌다. 그때까지는
      번복을 받는 것보다 막고 이유를 말하는 것이 낫다.

    ★ **`current.decision == "APPROVE"` 일 때만 막는다.** 첫 승인 · 거절 뒤 승인 ·
      조건부 재요청 뒤 승인은 앞선 장부가 없거나 이미 접혔으므로 그대로 열려 있다.
      이 조건을 지우면 사람이 거절한 뒤 아무것도 못 하게 된다.
    """
    if payload.decision != "APPROVE":
        return
    current = next((row for row in existing if row.is_current), None)
    if current is None or current.decision != "APPROVE":
        return
    raise DecisionRejected(
        f"'{payload.scenario_label}' 를 승인할 수 없다 — 이 실행에는 이미 승인된 안이 "
        f"있다 (회차 {current.decision_seq} · '{current.scenario_label}'). "
        "앞 승인이 만든 장부를 되돌리는 경로가 아직 없어, 번복하면 두 승인이 모두 "
        "장부에 남는다.",
        conflict=True,
    )


# ── 승인 → 약정 (H1) ────────────────────────────────────────────────────


def scenarios_labelled(
    response_payload: Mapping[str, Any], label: str | None
) -> list[Mapping[str, Any]]:
    """승인한 라벨의 안 **전부**. 라벨로 찾고, 유일성 판정은 호출자가 한다.

    ★ 첫 것을 돌려주는 함수였다가 목록으로 바꿨다 — 라벨이 겹치는 날 첫 것을
      조용히 고르면 검사도 이력도 그 선택을 모른다 (2026-09-01 자기 리뷰).
    """
    return [
        scenario
        for scenario in response_payload.get("scenarios") or ()
        if isinstance(scenario, Mapping) and scenario.get("label") == label
    ]


def item_of(response_payload: Mapping[str, Any]) -> str | None:
    """실행의 품목. 판정부 `meta.item` 이 정본이다 (매입 보증 2026-09-01)."""
    meta = (response_payload.get("judgment") or {}).get("meta") or {}
    item = meta.get("item")
    return item if isinstance(item, str) and item else None


def as_of_of(response_payload: Mapping[str, Any]) -> date | None:
    """실행의 기준일.

    🔴 **여기서 예외를 던지면 안 된다 (2026-09-01 실측).** 처음에 `DecisionRejected`
      를 올렸더니 **결정은 이미 적재된 뒤라** 저장은 되고 응답은 409 가 나갔다.
      *"약정을 못 만들어도 결정은 남아야 한다"* 고 적어 놓고 그 반대를 했다.
      못 읽으면 `None` 을 돌려주고 사유를 약정에 싣는다.
    """
    raw = response_payload.get("as_of")
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None
    return None


def lead_days_of(response_payload: Mapping[str, Any]) -> Any:
    """N4. **물류가 준 값을 그대로 읽는다** — 없으면 없는 대로 넘긴다."""
    inventory = (response_payload.get("constraints") or {}).get("inventory") or {}
    return inventory.get("inbound_lead_days")


def payment_days_of(response_payload: Mapping[str, Any]) -> Any:
    """N5. **재무가 준 값을 그대로 읽는다** — 없으면 없는 대로 넘긴다.

    ★ `lead_days_of`(N4)와 **같은 모양이다.** 부서가 봉투로 값을 주고 마스터는
      옮기기만 한다 — 마스터가 재무 정책 표를 다시 읽으면 같은 사실의 주인이 둘이 된다
      (`finance/capabilities/procurement.py` 가 `purchase_payment_days` 를 싣는다).
    """
    finance = (response_payload.get("constraints") or {}).get("finance") or {}
    return finance.get("purchase_payment_days")
