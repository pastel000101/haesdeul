"""자동 백필 승인 — 과거 구간을 규칙 하나로 재현해 장부를 만든다.

```text
백필 승인           과거 구간을 재현해 장부를 만드는 것   = 데이터 생성   ← 이 파일
에이전트 자율 승인   오늘의 안을 스스로 정하는 것          = 판단 위임    팀이 금지했다
```

어느 안이 나은지 판단하는 코드가 여기 없다. 규칙이 가리키는 안을 그대로 고르거나,
그 안이 없으면 고르지 않는다. 두 갈래뿐이라 고를 것이 없다.

`backtest_runner.walk` 과 같은 모양이다 — 범위를 받고, 하루가 막혀도 밖으로 예외를
내지 않고, 무엇을 했는지를 값으로 돌려준다.

이 파일에는 CLI 진입점을 일부러 두지 않는다. 판단이 사는 파일에 문까지 두면 실수로
돌아갈 길이 그만큼 짧아진다(`tests/master/test_backfill.py` 가 이것도 검사한다). 문은
`cli/backfill_runner.py` 에 가드와 함께 있고, 기본이 「안 쓴다」라 `--commit` 을 줘야
한 행이라도 적힌다.

## 규칙의 집

```json
{"backfill": {
  "procurement": {"rule": "ALWAYS_BASE",       "scenario_label":  "..."},
  "sales":       {"rule": "ALWAYS_FIXED_TYPE", "scenario_type":   "..."},
  "sales_terms": {"partner_id": "...", "payment_terms_type": "...",
                  "payment_days": 0,   "unit_price_source":  "..."}
}}
```

매입 규칙은 둘 중 하나다.

```json
{"procurement": {"rule": "ALWAYS_BASE",   "scenario_label":  "1순위"}}
{"procurement": {"rule": "FIRST_OFFERED", "scenario_labels": ["1순위", "2순위"]}}
```

순서 규칙(`FIRST_OFFERED`)이 있는 이유: 매입 `#584`(보유 차감) 이후 한 라벨의 안이 자주
서지 않는다. 실측에서 같은 열흘이 15건 → 3건으로 줄었고, 그 라벨 하나를 가리키던
걷기가 `LABEL_NOT_OFFERED` 를 51번 냈다 — 곡선에 승인이 거의 남지 않았다.

이것은 대체가 아니다. 사람이 "이것 먼저, 없으면 저것" 이라고 정해서 파일에 적는다.
코드는 그 순서를 읽기만 하고, 적히지 않은 라벨은 고르지 않는다.

규칙의 집은 `sim_runs.config_json` 이다. 한 실행 = 사이클마다 규칙 하나이고, 규칙은
행이 아니라 실행에 속한다.

`sales_terms` 는 승인 규칙이 아니다. 승인할 안을 고르는 둘과 달리, 이 칸은 판매에
물어볼 때 요청에 실리는 조건이다 — 자동 걷기에는 사람이 없어 거래처도 지급조건도
단가도 정할 사람이 없기 때문이다. `SALES_TERMS_KEY`(`domain/backfill.py`)에 왜 `sales`
안에 넣지 않았는지를 적어 두었다.

사이클별로 가른 모양만 읽는다. 옛 평면 모양(`{"backfill": {"rule": ...}}`)은 오류로
멈춘다 — 조용히 매입으로 접으면 어느 규칙으로 돌았는지가 갈린다. 그 모양을 쓴 실행이
없어(2026-09-10 확인) 호환을 만들지 않았다.

코드에 안 이름을 박지 않는다. `domain/decision.py` 의 `scenario_labels_of` 가 이미 그
규율을 적어 두었다 — "'보수·기본·공격' 은 매입의 계약이다. 여기에 복제하면 매입이 라벨을
바꿀 때 조용히 어긋난다." 판매 축 이름도 같다. 그래서 코드가 아는 것은 「고정 라벨
규칙」·「고정 축 규칙」이라는 모양뿐이고, 어느 이름인지는 설정이 말한다.

기본 규칙을 지어내지 않는다. 규칙 칸이 없으면 `NO_RULE` 이고 한 행도 쓰지 않는다 —
기본값은 곧 업무 규칙이고, 그러면 아무도 정하지 않은 규칙으로 곡선이 선다.

## 축이 둘로 갈리는 자리

```text
매입   설정의 scenario_label 로 고른다
판매   설정의 scenario_type 으로 후보를 찾아, 그 후보의 scenario_id 를 싣는다
```

판매 후보에는 `label` 이 없다. `DecisionIn.scenario_label` 칸에 `scenario_id` 를 싣는
것은 판매가 이미 정한 계약이고(`schemas/decision.py` 의 `DecisionIn` 참고), 여기서 새로
만드는 것이 아니다.

일반 Runtime 은 그대로 `HUMAN` 이다. 이 모듈은 과거 장부 재현만 하므로 추천·랭킹을
승인 권한으로 쓰는 것과 다르다.

규칙 해석은 `domain/backfill.py`, 실행 설정 조회는 `readmodel/backfill_rules.py` 에 있다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from app.core.clock import today_in_seoul
from app.master.domain.backfill import (
    BackfilledRun,
    BackfillOut,
    BackfillOutcome,
    BackfillRuleMissing,
    BackfillRules,
    SalesBackfillRule,
    read_rules,
)
from app.master.domain.decision import (
    AUTO_BACKFILL,
    approve_end_codes,
    available_scenario_names,
    scenario_ids_of_type,
)
from app.master.readmodel.backfill_rules import run_config
from app.master.readmodel.decisions import list_decisions
from app.master.readmodel.runs import list_runs
from app.master.schemas.decision import DecisionIn, DecisionOut
from app.master.service.decision import record_decision

# `AUTO_BACKFILL` 의 주인은 `domain/decision.py` 다. 그 값이 승인이 전이를 바로
# 부르는지를 가르므로 결정 어휘 옆에 두고, 여기서는 들여와 그대로 쓴다.

BACKFILL_BOUNDARY_AS_OF = date(2026, 9, 18)
"""자동으로 채울 수 있는 마지막 날. 이 날까지 포함이다. 가드가 둘이다.

