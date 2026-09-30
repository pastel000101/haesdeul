"""실행 설정의 자동 승인 규칙 조회 — `sim_runs.config_json` 을 읽어 규칙으로 해석한다.

★ 2026-09-30 재구성 BL-018: `master/backfill.py` 에서 옮겼다 — `run_config`, `read_run_rules`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from app.master.domain.backfill import BackfillRules, read_rules
from app.master.readmodel.ledger import get_burn_in


def run_config(sim_run_id: str) -> Mapping[str, Any]:
    """그 실행의 `config_json`.

    ★ **`sim_runs` 를 읽는 주인을 하나 더 만들지 않는다.** `ledger_repository` 가
      이미 그 행을 읽는다 — 여기에 SELECT 를 또 적으면 컬럼이 바뀌는 날 두 곳이
      어긋난다.
    """
    run = get_burn_in(sim_run_id).get("run")
    if not isinstance(run, Mapping):
        return {}
    config = run.get("config_json")
    return config if isinstance(config, Mapping) else {}


def read_run_rules(
    sim_run_id: str,
    *,
    load_config: Callable[[str], Mapping[str, Any]] = run_config,
) -> BackfillRules:
    """그 실행이 정한 규칙을 **행을 하나도 안 보고** 읽는다 (2026-09-11).

    ★ **왜 있나.** 걷기가 `--auto-approve` 를 받으면 **걷기 전에** 규칙이 있는지
      물어야 한다 — 없는 채로 179일을 걸으면 사람이 *"승인이 돌았는데 0건이구나"*
      로 읽고, 그때는 이미 하루도 되돌릴 수 없다.

    🔴 **읽는 방법의 주인을 둘로 만들지 않으려고 여기 둔다.** 부르는 쪽이
      `get_burn_in(...)["run"]["config_json"]` 을 제 손으로 파면 컬럼이 바뀌는 날
      백필과 걷기가 서로 다른 자리를 보게 된다.

    :raises BackfillRuleMissing: 규칙 칸이 없거나 아는 모양이 아닐 때.
        🔴 **여기서는 값으로 안 접는다** — `backfill_decisions` 는 이것을 잡아
        `NO_RULE` 로 담지만, 그쪽은 *"걸었는데 할 것이 없었다"* 를 말해야 하고
        이쪽은 *"걷기 전에 막는다"* 를 말해야 한다.
    """
    return read_rules(load_config(sim_run_id))
