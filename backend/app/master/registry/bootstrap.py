"""
bootstrap.py — 에이전트 레지스트리와 파트 등록소를 채우는 유일한 자리다.

진입점이 여럿이라 여기 있다.

```text
app/main.py                         임포트 시점에 부른다 (FastAPI 앱)
app/master/cli/backtest_runner.py   main() 이 하루 실행 전에 부른다 (CLI 범위 실행)
app/master/cli/backfill_runner.py   main() 이 부른다
```

CLI 는 `app/main.py` 를 거치지 않는다. 등록이 앱 모듈에만 있으면 CLI 에서는 레지스트리와
등록소가 전부 빈 채로 돌고, 날마다 "하루 넘김 미등록: finance, logistics" 로 돌아선다.
러너는 미등록을 이름까지 정확히 말하므로 고칠 곳은 러너가 아니라 조립 자리다.

멱등: 몇 번을 불러도 같다. 모든 등록이 키 덮어쓰기라 재등록이 거부되지 않는다. "이미
했다" 깃발을 두지 않는 이유가 그것이다 — 깃발을 두면 `wiring.reset()` 으로 비운 뒤 이
함수로 다시 채우는 경로가 막히고, `importlib.reload(app.main)` 로 등록을 다시 세우는
검사(`tests/logistics/test_logistics_day_open.py` ⑩)도 막힌다.

무엇을 등록할지의 근거는 각 줄 위에 있다.
"""

from __future__ import annotations

from functools import partial

from app.finance.adapter import (
    FinanceClosingAdapter,
    FinanceDayOpening,
    FinanceTransitionAdapter,
    finance_port,
)
from app.logistics.adapter import (
    LogisticsCancellationAdapter,
    LogisticsDayOpening,
    LogisticsInboundExecution,
    LogisticsTransitionAdapter,
    logistics_port,
)
from app.logistics.domain.simulated_inspection import ScenarioSimulatedInspectionProvider
from app.master.adapters.finance_parts import (
    FinanceCancellationAdapter,
    FinanceCollectionAdapter,
    FinanceReceivableAdapter,
)
from app.master.registry.cancellation import register_cancellation
from app.master.registry.closing import register_closing
from app.master.registry.collection import register_collection
from app.master.registry.day_open import register_day_opening
from app.master.registry.inbound import register_inbound
from app.master.registry.receivable import register_receivable
from app.master.registry.sim_run_binding import SimRunBound
from app.master.registry.transition import register_transition
from app.master.registry.wiring import register as register_agent
from app.ml.adapter import ml_port
from app.purchase_agent.adapter import purchase_port
from app.purchase_agent.readmodel.quotes import auction_quote_source
from app.sales.adapter import sales_port