```text
as_of <= 2026-09-18  그리고  as_of < 실제 서울 오늘   자동으로 채운다
둘 중 하나라도 아니면                                사람만. 한 행도 안 쓴다
```

값의 근거(2026-09-14 결정): 최종 실행 SIM-CHAIN-FINAL 을 2026-09-19(토) 아침에
01-01~09-20 · 판정 09-20 12시로 건다. 그 시점에 09-14~09-18 은 과거라 자동 승인이
「과거 재현」이다. 재무·물류가 동의했다.

그래서 실제 오늘 가드를 함께 둔다. 경계 상수가 실제 오늘보다 뒤이면, 그 사이에 누가
걷기를 걸 때 그날 안을 자동 승인할 수 있다 — 에이전트 자율 승인 금지가 뚫린다.
`as_of >= today_in_seoul()` 이면 경계 안이어도 막는다.

  실제 날짜는 새로 읽지 않고 시각 정본 `core.clock.today_in_seoul` 을 쓴다.

  걷기의 `--now`(판정 시각)로 가드하지 않는다. `--now` 는 사람이 고르는 입력이라
  그것으로 재면 미래 시각 하나로 가드가 풀린다 — 이 가드는 실제 시계다.

오늘 이후 자동 승인은 금지다(에이전트 자율 승인 금지). 경계는 `config_json` 으로 빼지
않고 코드 diff 로만 옮긴다. 이것은 규칙이 아니라 가드라 옮기려면 diff 에 보여야 한다.
설정으로 내리면 오늘 이후를 자동 승인하는 것이 행 하나 고치는 일이 된다.
"""


def backfill_decisions(
    *,
    sim_run_id: str,
    start: date,
    end: date,
    load_config: Callable[[str], Mapping[str, Any]] = run_config,
    runs_on: Callable[..., Sequence[Mapping[str, Any]]] = list_runs,
    decisions_of: Callable[[str], Sequence[DecisionOut]] = list_decisions,
    decide: Callable[[str, DecisionIn], DecisionOut] = record_decision,
    limit_per_day: int = 500,
    today: Callable[[], date] = today_in_seoul,
) -> BackfillOut:
    """`start` 부터 `end` 까지, 규칙이 가리키는 안을 승인 문으로 승인한다.

    승인 문을 우회하지 않는다. `record_decision` 을 그대로 부른다 — `save_decision` 을
    직접 부르거나 재검증을 건너뛰면 그 순간 「승인」이 두 종류가 된다. 백필 승인도 사람
    승인과 같은 검사 · 같은 재검증 · 같은 이력을 지나야 한다. 느려도 그것이 맞다.

    재검증은 벽시계로 돌지 않는다 — `record_decision` 안쪽의 `_revalidation_for` 가 그
    실행 행의 `as_of` 를 쓴다. 그래서 과거 구간을 오늘 재현해도 오늘로 개장을 묻지 않는다.

    :param load_config: 규칙을 읽는 자리. 기본은 `sim_runs.config_json`.
    :param runs_on: 하루치 실행 이력. `list_runs` 와 같은 키워드로 부른다.
    :param decisions_of: 그 업무 키에 이미 붙은 결정.
    :param decide: 승인 문. 기본값이 `record_decision` 자체다 — `None` 을 받지 않는다
        (`backtest_runner.walk` 의 `run_day_fn` 과 같은 규율).
    :param today: 실제 서울 오늘. 기본이 `clock.today_in_seoul` 자체다 — `None` 을 받지
        않는다(`clock.py` 의 규율). 한 번만 읽어 모든 행에 같은 날을 쓴다 — 행마다 읽으면
        자정을 넘기는 순간 한 백필 안에서 가드가 갈린다. 걷기의 `--now` 가 아니다.
        판정 시각으로는 이 가드를 열지 않는다.
    :raises ValueError: 범위가 거꾸로일 때. 막고 사유를 낸다.
    :raises LookupError: 그 실행을 못 찾아 규칙을 읽지도 못했을 때. "규칙이 없다" 와
        섞지 않는다 — 저쪽은 값(`NO_RULE`)이고 이쪽은 사고다.
    """
    if start > end:
        raise ValueError(
            f"백필 범위가 거꾸로다: {start.isoformat()} ~ {end.isoformat()}"
            " — 어느 쪽이 시작인지를 여기서 정하지 않는다"
        )

    try:
        rules = read_rules(load_config(sim_run_id))
    except BackfillRuleMissing as exc:
        # 한 행도 쓰지 않고 돌아간다. `decide` 를 한 번도 부르지 않는다.
        return BackfillOut(
            sim_run_id=sim_run_id, start=start, end=end, status="NO_RULE", reason=str(exc)
        )

    results: list[BackfilledRun] = []
    blocked: list[date] = []
    real_today = today()

    day = start
    while day <= end:
        if day > BACKFILL_BOUNDARY_AS_OF or day >= real_today:
            blocked.append(day)
        for row in runs_on(sim_run_id=sim_run_id, as_of=day, limit=limit_per_day):
            results.append(
                _backfill_one(
                    row, rules, real_today=real_today, decisions_of=decisions_of, decide=decide
                )
            )
        day += timedelta(days=1)

    return BackfillOut(
        sim_run_id=sim_run_id,
        start=start,
        end=end,
        status="RAN",
        rules=rules,
        runs=tuple(results),
        blocked_days=tuple(blocked),
    )


def _backfill_one(
    row: Mapping[str, Any],
    rules: BackfillRules,
    *,
    real_today: date,
    decisions_of: Callable[[str], Sequence[DecisionOut]],
    decide: Callable[[str, DecisionIn], DecisionOut],
) -> BackfilledRun:
    """실행 이력 한 행을 규칙대로 처리한다. 어느 안이 나은지 따지지 않는다."""
    as_of = row["as_of"]
    run_id = str(row["run_id"])
    request_id = row.get("request_id")
    cycle = row.get("cycle") if isinstance(row.get("cycle"), str) else ""

    def 결과(outcome: BackfillOutcome, reason: str | None = None) -> BackfilledRun:
        return BackfilledRun(
            as_of=as_of,
            run_id=run_id,
            request_id=request_id,
            outcome=outcome,
            reason=reason,
        )

    # ── ① 경계 ──────────────────────────────────────────────────────
    # 가장 먼저 본다. 뒤로 밀면 그 앞 검사가 하나 바뀌는 날 경계가 새 나간다. 루프가
    # 아니라 행의 `as_of` 로 잰다 — 조회 필터가 무엇을 걸었든 가드는 자기 눈으로 본다.
    if as_of > BACKFILL_BOUNDARY_AS_OF:
        return 결과(
            "BLOCKED_BY_BOUNDARY",
            f"{as_of.isoformat()} 은 백필 경계({BACKFILL_BOUNDARY_AS_OF.isoformat()}) 밖이다"
            " — 그 뒤는 사람만 승인한다",
        )
    # 경계 안이어도 실제 서울 오늘 이후면 막는다. 경계(09-18)가 실제 오늘보다 뒤일 때
    # 이 줄이 없으면 그 사이에 건 걷기가 그날 안을 자동 승인한다. 걷기의 `--now` 가
    # 아니라 실제 시계다.
    if as_of >= real_today:
        return 결과(
            "BLOCKED_BY_BOUNDARY",
            f"{as_of.isoformat()} 은 실제 오늘({real_today.isoformat()}) 이후다"
            " — 당일·미래 자동 승인 금지 · 사람만 승인한다",
        )

    # ── ② 승인이 성립하는 실행인가 ──────────────────────────────────
    # 어느 종료 코드에 승인이 서는지는 `domain/decision.py` 의 `approve_end_codes` 가
    # 주인이다. 여기에 코드를 베끼면 그쪽이 바뀌는 날 백필만 옛 규칙으로 돈다.
    #
    # 사이클을 여기서 다시 가르지 않는다. `approve_end_codes` 가 이미 사이클별로 답한다 —
    # 그 앞에 사이클 조건을 하나 더 놓으면 어느 종료 코드에 승인이 서는지의 주인이 둘이
    # 된다.
    end_code = row.get("end_code")
    if end_code not in approve_end_codes(cycle):
        return 결과("NOT_APPROVABLE", f"종료 코드가 {end_code!r} 다 (cycle={cycle!r})")

    # ── ③ 그 사이클 규칙을 설정이 정했나 ────────────────────────────
    # `NO_RULE` 로 접지 않는다. 실행 전체가 규칙 없이 돈 것과, 이 사이클만 정하지 않은
    # 것은 다른 사실이다 — 뒤엣것은 의도일 수 있다.
    rule = rules.for_cycle(cycle)
    if rule is None:
        return 결과("NO_RULE_FOR_CYCLE", f"{cycle!r} 사이클 백필 규칙을 설정이 안 정했다")

    if not isinstance(request_id, str) or not request_id:
        return 결과("FAILED", "실행 행에 업무 키가 없어 승인 문을 부를 수 없다")

    # ── ④ 이미 결정이 있으면 덮지 않는다 ────────────────────────────
    if decisions_of(request_id):
        return 결과("ALREADY_DECIDED", "이미 붙은 결정이 있다 — 자동이 사람 결정을 덮지 않는다")

    # ── ⑤ 규칙이 가리키는 안이 그날 있었나 ──────────────────────────
    response_payload = row.get("response_payload") or {}

    # 승인 문과 같은 눈으로 본다. `check_scenario_exists` 가 이 목록으로 검사하므로,
    # 여기서 맞추지 않으면 승인 문이 실패해 `FAILED` 로 남고 "그날 그 안이 없었다" 는
    # 사실이 사고로 뭉개진다.
    available = available_scenario_names(response_payload, cycle)
    찾은순서: tuple[str, ...] = ()

    if isinstance(rule, SalesBackfillRule):
        # 축으로 찾되, 둘 이상이면 고르지 않는다. 첫 번째를 고르면 배열 순서가 선택
        # 규칙이 되고, 판매가 그것을 쓰지 않기로 명시했다.
        matched = scenario_ids_of_type(response_payload, rule.scenario_type)
        if len(matched) > 1:
            return 결과(
                "AMBIGUOUS_TYPE",
                f"규칙이 가리키는 축의 후보가 {len(matched)} 개다 — 규칙이 어느 것인지"
                " 말하지 않아 안 고른다",
            )
        picked = matched[0] if matched else None
    else:
        # 적힌 순서대로 찾아 먼저 있는 것을 고른다(`FIRST_OFFERED`). `ALWAYS_BASE` 는 그
        # 순서가 하나뿐인 경우다.
        #
        # 순서를 여기서 정하지 않는다. `labels_in_order` 가 설정이 적은 그대로를 낸다 —
        # 이 줄이 정렬하거나 뒤집으면 규칙의 주인이 코드가 된다.
        #
        # 그래도 대체가 아니다. 사람이 "보수 우선, 없으면 기본" 이라고 적어 둔 것을 따르는
        # 것이고, 적히지 않은 라벨은 고르지 않는다.
        찾은순서 = rule.labels_in_order
        picked = next((label for label in 찾은순서 if label in available), None)

    # 없으면 다른 안으로 대체하지 않는다. 대체하면 곡선이 규칙과 다른 것을 재현하고,
    # "규칙이 정한 안을 늘 고른 곡선" 이라는 발표 문장이 거짓이 된다.
    if picked is None or picked not in available:
        shown = ", ".join(available) if available else "(없음)"
        # 「하나를 못 찾았다」와 「둘 다 못 찾았다」는 다른 사실이다. 찾아본 순서를 사유에
        # 적어 두면, 규칙을 늘렸는데도 안이 서지 않았다는 것이 그 줄로 보인다.
        #
        # 판매에는 이 절이 없다. 판매는 라벨이 아니라 축으로 찾으므로 "찾은 순서" 라는
        # 말 자체가 없다 — 없는 개념을 빈 값으로 적지 않는다.
        순서절 = f"찾은 순서: {' → '.join(찾은순서)} · " if 찾은순서 else ""
        return 결과(
            "LABEL_NOT_OFFERED",
            f"규칙이 가리키는 안이 그날 없다 ({순서절}제시된 안: {shown})",
        )

    # ── ⑥ 승인 문 ───────────────────────────────────────────────────
    try:
        saved = decide(
            request_id,
            DecisionIn(
                decision="APPROVE",
                # 주의: 판매에서는 이 칸에 `scenario_id` 가 실린다 — 칸 이름과 값이
                # 어긋나는 자리이고, 그렇게 하기로 판매가 정했다.
                scenario_label=picked,
                decided_by=AUTO_BACKFILL,
                history_run_id=run_id,
                # 되짚을 때 눈으로 보라는 것뿐이다. 파싱하지 않는다 — 자유 텍스트라 규칙
                # 이름의 주인이 아니다. 주인은 `config_json` 이다.
                note=f"{AUTO_BACKFILL} rule={rule.name}",
            ),
        )
    except Exception as exc:  # noqa: BLE001 - 한 날이 막혀도 나머지 날은 채운다.
        return 결과("FAILED", f"{type(exc).__name__}: {exc}")

    return BackfilledRun(
        as_of=as_of,
        run_id=run_id,
        request_id=request_id,
        outcome="RECORDED",
        revalidation_outcome=saved.revalidation_outcome,
        # 확정 결과를 여기서 버리지 않는다. `saved.sale` 을 읽지 않으면 확정이 `BLOCKED`
        # 로 막혀도 성적표에는 `RECORDED` 하나만 남아, "승인이 적혔다" 가 "판매가 섰다"
        # 로 읽힌다.
        confirmation_status=None if saved.sale is None else saved.sale.status,
        confirmation_reason=None if saved.sale is None else (saved.sale.reason or None),
        # 확정이 재고를 잡았는지를 여기서 버리지 않는다. `CONFIRMED` 만 세면 "팔렸다" 가
        # "잡아 뒀다" 로 읽히고, 그 사이에서 같은 재고가 다음 날 또 팔린다.
        reservation_outcome=None if saved.sale is None else saved.sale.reservation_outcome,
        # 순서에서 실제로 고른 것을 남긴다. 규칙 파일에 적힌 순서와 그날 선 라벨은 다른
        # 사실이다 — 규칙만 보고 곡선을 읽으면 틀린다.
        #
        # 판매는 `None` 이다(`찾은순서` 가 비어 있다). 판매의 `picked` 는 라벨이 아니라
        # 후보 `scenario_id` 라 라벨 어휘에 섞으면 안 된다.
        picked_label=picked if 찾은순서 else None,
        # 전이가 왜 원장을 못 썼는지를 여기서 버리지 않는다. `saved.transition` 을 읽지
        # 않으면 승인이 났는데 매입 원장에 한 행도 안 남아도 성적표에는 `RECORDED` 하나만
        # 남는다 — `confirmation_status` 와 같은 이유다.
        transition_block_kind="" if saved.transition is None else saved.transition.block_kind,
        # 동일성 키의 나머지 반쪽이다. 이 칸이 없으면 세는 쪽이 `request_id` 하나로
        # 접어야 하고, 같은 업무 키에 결정이 둘 붙은 날 수가 조용히 작아진다.
        decision_seq=saved.decision_seq,
    )
