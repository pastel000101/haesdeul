"""실행(sim_run) 열기 — 실행 행 · 기초 재무 상태 · 물류 fixture 를 한 트랜잭션으로 만든다.

★ 2026-09-30 재구성 BL-018: `master/sim_run_runner.py` 에서 옮겼다 — `SimRunOpened`, `open_sim_run`,
  `_config_json`, `_provenance_config`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.master.domain.backfill import BACKFILL_CONFIG_KEY, read_rules
from app.master.domain.sim_run import PROVENANCE_CONFIG_KEY
from app.master.repository.sim_run_open import (
    BaselineLineage,
    LedgerReset,
    delete_sim_run_row,
    reset_sim_run_ledger,
    seed_opening_finance_state,
    seed_opening_logistics_fixture,
)
from app.master.repository.sim_runs import assert_openable, create_sim_run


@dataclass(frozen=True)
class SimRunOpened:
    """문 한 번의 결과. **무엇이 섰고 무엇을 지웠는지**를 값으로 든다."""

    sim_run_id: str
    financing_mode: str
    #: 어디서 출발하는가. 🔴 **가리키기만 한다** — 숫자를 안 담는다.
    baseline: BaselineLineage
    #: 새로 선 시작 재무 상태의 이름.
    opening_finance_state_id: str
    #: 새로 선 시작 물류 fixture 의 이름. 🔴 이것이 없으면 개장이 anchor 를 못 찾는다.
    opening_logistics_fixture_id: str
    period_start: date
    period_end: date
    #: 지운 결과. 🔴 **`None` 은 「안 지웠다」** — `--reset` 을 안 줬다는 뜻이다.
    #: 0 행을 지운 것과 아예 안 지운 것을 같은 값으로 적지 않는다.
    ledger_reset: LedgerReset | None
    #: 실행 행을 몇 행 지웠나. 🔴 **`None` 은 「안 지웠다」** — `--reset` 을 안 줬다는
    #: 뜻이고, `0` 은 *"지우려 했는데 그 이름의 행이 없었다"* 다.
    #:
    #: ★ **장부와 따로 든다.** 장부는 비웠는데 실행 행이 안 지워졌으면 다음 INSERT 가
    #:   PK 에 걸리고, 그 둘을 한 값에 뭉치면 어느 쪽이 안 된 것인지 못 가른다.
    deleted_run_rows: int | None = None
    #: 실행 행에 실은 백필 규칙. 🔴 **`None` 은 「안 실었다」** — 그 실행은
    #: `--auto-approve` 로 걸을 수 없다 (걷기가 걷기 전에 막는다).
    #:
    #: ⚠️ **받은 것을 그대로 든다.** 이 문은 규칙 이름도 라벨도 축 이름도 모른다.
    backfill_rules: Mapping[str, Any] | None = None
    #: 이 실행이 **선 커밋**. 🔴 **`None` 은 「안 받았다」** — 요약이 그렇게 적는다.
    #:
    #: ⚠️ **`baseline` 과 이름만 나란하고 축이 다르다.** 그쪽은 어디서 출발하는가이고
    #:   이쪽은 어느 코드가 걸었는가다.
    baseline_commit: str | None = None


def open_sim_run(
    conn: Any,
    *,
    sim_run_id: str,
    company_persona_id: str,
    run_type: str,
    period_start: date,
    period_end: date,
    as_of: date,
    status: str,
    financing_mode: str,
    baseline: BaselineLineage,
    opening_finance_state_id: str,
    opening_state_date: date,
    opening_state_type: str,
    opening_fixture_id: str,
    opening_usage_scope: str,
    backfill_rules: Mapping[str, Any] | None = None,
    baseline_commit: str | None = None,
    reset: bool = False,
    note: str | None = None,
    reset_fn: Callable[..., LedgerReset] = reset_sim_run_ledger,
    delete_run_fn: Callable[..., int] = delete_sim_run_row,
    create_fn: Callable[..., str] = create_sim_run,
    seed_fn: Callable[..., str] = seed_opening_finance_state,
    logistics_seed_fn: Callable[..., str] = seed_opening_logistics_fixture,
) -> SimRunOpened:
    """실행을 연다. **다섯을 순서대로 부르고 한 번 커밋한다.**

    :param baseline: 🔴 **호출자가 `financing_mode` 와 함께 명시한다** — 한쪽을 보고
        다른 쪽을 고르지 않는다 (모듈 docstring · 재무 청함).
    :param opening_state_date: 🔴 **재무 씨앗과 물류 씨앗이 둘 다 쓴다.** 물류용
        날짜를 따로 두면 두 파트의 anchor 가 갈린다.
    :param opening_usage_scope: 🔴 **어휘의 주인은 물류다** — 여기 박지 않고 받는다.
    :param backfill_rules: 그 실행이 쓸 백필 규칙 (2026-09-11). 🔴 **어휘의 주인은
        부서다** — 이 문은 규칙 이름도 라벨도 축 이름도 모르고, 받은 것을 그대로
        `config_json` 에 싣는다. 안 주면 그 칸이 아예 안 선다.
    :param baseline_commit: 이 실행이 **선 커밋** (2026-09-12). 🔴 **선택이다** —
        커밋은 기본값을 둘 수 있는 값이 아니라 모를 수 있는 값이고, 필수로 두면
        커밋을 모르는 정당한 호출이 막힌다. 안 주면 그 칸이 안 서고 **요약이
        「안 받았다」고 말한다.** 🟡 문자열을 해석하지 않는다 — 빈 값만 막는다.
    :param reset: 🔴 **기본이 거짓이다.** 거짓이면 지우는 함수 **둘 다 한 번도 안
        부른다** — 장부도 실행 행도 그대로 둔다.
    :raises ValueError: 실행이 이미 있는데 `reset` 을 안 줬을 때. **조용히 덮지 않는다.**
    :raises RuntimeError: `reset` 인데 장부가 남아 실행 행 삭제가 FK 에 막힐 때
        (`delete_sim_run_row`). 🟢 **그 막힘이 자기 검사다.**
    :raises BackfillRuleMissing: `backfill_rules` 가 아는 모양이 아닐 때.
        🔴 **여는 자리에서 터진다** — 179일을 걷고 나서 알면 늦다.
    """
    assert_openable(conn, sim_run_id=sim_run_id, reset=reset)
    config_json = _config_json(baseline, backfill_rules, baseline_commit)

    try:
        # 🔴 **순서가 여기다.** 지우는 것이 맨 앞이고, 실행 행이 서야 시작 상태가
        #    그 축을 가리킬 수 있다 (`finance_states.sim_run_id` 가 `sim_runs` 를
        #    참조하는 FK 다 — 뒤집으면 FK 가 막는다).
        #
        # 🔴 **이 한 자리가 「지운다 / 안 지운다」가 갈리는 유일한 곳이다.**
        #    거짓 쪽으로 서 있는 한 지우는 함수는 이름조차 안 불린다.
        ledger_reset: LedgerReset | None = None
        deleted_run_rows: int | None = None
        if reset:
            ledger_reset = reset_fn(conn, sim_run_id=sim_run_id)
            # 🔴 **장부를 지운 다음, 만들기 전이다.** 실행 행이 남아 있으면 바로
            #    아래 INSERT 가 같은 이름의 PK 에 걸린다 — `--reset` 이 한 번도
            #    성공한 적이 없던 이유가 그것이다.
            #
            # ⚠️ **`ON CONFLICT` 로 풀지 않는다** (모듈 docstring) — 조용히 덮으면
            #   다른 설정으로 만들려던 실행이 옛 행 위에 앉는다.
            #
            # 🟢 장부를 다 안 지웠으면 **이 줄이 FK 에 막힌다.** 그것이 자기 검사다.
            deleted_run_rows = delete_run_fn(conn, sim_run_id=sim_run_id)

        create_fn(
            conn,
            sim_run_id=sim_run_id,
            company_persona_id=company_persona_id,
            run_type=run_type,
            period_start=period_start,
            period_end=period_end,
            as_of=as_of,
            status=status,
            financing_mode=financing_mode,
            # 🟢 **lineage 와 백필 규칙만 싣는다.** 잔액도 한도도 안 싣는다 —
            #    값은 늘 `finance_state_id` 가 가리키는 행에서 읽는다 (`sim_run_open`).
            config_json=config_json,
            note=note,
        )

        seed_fn(
            conn,
            sim_run_id=sim_run_id,
            financing_mode=financing_mode,
            baseline=baseline,
            finance_state_id=opening_finance_state_id,
            state_date=opening_state_date,
            state_type=opening_state_type,
        )

        # 🔴 **재무만 놓고 물류를 안 놓으면 새 실행에 물류 행이 한 행도 없다.**
        #    그때 개장은 상한만큼 거슬러도 anchor 를 못 찾고 거절한다 (`#551`).
        #
        # ★ **날짜가 재무 씨앗과 같은 값이다** — 갈릴 자리를 안 만든다.
        logistics_seed_fn(
            conn,
            sim_run_id=sim_run_id,
            baseline_run_id=baseline.from_sim_run_id,
            fixture_id=opening_fixture_id,
            as_of=opening_state_date,
            usage_scope=opening_usage_scope,
        )
    except Exception:
        # ⚠️ **반쪽 실행을 남기지 않는다.** 되돌리기가 또 터져도 원래 사유를 덮지
        #   않는다 — 무엇이 터졌는지가 먼저다.
        with suppress(Exception):
            conn.rollback()
        raise

    # 🔴 **여기가 유일한 커밋이다.** 넷 중 하나라도 터졌으면 위에서 이미 나갔다.
    conn.commit()
    return SimRunOpened(
        sim_run_id=sim_run_id,
        financing_mode=financing_mode,
        baseline=baseline,
        opening_finance_state_id=opening_finance_state_id,
        opening_logistics_fixture_id=opening_fixture_id,
        period_start=period_start,
        period_end=period_end,
        ledger_reset=ledger_reset,
        deleted_run_rows=deleted_run_rows,
        backfill_rules=backfill_rules,
        baseline_commit=baseline_commit,
    )


def _config_json(
    baseline: BaselineLineage,
    backfill_rules: Mapping[str, Any] | None,
    baseline_commit: str | None = None,
) -> dict[str, Any]:
    """실행 행에 실을 설정. **계보 옆에 규칙 칸을 붙인다** (2026-09-11).

    🔴 **칸 이름을 여기서 다시 적지 않는다.** `BACKFILL_CONFIG_KEY` 를 가져다 쓴다 —
      읽는 쪽(`backfill.read_rules`)과 쓰는 쪽이 문자열을 두 벌로 들면, 한쪽만
      고치는 날 **규칙을 실은 실행이 규칙 없는 실행으로 읽힌다.**

    🔴 **여기서 규칙을 검사하지도 지어내지도 않는다.** 아는 모양인지는
      `read_rules` 가 판정한다 — 판정을 두 곳에 두면 언젠가 한쪽만 고쳐지고,
      그때 어느 쪽이 진짜 규칙인지 아무도 못 답한다 (`--commit` · 경계와 같은 규율).

    ★ **안 주면 칸이 아예 안 선다.** 빈 칸을 만들어 두면 *"규칙을 안 정했다"* 와
      *"규칙을 비워 뒀다"* 가 같아지고, 걷기가 그 둘을 못 가른다.

    🔴 **코드 자취는 세 번째 칸이다** (2026-09-12). 계보 안에 얹지 않는다 — 출발점과
      *"어느 코드가 걸었는가"* 는 축이 다르고, 뭉치면 한쪽을 고치는 날 다른 쪽이
      같이 움직인다.
    """
    자취 = _provenance_config(baseline_commit)
    if backfill_rules is None:
        return {**baseline.as_config(), **자취}
    # 🔴 **여는 자리에서 검사한다.** 179일을 걷고 나서 *"모르는 규칙이었다"* 를
    #    알면 늦다 — 그 사이 승인은 한 건도 안 서 있다.
    read_rules({BACKFILL_CONFIG_KEY: backfill_rules})
    return {**baseline.as_config(), BACKFILL_CONFIG_KEY: dict(backfill_rules), **자취}


def _provenance_config(baseline_commit: str | None) -> dict[str, Any]:
    """이 실행이 **선 커밋**이 앉을 칸 (2026-09-12).

    🔴 **안 받았으면 칸이 안 선다.** 빈 값으로 메우면 *"커밋을 안 받았다"* 와
      *"커밋이 비어 있다"* 가 같아지고, 나중에 원장을 읽는 사람이 그 둘을 못 가른다
      (`--reset` 의 `None` 과 `0` 을 안 뭉치는 것과 같은 규율).

    🔴 **문자열을 해석하지 않는다.** sha 인지 태그인지 가지 이름인지 안 본다 —
      그것을 판정하는 순간 이 문이 사람이 쓰는 표기를 알게 되고, 표기가 바뀌는 날
      멀쩡한 값이 거절된다 (`--backfill-rules` 내용을 안 읽는 것과 같은 이유).

    ⚠️ **빈 값과 공백만 있는 값은 막는다.** 그것은 「안 줬다」와 **다른 것을
      가장한다** — 칸은 섰는데 가리키는 커밋이 없는 실행이 남는다.
    """
    if baseline_commit is None:
        return {}
    if not baseline_commit.strip():
        raise ValueError(
            "기준 커밋이 비어 있다 — 안 줄 것이면 --baseline-commit 을 아예 빼라"
            " (🔴 빈 값은 「안 받았다」와 다른 것을 가장한다)"
        )
    return {PROVENANCE_CONFIG_KEY: {"commit": baseline_commit}}