def wire_registries() -> None:
    """에이전트 레지스트리와 파트 등록소 일곱을 채운다. 앱과 CLI 진입점이 이것을 부른다.

    파트 등록소: 상태전이 · 하루 넘김 · 마감 · 승인 취소 · 입고 실행 · 수금 · 채권 발행.
    """
    # 마스터가 부를 수 있는 대상은 런타임에 등록된 것뿐이다(wiring).
    #
    # 미등록은 오류가 아니라 "오늘 그 부서가 돌지 않는다"와 같은 상태다 — 마스터가
    # E4_NOT_STARTED + missing_adapters 를 낸다(정의서 §5.3). 등록된 부서는 무엇이 없어서
    # 못 도는지를 자기 missing_data 로 답한다. 둘은 다르다 — 앞은 연결 문제이고 뒤는
    # 그날의 사실이다.
    register_agent("finance", finance_port)
    register_agent("inventory", logistics_port)
    # 매입에는 실 경락가 시세(`auction_quote_source()`)를 넣는다. 기본값 mock 단가로 두면
    # 매입이 안을 못 낸다. mock 단가는 실 ML 예측에서 나온 컷 기준을 못 넘는다 — 두 값의
    # 출처가 달라서다.
    #
    #       cut_unit_price     실 ML 예측 밴드 상단   ← self_check 가 이 값으로 컷한다
    #       grade_unit_price   mock                 배추 1,650 · 무 1,100
    #
    #   그러면 self_check 가 전부 컷하고 `no_proposal_reason` 만 남는다. 같은 payload 를
    #   두 시세로 돌려 확인한 결과다(측정 당시 컷 기준의 이름은 `max_price` 였다).
    #
    #       mock      배추 0안 · 무 0안   business=skipped
    #       실 경락가  배추 2안 · 무 2안   business=ok
    #
    #   지금 `max_price` 는 재무 STRESS 전용이고(`amount_max_krw = qty × max_price`, 매입
    #   `#394`), 컷은 `self_check.check_max_price` 가 `cut_unit_price` 로 한다.
    #   `compute_cut_unit_price` 가 아직 `compute_max_price` 에 위임해 두 값이 같으므로 위
    #   관측은 지금도 그대로 적용된다.
    #
    # mock 이 틀린 값이라서가 아니라 섞이면 안 되는 값이라서다. 예측은 실데이터인데 단가만
    # 연습값이면 그 둘을 비교한 판정은 아무 뜻이 없다.
    #
    # ML `current_price` 와 매입 물량가중 시리즈가 다른 것(배추 812 vs 933)은 오류가 아니다.
    # 그 칸은 시세가 아니라 앵커(0.4×어제 + 0.6×최근 7 거래일 평균)라 애초에 다른 값이다.
    # 산식과 재현값은 `purchase_agent/readmodel/quotes.py` 머리말에 있다. 남은 것은 두 값을
    # 어떻게 병기해 보여줄지이고, 매입단가로 무엇을 쓸지는 아니다 — 매입단가는 실제로 살
    # 때 낼 돈이다.
    register_agent("purchase", partial(purchase_port, quotes=auction_quote_source()))
    # 판매 어댑터. 이 등록이 없으면 `POST /master/sales/run` 이 부르기 전에 선다 —
    # `REQUIRED_FOR_SALES = ("sales", "finance")` 를 문 앞에서 보므로 `sales` 가 없으면
    # `SL4_NOT_STARTED` 로 돌아선다.
    #
    # 미등록과 후보 0건은 다른 사실이다. 앞은 아무도 못 부른 상태이고 뒤는 판매가 답을 낸
    # 결과다. `tests/master/test_sales_registration.py` 가 등록과 그 경로를 같이 잰다.
    register_agent("sales", sales_port)
    # 가격 전망 조회 전용이다. 다른 파트와 같이 조립 자리가 직접 건다 — 부서가 마스터
    # 등록소를 import 하는 역방향을 두지 않으려는 것이다. 등록 자체는 사전에 넣는 것뿐이라
    # 실패하지 않는다.
    register_agent("ml", ml_port)

    # ── 승인 → 장부 상태전이 (C 형태 ⑦) ────────────────────────────────────
    #
    # 이 등록이 없으면 `apply_approval` 이 매 승인마다 "상태전이 미등록" 으로 돌아서고,
    # 사람이 승인해도 장부가 바뀌지 않는다. 어댑터는 재무·물류에 있고 등록은 마스터 몫이다.
    #
    # 위의 `register_agent` 와 다른 등록소다. 저쪽은 부를 대상(에이전트)을, 이쪽은 장부를
    # 바꿀 방법을 담는다. 한 사전에 섞으면 "어댑터가 없다" 와 "전이가 없다" 가 같은
    # 문장으로 나가는데, 둘은 다른 사실이다.
    #
    # `sim_run_id` 는 마스터가 정한다. `persist_inventory` 의 WHERE 가 그 값을 쓰지만
    # "어느 실행의 장부인가" 는 물류 사실이 아니다. 물류 모듈에 상수로 박으면 실행이 둘이
    # 되는 날 물류 코드를 고쳐야 하므로 여기서 눈에 보이게 준다.
    #
    # 어댑터는 `SimRunBound` 로 감싸 부를 때 축을 묶는다. 등록은 프로세스 시작 때
    # 한 번 돌므로, 만들 때 축을 굳히면 하루 실행이 번인이 아닌 실행을 타는 날 매입 원장과
    # 갈린다. 부서 어댑터는 `sim_run_id=` 를 생성자로 받고, 달라지는 것은 언제 만드느냐뿐이다.
    #
    # 여기에 상수를 적지 않는다. 축은 `apply_approval(sim_run_id=…)` 이 나르고, 그 값은
    # 결정이 걸린 실행 행에서 온다(`domain/decision.py` 의 `run_sim_run_id_of`).
    # `open_day` · `receive_arrivals` · `collect_receipts` · `issue_receivables` 도
    # `sim_run_id` 를 필수 키워드 인자로 받는다.
    register_transition(
        "finance",
        SimRunBound(lambda axis: FinanceTransitionAdapter(sim_run_id=axis)),
    )
    register_transition(
        "logistics",
        SimRunBound(lambda axis: LogisticsTransitionAdapter(sim_run_id=axis)),
    )

    # ── 하루 넘김 (day_open) ────────────────────────────────────────────────
    #
    # 또 하나의 다른 등록소다. 위 `register_transition` 은 승인이 장부를 바꾸는 방법을 담고
    # 이쪽은 하루가 넘어가는 방법을 담는다. 한 사전에 섞으면 전이가 없는 것과 하루 넘김이
    # 없는 것이 같은 문장으로 나가고, 둘은 다른 사실이다.
    #
    # `sim_run_id` 를 여기서도 준다(`#324`). 새 행에 쓰는 값은 하루 넘김이 정하지 않는다 —
    # carry-forward 는 `base.sim_run_id` 를 그대로 옮기고 여기서 준 값을 칸에 적지 않는다.
    # 축이 필요한 것은 어느 전날 행을 물려받을지 때문이다. `uq_log_runtime_fixture` 가
    # `(sim_run_id, as_of, usage_scope)` 라 다른 실행의 행이 같은 날에 공존할 수 있고,
    # 좁히지 않으면 하루 넘김이 남의 실행 행을 보고 "열렸다" 고 답한다. 그것은 물려받는
    # 값이 아니라 읽기 전에 알고 있어야 하는 실행 정체성이라 위 두 어댑터와 같은 자리에서
    # 받는다.
    #
    # 새 값을 만들지 않는다. 다른 등록소와 같은 실행 축을 그대로 넘긴다 — 서로 다른 실행에
    # 앉으면 승인이 갱신하는 행과 하루 넘김이 세우는 행이 갈린다.
    #
    # 재무 하루 넘김은 DB 제약과 짝이다. `#285` 가 일별 상태를 `ON CONFLICT (sim_run_id,
    # financing_mode, state_date)` 로 누적하므로, 그 UNIQUE 가 없는 DB 에서는 승인 전이가
    # 그 자리에서 실패한다("there is no unique or exclusion constraint matching the ON
    # CONFLICT specification"). `database/migrations/finance/finance_state_daily_unique.sql`
    # 이 그 제약을 더한다.
    register_day_opening(
        "logistics",
        SimRunBound(lambda axis: LogisticsDayOpening(sim_run_id=axis)),
    )
    register_day_opening(
        "finance",
        SimRunBound(lambda axis: FinanceDayOpening(sim_run_id=axis)),
    )
    register_closing("finance", FinanceClosingAdapter())


    # ── 승인 취소 (undo_approval) ───────────────────────────────────────────
    #
    # 승인을 물리는 방법을 담는 등록소다. 전이와 한 사전에 섞으면 전이는 되는데 취소는 안
    # 되는 상태를 표현할 수 없다.
    #
    # 재무 파트용 `FinanceCancellationAdapter`(`app/master/adapters/finance_parts.py`)는 마스터가
    # 얹은 얇은 연결이다(`#280` 전례) — 재무 취소 함수(`#302`)를 Protocol 에 잇기만 한다.
    # 재무가 자기 구현을 올리면 그 어댑터를 지우고 이 줄만 바꾸면 된다. 물류 쪽은
    # `app/logistics/adapter.py` 의 `LogisticsCancellationAdapter` 다.
    #
    # DB 어휘: `master_decisions.decision` 의 `CANCEL` 과 `payables.status` 의 `CANCELLED` 는
    # `database/schema/` 정의에 들어 있다. 그 어휘가 없는 DB 에서는 이 경로가 CHECK 로
    # 막히므로 `database/migrations/master/master_decision_cancel.sql` 과
    # `database/migrations/finance/payable_cancellation.sql` 을 함께 적용해야 한다. 어휘가
    # 없어도 등록은 해 둔다: 연결이 없는 것과 어휘가 없는 것은 다른 사실이고, 둘을 같은
    # 문장으로 접으면 무엇을 고칠지가 사라진다.
    register_cancellation("finance", FinanceCancellationAdapter())
    register_cancellation(
        "logistics",
        SimRunBound(lambda axis: LogisticsCancellationAdapter(sim_run_id=axis)),
    )


    # ── 입고 실행 (receive_arrivals) ────────────────────────────────────────
    #
    # 도착분을 받는 방법을 담는 등록소다. 한 사전에 섞으면 전이는 되는데 입고는 안 되는
    # 상태를 표현할 수 없다. 이 등록이 없으면 `receive_arrivals` 가 매일 "입고 실행 미등록"
    # 으로 돌아선다. 구현은 물류에 있고 등록은 마스터 몫이다.
    #
    # `inspection_provider` 에는 기본값이 없다. 물류가 일부러 두지 않았다. 자동 검수
    # 규칙(합격률·등급별 판정·수량 배분)을 정한 문서도 코드도 씨앗 데이터도 없는데 기본
    # 구현을 놓으면, 아무도 정한 적 없는 비율이 곧 업무 사실이 되어 원가·폐기·판매 판단으로
    # 흘러간다. 그래서 등록 자리에서 눈에 보이게 고른다. 클래스가 저장소에 있다고 해서
    # 그것이 기본값이 되지는 않는다 — `ScenarioSimulatedInspectionProvider` 의 docstring 이
    # 그렇게 적어 뒀다.
    #
    # `ScenarioSimulatedInspectionProvider`(물류 `#336`)는 품질 모델이 아니다. "실제
    # 농산물이 늘 100% 정상" 이라는 주장이 아니라 이번 MVP 가 품질손실 축을 아직 쓰지
    # 않는다는 명시적 가정이다. 물류가 그 이유를 자기 파일에 적어 뒀고, 그 판단의 주인이
    # 물류다. 마스터는 검수 규칙을 정하지 않는다.
    #
    #   ```text
    #   등록 안 함          "입고 실행 미등록"   연결 문제 — 그날의 사실이 아니다
    #   ScenarioSimulated   전량 PASS            물류가 정한 MVP 가정  ← 지금
    #   ```
    register_inbound(
        "logistics",
        SimRunBound(
            lambda axis: LogisticsInboundExecution(
                sim_run_id=axis,
                inspection_provider=ScenarioSimulatedInspectionProvider(),
            )
        ),
    )


    # ── 수금 (collect_receipts) ─────────────────────────────────────────────
    #
    # 채권이 돈으로 들어오는 방법을 담는 등록소다(`registry/collection.py`). 한 사전에
    # 섞으면 입고는 되는데 수금은 안 되는 상태를 표현할 수 없다. 이 등록이 없으면
    # `collect_receipts` 가 매일 `NOTHING_DUE` + `missing=["finance"]` 로 돌아선다.
    #
    # `sim_run_id` 는 마스터가 정하고 `financing_mode` 는 마스터가 고르지 않는다.
    #
    #    ```text
    #    sim_run_id       마스터가 정한다 — 다른 등록소와 같은 실행 축이다
    #    financing_mode   마스터 축이 아니다 — 재무 축의 것
    #                        (sim_run_id, as_of, financing_mode)
    #    ```
    #
    #    `finance_states` 에는 `LOAN_BASELINE` 행과 `BASE_NO_LOAN` 행이 공존한다(실측
    #    252행 · 2행). 여기에 하나를 상수로 박으면 무차입 상태가 대출 baseline 자리에 조용히
    #    들어온다 — `app/finance/readmodel/finance_state.py` 의 `get_finance_runtime_axis`
    #    가 그 문장을 적어 뒀다.
    #
    #    그래서 어댑터가 `collect()` 안에서 재무에게 축을 묻는다. 임포트 시점에 DB 를 읽지
    #    않는 것은 다른 등록소와 같다. 재무 축의 `sim_run_id` 가 마스터가 준 것과 다르면
    #    막는다(fail-closed) — 조용히 남의 실행 장부에 수금을 적으면 안 된다.
    #
    # 이 어댑터도 마스터가 얹은 얇은 연결이다(`#280` · `FinanceCancellationAdapter` 전례).
    # 재무가 축 둘을 직접 들고 오는 구현을 올리면 그 어댑터를 지우고 이 줄만 바꾸면 된다.
    #
    # 수금 사건은 이 줄이 아니라 `master_collection_events` 표에서 온다. 어댑터가
    # `collect()` 안에서 그 축의 사건을 읽는다. 등록 시점에 사건 목록을 만들어 넘기면 그
    # 목록이 고정돼, 표에 한 줄 넣어도 앱을 다시 띄우기 전까지는 아무 일도 일어나지 않는다.
    #
    # 이 등록은 자리를 만들 뿐 사건을 만들지 않는다. 표가 비어 있으면(2026-09-08 실측 0행)
    # 매일 `NOTHING_DUE` 이고, 그것은 "확인했고 낼 것이 없다" 로서 「등록 안 됨」과 갈린다.
    # 무엇을 사건으로 둘지는 팀 결정이고, 재무가 "due_date 경과를 수금으로 읽지 않는다" 로
    # 그은 선이 그 이유다.
    register_collection(
        "finance",
        SimRunBound(lambda axis: FinanceCollectionAdapter(sim_run_id=axis)),
    )


    # -- 채권 발행 (issue_receivables) --------------------------------------
    #
    # 판매 확정이 채권을 만드는 방법을 담는 등록소다. 앞의 등록소 어디에도 합치지 않는다 —
    # 합치면 "수금은 되는데 채권은 안 서는" 상태를 표현할 수 없다. 이 등록이 없으면
    # 경계(`service/receivable.py`)와 재무 구현(`app/finance/service/receivables.py` 의
    # `confirm_receivable`)이 있어도 부르는 자리가 없어, 배송된 판매에 채권이 생기지 않는다.
    #
    # sim_run_id 는 다른 등록소와 같은 실행 축을 받고, financing_mode 는 어댑터가 issue()
    # 안에서 get_finance_runtime_axis() 로 재무에 묻는다. 이유는 수금과 같다 —
    # finance_states 에 LOAN_BASELINE 과 BASE_NO_LOAN 이 공존하므로 하나를 상수로 박으면
    # 무차입 상태가 대출 baseline 자리에 조용히 들어온다.
    #
    # 판매 목록도 이 줄이 아니라 호출 시점에 sales 에서 읽는다 — 등록 시점에 고정하면
    # 판매가 한 줄 들어와도 앱을 다시 띄우기 전까지 아무 일도 일어나지 않는다. 수금 사건을
    # master_collection_events 에서 읽는 것과 같은 이유다.
    register_receivable(
        "finance",
        SimRunBound(lambda axis: FinanceReceivableAdapter(sim_run_id=axis)),
    )
