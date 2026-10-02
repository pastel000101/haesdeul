"""그래프가 들고 다니는 상태 (상세설계 v1.1 §3). 초기값을 만드는 함수는 ``service/`` 에 있다.

필드는 §3 State 정의를 그대로 옮긴 것이다. 임의로 늘리지 않는다 — 이 State는 노드끼리만
쓰는 내부 구조가 아니라 설계 문서가 규정한 계약이고, 필드가 늘면 어느 노드가 무엇을 읽는지
문서로 추적할 수 없게 된다.

노드는 State 전체가 아니라 바꿀 키만 담은 dict를 반환한다 — LangGraph 런타임이 병합한다.
그래서 이 TypedDict가 ``total=True``인데도 부분 갱신이 성립한다.

포트 ①~⑤는 초기값을 만들 때 한 번만 부른다 (``service/graph.build_initial_state`` ·
어댑터 경로는 ``service/scenarios.build_state`` 가 봉투에서 편다). ``ports.py`` docstring이
약속한 "T0 스냅샷 생성 시점에만 호출되고 T1 이후 노드는 스냅샷에서 읽는다"가 그 두 함수다.
노드가 직접 ports를 부르면 같은 사이클 안에서 값이 달라질 수 있고(T0~T2 시점 차), 그러면
사중 일치가 무너진다.

⑥ 문서 포트만 예외다 — ② collect_context가 런타임에 호출한다 (정의서 §3.1.1 ·
팀 확인 2026-08-25 · IO명세 §0). 문서는 상태 데이터가 아니라 ``published_at <= as_of``로
고정된 불변 발행물이라 사이클 중 값이 변하지 않는다.
"""

from typing import Literal, NotRequired, TypedDict


