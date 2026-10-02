"""대시보드 — 다섯 부서 값을 날짜 축에 놓기만 한다.

제약: 여기서 숫자를 만들지 않습니다.

같은 값을 두 군데서 계산하면 언젠가 갈라집니다. 그러면 어느 쪽이 맞는지
아무도 모릅니다. 그래서 이 파일은 다른 탭의 `build()` 를 불러 골라
담기만 합니다.

값이 없으면 공란으로 둡니다 — 0 으로 채우지 않습니다.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import date
from functools import partial
from typing import Any

from app.api.calendar import build_axis
from app.api.dashboard.schema import DashboardTab
from app.api.finance import presenter as finance_presenter
from app.api.forecast import presenter as forecast_presenter
from app.api.logistics import presenter as logistics_presenter
from app.api.primitives import Badge, Column, Note, Stat, Table
from app.api.purchase import presenter as purchase_presenter
from app.api.sales import presenter as sales_presenter
from app.master.domain import plan_state
from app.master.readmodel.purchase_record import RecordedTotals, recorded_totals_by_plan

log = logging.getLogger(__name__)

#: 재무 선택 상태 키 → 같은 화면 현금 그래프 계열 이름(`finance_presenter.dashboard_cash`).
#: 「운영 여유」 칸과 그래프 선이 같은 말로 기준을 밝히게 한다. 모르는 키면
#:   재무 탭이 준 상태 이름(`fi.states[].label`)을 그대로 쓴다.
_BASIS = {"base": "대출 제외", "loan": "대출 포함"}

#: 상태 어휘와 판정은 `app/master/domain/plan_state.py` 가 소유한다.
#:
#:   매입 탭도 같은 낱말을 싣는다. 두 벌로 짜면
#:   한쪽만 고치는 날 같은 안이 화면마다 다른 상태로 뜬다. 여기서는 그 이름을
#:   가져다 쓰기만 하고, 부르는 자리는 아래 `_state` 하나다.
PLAN_STATES = plan_state.PLAN_STATES

#: 모르는 값 한 글자. 재고 칸(`_현재고`)이 쓰는 것과 같은 글자다.
_UNKNOWN = "—"

#: 안 이름에서 품목·안 이름을 읽는 규칙은 그 이름을 짓는 매입 탭
#: (`purchase_presenter._plan`)에서 온다.
_plan_item = purchase_presenter.plan_item
_recorded = purchase_presenter.recorded_for


def _pending(plans) -> int:
    return sum(1 for p in plans if p.pending)


def _state(plan: Any, recorded: RecordedTotals | None) -> str:
    """안의 상태를 사람 말로. 판정은 `plan_state.state_of` 가 한다."""
    return plan_state.state_of(approved=plan.approved, recorded=recorded is not None)


def _records(as_of: date, sim_run_id: str) -> dict[tuple[str, str], RecordedTotals]:
    """그날 · 보고 있는 실행의 실매입 기록 합계.

    여기서 숫자를 만들지 않는다. 표를 읽고 합계를 엮는 자리는 마스터 한 곳이고
    (`master/readmodel/purchase_record.recorded_totals_by_plan` — SQL 은 그 아래
    repository) 이 함수는 그것을 부르기만 한다. 같은 SELECT 를 화면 층에 한 벌 더 두면
    PK 가 바뀌는 날 갈린다.

    실패 처리: 못 읽으면 빈 표다. 다섯 부서 탭이 저마다 DB 실패를 삼키고 「예시값」으로 뜨는
    것과 같은 태도 — 기록 하나 때문에 대시보드가 통째로 실패하면 안 된다.
    """
    try:
        return recorded_totals_by_plan(sim_run_id=sim_run_id, as_of=as_of)
    except Exception as error:  # noqa: BLE001  DB 미연결 · 표 없음 둘 다
        log.info("실매입 기록을 못 읽어 안의 값을 그대로 보입니다: %s", error)
        return {}


def _unit_qty(plan: Any, recorded: RecordedTotals | None) -> str:
    """`단가 × 수량`. 기록이 있으면 기록값이다.

    이 표에는 상태 칸이 있고, 상태가 「매입 기록됨」이면 사람이 보는 값은 실제로 산
    값이어야 한다. 제안값은 지나간 값이고, 같은 화면의 「확정 매입액」이 이미 기록값으로
    서 있다 — 한 화면에서 두 숫자가 다른 사실을 말하면 안 된다.

    단가가 정수로 안 떨어지면 「—」다. 반올림해 보이면 `단가 × 수량` 이 금액 칸과
      어긋나고, 그건 틀린 줄도 모르는 오류다.
    """
    if recorded is None:
        return f"{plan.unit_price:,} × {plan.qty_kg:,.0f}"
    unit = _UNKNOWN if recorded.unit_price is None else f"{recorded.unit_price:,}"
    return f"{unit} × {recorded.qty_kg:,.0f}"


def _buffer_stat(fi):
    """재무 「운영 여유」 에 어느 기준인가를 붙인다. 값은 건드리지 않는다."""
    if not fi.stats:
        return None
    stat = fi.stats[0]
    label = next((s.label for s in fi.states if s.key == fi.selected), None)
    basis = _BASIS.get(fi.selected, label)
    if not basis:
        return stat
    return stat.model_copy(update={"label": f"{stat.label} · {basis}"})


def _현재고(lg: Any) -> Stat:
    """물류 탭에서 현재고 한 칸만 꺼낸다.

    자리(`panes[0].stats[0]`)로 집지 않고 열쇠(`key == "stock"`)로 찾는다. 물류 탭 맨 앞은
    「한눈에 보기」라(#675) 자리로 집으면 문제 건수를 재고라고 내보낸다 — 자리는 화면
    사정으로 움직이고 열쇠는 안 움직인다.
    """
    재고칸 = next((p for p in lg.panes if p.key == "stock"), None)
    if 재고칸 is not None and 재고칸.stats:
        return 재고칸.stats[0]
    #  못 찾으면 «모른다» 로 낸다 — 다른 칸을 재고인 척 올리지 않는다.
    return Stat(label="현재고 합계", value="—", detail="물류 탭에서 못 읽었습니다",
                tone="warn", raw=None)


def build(as_of: date, *, sim_run_id: str) -> DashboardTab:
    axis = build_axis(as_of)
    n = len(axis.days)
    at = axis.as_of_index

    #  여덟 조회를 동시에 보낸다. 전부 읽기이고 서로의 결과를 안 쓰므로 순서가 없다.
    #     한 줄로 세우면 왕복 시간이 그대로 더해진다(실측 합 1.79s · DB 가 원격이라
    #     대부분이 기다림이다).
    #
    #  연결을 나눠 쓰지 않는다. 스레드가 한 연결을 같이 쓰면 조용히 섞인다. 여덟 갈래는
    #     공통 풀에서 각자 연결을 빌린다 — 동시에 도는 갈래는 서로 다른 연결을 빌리고,
    #     동시에 빌리는 수의 상한은 `DB_POOL_MAX_SIZE`(`app/core/settings.py`)다.
    #
    #  순서 의존은 없다. 여덟 중 앞 결과를 뒤가 쓰는 쌍이 하나도 없다. 결과는 아래 코드
    #     순서대로 받으므로, 둘이 같이 실패하면 앞에 적힌 쪽의 예외가 먼저 올라간다.
    #
    #  물류 둘은 같은 일정 조회를 나눠 쓴다 (`read_scope`). 범위를 스레드 밖에서
    #     열고 `copy_context()` 로 떠서 넘기는 이유가 이것이다 — `ContextVar` 는 새
    #     스레드에 저절로 안 따라간다. 나눠 쓰는 것은 답(불변 튜플)뿐이고, 먼저 온 쪽이
    #     읽는 동안 뒤 쪽은 기다렸다 그 답을 집는다. `submit` 마다 새 사본을 떠야
    #     한다 — 한 `Context` 를 둘이 동시에 `run` 하면 `RuntimeError` 다.
    with logistics_presenter.read_scope(), ThreadPoolExecutor(
        max_workers=8, thread_name_prefix="dashboard"
    ) as pool:
        def 맡긴다(fn, *args, **kwargs):
            return pool.submit(copy_context().run, partial(fn, *args, **kwargs))

        f_fc = 맡긴다(forecast_presenter.build, as_of, "배추")
        #  매입은 축을 안 주면 모든 실행을 섞는다. 다른 네 탭과 같은 실행을 넘긴다
        #    (입구가 정해 넘긴 `sim_run_id`).
        #
        #  `window_days=0` — 도착일을 안 읽는다 (`#740` 의 인자).
        #     이 화면이 매입에서 읽는 것은 `pu.plans` · `pu.source` 둘뿐이다. 도착일
        #     조회(`arrivals`)가 먹이는 곳은 매입 탭의 확정 매입 표 하나이고 여기서는
        #     안 쓰는데, 그 왕복이 실측 `829.6ms` 로 이 화면의 단일 최대였다.
        #
        #  좁히는 것이 아니라 안 읽는 것이다. 그래서 `12` 가 아니라 `0` 이다.
        #     매입 탭 쪽은 그 사실을 「확정 입고 예정 —」 으로 말하는데, 이 화면은 그
        #     칸을 안 읽으므로 표시가 달라지지 않는다 —
        #     `tests/api/test_dashboard_purchase_window.py` 가 그 자리를 지킨다.
        f_pu = 맡긴다(purchase_presenter.build, as_of, sim_run_id=sim_run_id, window_days=0)
        f_fi = 맡긴다(finance_presenter.build, as_of, "base", sim_run_id=sim_run_id)
        f_lg = 맡긴다(logistics_presenter.build, as_of, "stock", sim_run_id=sim_run_id)
        f_sl = 맡긴다(sales_presenter.build, as_of, sim_run_id=sim_run_id)
        #  두 그래프는 주인 부서(물류·재무)가 만듭니다. 여기서 만들면 같은 값을 두
        #    군데서 계산하게 되고, 실제로 갈라진 적이 있습니다 — 요약은 재고 4,550kg 인데
        #    그래프 끝은 14,600kg 이었습니다.
        f_cash = 맡긴다(finance_presenter.dashboard_cash, axis, sim_run_id=sim_run_id)
        f_stock = 맡긴다(logistics_presenter.dashboard_stock, n, at, as_of, sim_run_id=sim_run_id)
        #  매입안과 같은 실행 · 같은 날의 실매입 기록. 열쇠는 `(품목, 안 이름)`.
        f_records = 맡긴다(_records, as_of, sim_run_id)

        fc = f_fc.result()
        pu = f_pu.result()
        fi = f_fi.result()
        lg = f_lg.result()
        sl = f_sl.result()
        cash = f_cash.result()
        stock = f_stock.result()
        records = f_records.result()

    cabbage = next(c for c in fc.cards if c.item == "배추")
    cards = {c.item: c for c in fc.cards}
    pending = _pending(pu.plans)
    today = axis.days[at] if 0 <= at < n else None

    return DashboardTab(
        axis=axis,
        badges=[
            #  날짜축이 가진 사실만 적는다. 배치 시각·개장 처리 결과는 이 응답에
            #    없으므로 적지 않는다.
            *([] if today is None else [
                Badge(text=("장 열림" if today.market_open else "휴장"),
                      tone=("good" if today.market_open else "neutral")),
            ]),
            #  갈래가 셋이다 — 「안 없음」을 「승인 완료」로 접지 않는다.
            #     `pending == 0` 이 되는 길이 ①전부 승인됐다 ②안이 아예 없다 둘이다. 한
            #     문구로 접으면 걷기가 안 닿은 날처럼 매입안이 0건인 날을 «다 됐다» 고 말한다.
            #     아래 매입 표(`empty_text`)와 「매입 승인 대기」 Stat(`detail`)도 같은 사실을
            #     같은 갈래로 말한다.
            #  `pending` 값은 안 건드린다 — 아래 Stat 의 `raw` 와 같은 수여야 한다.
            Badge(text=("오늘 낸 매입안 없음" if not pu.plans
                        else f"승인 대기 {pending}건" if pending
                        else "오늘 승인 완료"),
                  tone=("neutral" if not pu.plans else "warn" if pending else "good")),
        ],
        stats=[
            #  «내일» 이라고 쓰지 않는다. 카드는 예측 카드가 싣는 대상일
            #    (`cabbage.target_date`)을 그대로 적는다.
            Stat(label=f"{cabbage.item} {cabbage.grade} · {cabbage.target_date[5:]} 예측",
                 value=f"{cabbage.predicted:,}", unit="원/kg",
                 detail=f"구간 {cabbage.lower:,}–{cabbage.upper:,} · 폭 {cabbage.ci_width}",
                 tone="info", raw=cabbage.predicted),
            *([s] if (s := _buffer_stat(fi)) is not None else []),
            _현재고(lg),
            #  상세는 값과 같은 것을 센다. 값은 대기인 안만 세므로(`_pending`) 상세에
            #     그날 안 전부의 이름을 붙이면 값과 다른 것을 센다.
            #  「N안 중 M건 대기」 — M 이 곧 값이다. 대기인 안 이름만 적으면 카드에서 두 줄이
            #    되고(최장 50글자), 대기 0 인 날(안이 선 146일 중 92일)에 따로 쓸 말이 필요하다.
            #    안 이름은 아래 매입 표가 상태와 함께 이미 보인다.
            #  값(`pending`)은 안 건드린다 — 배지 「승인 대기 N건」과 같은 수다.
            Stat(label="매입 승인 대기", value=str(pending), unit="건",
                 detail=(f"{len(pu.plans)}안 중 {pending}건 대기"
                         if pu.plans else "오늘 낸 안 없음"),
                 tone=("warn" if pending else "good"), raw=pending),
            sl.stats[0],
        ],
        forecast_cards=fc.cards,
        purchase=Table(
            columns=[
                Column(key="item", label="품목"),
                Column(key="ml", label="ML 특", align="right", mono=True),
                Column(key="plan", label="안"),
                Column(key="unit_qty", label="단가 × 수량", align="right", mono=True),
                Column(key="amount", label="금액", align="right", mono=True),
                Column(key="state", label="상태"),
            ],
            rows=[
                {
                    "item": item,
                    "ml": (None if (card := cards.get(item)) is None
                           else f"{card.predicted:,}"),
                    "plan": f"{p.key}안",
                    #  기록이 있으면 금액도 단가 × 수량도 기록값이다. 없으면
                    #     안의 값 그대로다 — 0 으로도 «—» 로도 바꾸지 않는다.
                    "unit_qty": _unit_qty(p, rec),
                    "amount": f"{(p.amount_krw if rec is None else rec.amount_krw):,}",
                    "state": _state(p, rec),
                }
                for p in pu.plans
                for item in [_plan_item(p.key)]
                for rec in [_recorded(records, p.key)]
            ],
            empty_text="오늘 낸 매입안이 없습니다",
        ),
        #  안이 없으면 상한가 문장을 짓지 않는다. 안이 없을 때 아래 `join` 결과는 빈
        #     문자열이라 «—» 와 «원/kg» 사이에 공백만 남고 문장이 끊긴다. 이 안내문이 붙는
        #     매입 표가 이미 `empty_text` 로 「오늘 낸 매입안이 없습니다」 라고 말하므로,
        #     안내문도 그 사실을 말한다 — 없는 상한가를 있는 척 가리키면 안 된다.
        purchase_note=Note(
            tone="neutral",
            text=("오늘 낸 매입안이 없어 상한가도 없습니다." if not pu.plans else
                  "상한가(이보다 비싸면 안 산다)는 안마다 다릅니다 — "
                  + " · ".join(f"{p.key} {p.max_price:,}" for p in pu.plans)
                  + " 원/kg. 매입 화면에서 근거와 함께 봅니다."),
        ),
        cash_chart=cash,
        stock_chart=stock,
        sources=[fc.source, pu.source, fi.source, lg.source, sl.source],
    )
