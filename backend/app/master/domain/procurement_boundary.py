"""매입 경계 판정 — 최근 매입 실행 행에서 판매가 넘겨받을 경계 값을 읽는다.

행을 읽는 쪽은 `readmodel/procurement_boundary.py` 다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.master.domain.request_ids import is_ledger_gap_request_id
from app.master.schemas.procurement_boundary import ProcurementBoundary
from app.master.schemas.runs import MasterAgentRun

# ---------------------------------------------------------------------------
# 안쪽 — 행 하나에서 무엇을 꺼내나
# ---------------------------------------------------------------------------


def constraints_of(row: MasterAgentRun) -> Mapping[str, Any]:
    """그 행이 든 부서별 경계. 없으면 빈 매핑이다.

    빈 매핑이 "경계를 안 든 행" 이다 — 관문 행·조회 행이 여기로 떨어진다.
    """
    payload = row.get("response_payload") or {}
    if not isinstance(payload, Mapping):
        return {}
    constraints = payload.get("constraints")
    return constraints if isinstance(constraints, Mapping) and constraints else {}


def is_ledger_gap_row(row: MasterAgentRun) -> bool:
    """장부 관문 행인가 — 업무 키로 알아본다 (`#465`).

    모양(`item IS NULL AND end_code == 'E4_NOT_STARTED'`)으로 알아보지 않는다. 그 모양은
    관문 행만의 것이 아니다 (실측 2026-09-09).

      ```text
      PROCUREMENT           1,315행
        E4_NOT_STARTED        404행
          item IS NULL          14행   ← 품목을 정하기 전에 죽은 옛 매입 실행이다
                                        ("경계를 내지 못한 에이전트: finance")
      ```

      그 14행은 전부 `sim_run_id` 가 `NULL` 이라 축을 좁히는 이 함수에 걸리지 않았을
      뿐이다. 축이 막고 있었던 것이지 모양이 스스로를 증명한 것이 아니다. 축이 실린
      채로 품목 전에 죽는 실행이 한 번만 나오면 그날이 "장부가 막았다" 로 잘못 읽힌다.

    관문 행에는 그 행만의 키가 있다 — `request_ids.ledger_gap_request_id` 가 짓고
      `service/persistence.py` 의 `record_ledger_gap` 이 `request_id` 로 적는다. 적는
      쪽과 되찾는 쪽이 같은 모듈을 본다.

    꼬리 문자열을 여기 다시 적지 않는다. 주인은 `request_ids.py` 하나이고,
      `count_runs_by_day` 의 `gate_blocked` 도 같은 꼬리로 그 행을 되찾는다 — 두 벌로
      적으면 한쪽만 바뀌는 날 화면과 성적표가 조용히 갈린다.

    `end_code` 를 같이 보지 않는다. 종료 코드는 "시작 못 했다" 이고 그것은
      관문 행만의 사실이 아니다. 같은 사실을 두 칸으로 물으면 한 칸이 바뀌는 날
      판정이 이유 없이 조용해진다.
    """
    return is_ledger_gap_request_id(row.get("request_id"))


def boundary_from(row: MasterAgentRun, constraints: Mapping[str, Any]) -> ProcurementBoundary:
    """읽은 행 하나를 경계로. 부서 칸을 그대로 옮긴다 — 계산하지 않는다."""
    inventory = _dept(constraints, "inventory")
    finance = _dept(constraints, "finance")
    return ProcurementBoundary(
        present=True,
        source_ref=_source_ref(row),
        warehouse_free_kg=_read_float(inventory, "warehouse_free_kg"),
        rental_cap_kg=_read_float(inventory, "rental_cap_kg"),
        finance_cap_amount_krw=_read_int(finance, "finance_cap_amount_krw"),
        inbound_lead_days=_read_int(inventory, "inbound_lead_days"),
    )


def _dept(constraints: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = constraints.get(name)
    return value if isinstance(value, Mapping) else {}


def _source_ref(row: MasterAgentRun) -> str:
    """"이 경계는 어느 실행의 언제 것" 한 줄. 숨기지 않는다.

    `run_id` 만으로는 부족하다. 그 경계는 그날 매입 판단 시점의 것이고,
      판단 뒤 출고가 나가면 창고가 바뀐다 — 언제 것인지가 답에 따라가야 낮에 받은
      값이 아침 값이라는 것을 사람이 안다.
    """
    return f"master_agent_runs/{row['run_id']}@{row['as_of'].isoformat()}"


def _read_float(payload: Mapping[str, Any], key: str) -> float | None:
    """숫자 칸 하나. 없으면 `None` — `0.0` 으로 안 채운다.

    `0.0` 은 "자리가 없다" 라는 읽은 값이다 (물류 `rental_cap_kg` 이 실제로 그렇다). 못
    읽은 것을 그 값으로 채우면 두 사실이 화면에서 같아진다.

    `bool` 을 숫자로 안 센다 — 파이썬에서 `True` 는 `int` 라 그냥 두면 `1.0` 이 된다.
    """
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _read_int(payload: Mapping[str, Any], key: str) -> int | None:
    """정수 칸 하나. 없으면 `None`.

    실측에 `finance_cap_amount_krw` 가 `168012`(int) 로도 `31854627.0`(float) 로도
      온다 — 같은 칸이 경로에 따라 두 모양이다. 원 단위라 실측 전부가 정수라서
      `round` 가 값을 움직이지 않는다. 모양만 맞추고 값은 안 고친다.
    """
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(value)