class PurchaseAgentState(TypedDict):
    """상세설계 §3. 주석의 ★는 문서 원문 표기를 그대로 옮긴 것이다."""

    # ── 입력 (T0 스냅샷에서 주입) ───────────────────────────────────────────
    date: str  # as_of. 노드는 이 값만 보고, 벽시계를 읽지 않는다 (규칙 1)
    item: Literal["배추", "무", "양파"]
    forecast: dict  # 경락가 예측 (daily는 D+1 ~ D+18)
    market_quotes: list[dict]  # 가락 등급별 당일 경락가
    inventory: dict
    confirmed_orders: dict
    item_mix_ratio: dict  # ★ 품목 비중 (mix 축 게이팅용)
    # ★ 계약단가 (참조값 — 마진 표시용, 컷 아님). 미수령이면 None이고, 그때
    # margin_warning·expected_margin_rate가 함께 null로 나간다 (IO명세 §2 동기화 규칙).
    contract_price: float | None
    margin_defense_floor_rate: float  # ★ 구간별 방어선 (참조값)
    projected_cash_min: int  # ★ 향후 N일 최저 현금 (재무 base_projected_cash_min)
    feedback: dict | None  # 오케스트레이터 재조정 요청 (§6, 전부 기각 시만)

    # ── 재무 수신값 (어댑터 경로에만 실린다) ────────────────────────────────
    # 넷 다 선택 필드다. ``build_initial_state``(mock 경로)는 채우지 않으므로
    # ``.get()``이 None을 돌려주고 노드는 그 값 없이 계산한다 — mock 회귀 테스트가 그대로
    # 도는 근거다. 어댑터 경로에서만 값이 실리고, 그때 계산이 달라진다 (IO명세 §2-B).
    #
    # ``finance_cap_amount_krw``: 재무가 낸 최종 매입 상한(원). 이 값이 오면
    # ``cash.max_purchase_ratio``를 곱하지 않는다 — 같은 목적으로 두 번 조이면
    # "왜 이만큼밖에 못 사나"의 근거가 흐려진다 (재무 회신 v2.2.1 · B6).
    finance_cap_amount_krw: NotRequired[int | None]
    # ``purchase_payment_days``: N5. 정본은 0 — D+0, 매입 당일 지급 (재무 확정)::
    #
    #     purchase_payment_days = 0
    #     source_ref     SRC-FIN-PERSONA
    #     evidence_grade SIM_FIXED
    #     usage_scope    AGENT_MVP_DEMO
    #
    # 축 넷이 같은 방향이라는 것이 재무의 근거다 — Finance Seed `D+0` · Persona `0` ·
    # Evidence `SRC-FIN-PERSONA` · 원장 `due_date = purchase_date`.
    #
    # 우리 동작: 수신값을 그대로 쓴다.
    #
    #     0 = D+0 이라는 유효한 값 · None = 값 없음
    #     0 을 falsy/missing 으로 접지 않는다
    #     7 을 하드코딩하거나 폴백으로 넣지 않는다
    #
    # ``pending_value`` 가 ``is None`` 으로 가르는 것이 옳은 이유는 0 이 뜻을 가진 값이기
    # 때문이다 (규칙 3). 미결은 ``None`` 하나뿐이다. 값을 고쳐 쓰지 않고 수신값을 그대로
    # 쓰므로, 정본 판정이 바뀌어도 동작을 고칠 일이 없다.
    #
    # 마감이 이 값을 그대로 세지는 않는다. ``daily_closings`` ``purchase_cash_out_krw`` 는
    # N5 가 아니라 결정된 ``payment_date`` 에 귀속되고, 이미 확정된 ``payment_date`` 가
    # 있으면 마감이 N5 로 덮어쓰지 않는다. 기존 ``SIM-BURNIN-202512`` 이력도 소급 재계산하지
    # 않는다.
    #
    # mock 경로는 여전히 None이라 지급일 계산이 보류된다 (규칙 3).
    purchase_payment_days: NotRequired[int | None]
    # ``execution_calendar``: 마스터가 싣는 실행일 봉투 (`#300`/`#303`).
    #
    #     {"non_execution_days": ["2026-02-17", …], "horizon_end": "2026-03-02"}
    #
    # 축이 「살 수 있는 날」이다 — `is_open` 이고, 문 앞 게이트의 「예측이 있어 도는
    # 날」과 다르다. 마스터 `service/procurement.py` 의 `_execution_calendar_payload` 가 그
    # 둘을 갈라 적어 두었다: 2026년 토요일 45일에 가락이 서므로, 문 앞 축으로 밀면 살 수
    # 있는 날에 못 산다고 계획한다.
    #
    # 인용으로는 안 풀린다. `is_execution_day` 를 import 해도 `calendar` 를 못 줘서
    # 주말만 피하게 된다 — 값으로 받아야 한다. N4·N5 와 같은 모양이다: 값은 아는 쪽이,
    # 계산은 쓰는 쪽이.
    #
    # ``None`` 과 ``[]`` 가 다른 사실이다 (규칙 3). ``None`` 은 «봉투가 안 왔다» 이고
    # 빈 목록은 «그 지평에 안 서는 날이 없다» 는 확정이다. 마스터는 지평을 다 못 덮으면
    # 통째로 안 싣는다 — 반쪽 달력보다 없는 달력이 낫다는 판단이고, 받는 쪽에서
    # ``or []`` 로 접으면 그 구분이 여기서 사라진다.
    #
    # 회차일 · 도착일을 장이 서는 날로 미는 계산(③ 창고 상한 · ④ 배분 사전검사 · ⑥ 회차
    # 생성)과 ⑦ ``self_check.market_open_days`` 검사가 이 값을 읽는다. ⑦ 은 값이 없으면
    # 컷하지 않고 미검사로 고지한다.
    #
    # 2026-09-10 실측에서는 ⑦ 검사가 한 번도 안 걸렸다. 회차가 155건 전부 하나뿐이고 그
    # 하나가 `as_of` 이며, `as_of` 는 마스터 문 앞 게이트가 실행일만 통과시킨다. 분할이
    # 서는 날(`#308`) 2·3회차가 `as_of + k` 로 가면서 걸린다.
    execution_calendar: NotRequired[dict | None]
    # ``inbound_lead_days``: N4. 입고 리드타임(일). 도착일 = 회차일 + N4이고, 도착일이
    # 없으면 ⑥의 회차별 ``cap_by_date`` 검사가 성립하지 않는다 (#58).
    #
    # 물류가 ``constraints.inventory`` 안에 담아 보내므로 ``absorb_inventory``의 통째 복사로
    # ``state["inventory"]``에도 들어온다. 그런데 ``pending_value``는 State 최상위를 보므로,
    # ``service/scenarios.build_state`` 가 이 칸으로 꺼내 싣는다 — 안 꺼내면 값이 와 있어도
    # "N4 미확정"을 고지하고, "수신값이 설정값을 이긴다"는 규칙이 이 키에서만 안 돈다.
    #
    # 선언(``agent_policy_config.inbound_lead_days``)과 봉투 수신값이 갈릴 수 있다 —
    # 2026-09-10 실측에서 선언은 `1`, 수신은 `2` 였다. 동작은 수신값 그대로이므로 봉투가
    # 바뀌면 저절로 따라간다. 이 칸에 값을 박지 않는다.
    #
    # 성격이 ``PROVISIONAL`` 이다. 물류 원문 — "업계 고정 표준값이라는 의미가
    # 아니라, 창고 위치·배차 SLA 가 최종 확정되기 전 사용하는 운영 Buffer".
    # 그러니 이 값을 근거로 다른 수를 파생시키지 않는다. 도착일 계산에만 쓴다 — 값이
    # 바뀌어도 바뀌는 것은 도착일뿐이다.
    #
    # `2.0` 이 섞여 오는 것은 이미 막혀 있다 — ``domain/payload.py`` 가 ``lead != int(lead)``
    # 로 세우므로 `2.0` 은 통과하고 `2.5` 는 사유와 함께 멈춘다.
    inbound_lead_days: NotRequired[int | None]
    # ``critical_payment_dates``: 지급 집중일. 겹침 경고에만 쓴다 — 날짜별 잔액
    # 재계산은 재무 SCENARIO_VALIDATION 소관이다 (도메인 침범 + 이중 계산).
    critical_payment_dates: NotRequired[list[str] | None]

    # ── 마스터 되먹임 수신값 (어댑터 경로 · 2회차부터) ──────────────────────
    # 재무 수신값 넷과 같은 방식이다 — 선택 필드라 ``build_initial_state``(mock 경로)는
    # 채우지 않고, 어댑터 경로에서만 값이 실린다.
    #
    # ``feedback`` 슬롯과 섞지 않는다 (되먹임 계약 v0.2 §2). 셋이 다르다::
    #
    #     수명   ``feedback``(사용자 조건)은 실행 단위 · 되먹임은 회차 단위
    #     모양   자연어 하나 vs 구조화 배열
    #     권위   사람 → 제안자 vs 조언자 → 제안자
    #
    #   한 슬롯에 ``source`` 로 가르면 payload 의 타입이 source 값에 딸려 가서 계약이
    #   아니라 관례가 된다. 마스터가 두 칸으로 나눠 보내므로 (``flow.py``
    #   ``_purchase_input``) 받는 쪽도 두 칸으로 받는다.
    #
    # 조정안을 반영한다 (E3-6). ``target_value`` 는 마스터 IO Contract §4.4 가 "넘지
    #   말아야 할 값입니다 — 목표가 아닙니다" 로 정했다 (`quantity`·`amount` 는 그 값 이하).
    #
    #   ```text
    #   거른다   ③ draft_plan.split_adjustments   항목·단위·대상 안
    #   반영한다 ③ draft_one 의 caps 에 «조정안» 칸 (라벨별 상한)
    #   말한다   ⑥ risks (못 쓴 사유 · 안 물린 사실) · ⑦ meta.applied_adjustments
    #   ```
    #
    # 전부 반영하는 것이 아니다. 반영할 수 있다고 선언한 항목은 재무 ``amount`` 하나다
    #   (`constraints.feedback.applicable_axis_units`). 물류 ``quantity``·``timing`` 은
    #   관통에서 조정안이 0건이라 어느 단위로 오는지 모른다 — 못 쓰는 것도 버리지 않고
    #   사유와 함께 고지한다.
    #
    # ``adjustments``: 부서 조정안 표준형(``SuggestedAdjustment``)을 편 dict 목록.
    adjustments: NotRequired[list[dict] | None]
    # ``feedback_context``: 그 회차의 방아쇠 — attempt · reason · findings ·
    # verdicts · verdict_reasons. 마스터가 1회차 산출물에서만 만든다.
    feedback_context: NotRequired[dict | None]

    # ── 승인 이력 (어댑터 경로에만 실린다) ──────────────────────────────────
    #
    # ``approved_commitments``: 어제까지 승인된 약정 (`#310` → 마스터 `#312`).
    #   ``constraints`` 밖 최상위로 온다 — 부서가 낸 값이 아니라 마스터가 이력에서
    #   재조립한 값이라 ``execution_calendar`` 와 같은 자리다.
    #
    # ``None`` 과 ``[]`` 가 다른 사실이다. 마스터가 "없으면 칸을 안 만든다 — 빈 배열은
    #   '어제 승인이 없었다' 와 '마스터가 안 보낸다' 를 구별할 수 없다" 로 보낸다
    #   (`flow.py._commitments_block`). 받는 쪽에서 ``or []`` 로 접으면 보내는 쪽이 지킨
    #   구분이 여기서 사라진다 (규칙 3).
    #
    # ⑥ 하나가 읽는다. ``_warehouse_rationale`` 이 창고 근거 문장에서 "어제 승인분 N kg 이
    #   D 에 옵니다" 를 말할 때 쓴다.
    #
    #   근거에만 쓰고 판정에는 안 쓴다. 수량·회차를 이 값으로 바꾸지 않는다 —
    #   ③(총량 클립)과 ⑦(도착일 컷)은 물류 ``cap_by_date`` 만 본다. ``adjustments``
    #   주석이 적어 둔 규율이 여기서도 산다: 반영과 독해는 다른 말이다.
    #
    #   문장을 넓히는 조건이 대조다. 예정분(보장용량 − 여유 − 점유)과 약정 수량이 같고
    #   도착일이 한 날일 때만 인과를 쓴다. 어긋나면 예정분에 승인분 아닌 점유가 섞였다는
    #   뜻이라 인과 없는 기본 문장을 쓴다 (`_commitment_cause`).
    #
    #   누가 읽는지를 검사가 잠근다. ``test_approved_commitments.py`` 의
    #   ``test_이_값을_읽는_노드가_어디인지_잠근다`` 가 ``ast`` 로 실제 참조를 세어
    #   ⑥ 하나임을 확인한다 — 다른 노드가 읽기 시작하면 실패하고, 그때 "판정에 쓰는가" 를
    #   다시 물어야 한다.
    approved_commitments: NotRequired[list[dict] | None]

    # ── 중간 산출 ───────────────────────────────────────────────────────────
    situation: Literal["stable", "uncertain"]
    context_docs: list[dict]  # 주입된 문서 (published_at <= as_of)
    context_loop_count: int  # max 3

    #: 문서를 못 읽은 사유. 비어 있으면 정상이다 (마스터 결정).
    #:
    #:   `context_docs == []` 는 두 뜻이 될 수 있다.
    #:
    #:     그날 그 유형의 문서가 없다        정상 — 무·양파가 그렇다 (이 칸 안 참)
    #:     읽으려다 못 읽었다                실 소스 없어 mock 이 막힘 (이 칸이 참)
    #:
    #: 둘 다 안을 막지 않는다. 마스터가 "문서 없으면 없이 진행" 으로 정했다.
    #:   이 칸이 차면 ⑦ `self_check` 이 각 안의 risks 에 고지만 붙인다 (컷 아님).
    context_unavailable: NotRequired[str]
    allowed_axes: list[str]  # ★ 그날 허용 strategy_type (규칙 계산)
    coverage_days: int  # ★ 커버일수 D
    base_plan: dict  # 수량·타이밍 초안
    split_plan: list[dict] | None  # 분할 계획 (timing 축)
    sourcing_plan: list[dict]  # 등급 배분
    #: ⑥ 이 분할을 일괄로 되돌린 안의 라벨.
    #: ⑦ 이 「축이 왜 안 쓰였나」를 가르는 데 쓴다 — 「되돌려서」와 「다른 검사에서
    #: 탈락해서」는 다른 사실이고, 뒤쪽인데 축 목록을 좁히면 그 탈락을 가린다.
    #: 빈 목록이 기본이다. 여기 이름이 없으면 좁히지 않는다.
    split_rolled_back_labels: list[str]

    # ── 출력 ────────────────────────────────────────────────────────────────
    scenarios_final: list[dict]
    confidence: Literal["high", "medium", "low"]
    rejected_reasons: list[dict]  # {label, reason} — 출력 스키마와 동일 형 (v1.1 정정)
    proposal: dict | None  # ⑦이 조립·재검증한 최종 산출물
    #: ⑧ 이 남긴 안별 검토 흔적. 안 본 안도 들어 있다 — 목록에서 빼면
    #: 「봤는데 깨끗했다」와 구분되지 않는다. 어댑터가 실행 흔적으로 옮긴다.
    review_calls: NotRequired[tuple]
