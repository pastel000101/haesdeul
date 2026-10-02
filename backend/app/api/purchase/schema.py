"""매입 탭이 받는 모양.

소유: 매입 파트. 모양을 바꾸려면 화면 담당(ML)과 같이 봅니다 — 칸 이름을
바꾸면 화면이 조용히 빈 칸이 됩니다.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.api.primitives import Note, Source, Stat, Table


class Reason(BaseModel):
    """이 안을 왜 냈나 — 한 줄.

    `ref` 는 근거를 되짚을 꼬리표입니다 (예: 예측 `FC-…` · 시세 `MQ-…`). 꼬리표가
    없으면 나중에 근거를 되짚을 수 없으므로 매입 파트는 채워 보냅니다. 원래 근거에
    꼬리표가 없으면 `None` 입니다.
    """

    source: str = Field(description="예측 · 시세 · 주문 · 재고 · 현금 · 창고")
    text: str = Field(description="근거 한 줄. 숫자를 그대로 적는다")
    ref: str | None = Field(default=None, description="근거 꼬리표")
    carried: bool = Field(default=False, description="어제 판단에서 이어받은 것인가")


class Plan(BaseModel):
    """매입안 하나.

    단가 상한 칸이 둘이고 쓰임이 다르다.

    ```text
    max_price       재무 스트레스 기준. amount_max_krw = qty × max_price 등식을
                    재무 시나리오 검증과 마스터 검사(L-PAYSCHED-MAX)가 확인한다
    cut_unit_price  매입 컷 기준. 매입단가가 이보다 비싸면 그 안을 사지 않는다
    ```

    지금은 두 값이 같은 산식에서 나와 같다. 컷 산식을 바꾸면 `cut_unit_price` 만
    달라진다. 주의: 화면의 「이보다 비싸면 안 산다」 자리에는 `cut_unit_price` 를 쓴다 —
    `max_price` 를 쓰면 컷 산식이 바뀐 뒤 틀린 값이 보인다.
    """

    #  값은 `presenter._plan` 이 품목을 앞에 붙인 「배추 · 보수」 모양이다. `presenter.plan_label`
    #     (대시보드도 이것을 부른다)과 말로 한 승인(`MasterConsole.planLabel`)이 그 모양을 쪼개
    #     읽는다.
    key: str = Field(description="품목 · 안 이름 (예: 배추 · 보수)")
    coverage: str = Field(description="며칠치인가")
    knob: str = Field(description="무엇으로 조절한 안인가")
    qty_kg: float = Field(description="사는 양 (kg)")
    amount_krw: int = Field(description="예상 금액 (원)")
    unit_price: int = Field(description="등급 단가 (원/kg)")
    grade: str = Field(description="배정된 등급")
    max_price: int = Field(description="재무 스트레스 기준 (amount_max_krw = qty × 이것)")
    cut_unit_price: int | None = Field(
        default=None,
        description=(
            "컷 기준 단가 (원/kg). 매입단가가 이보다 비싸면 사지 않는다. None 이면 그 실행 "
            "기록에 이 칸이 없던 것이고, `max_price` 로 대신 채우지 않는다"
        ),
    )
    legs: Table = Field(description="회차 — 언제 사서 언제 오나")
    payments: Table = Field(description="언제 얼마 내나")
    reasons: list[Reason] = Field(description="이 안을 왜 냈나. 여섯 갈래를 다 채운다")
    risks: list[str] = Field(description="걸리는 것. 비어 있으면 안 적는다")
    #  요청(품목·날) 단위다. 같은 요청에서 다른 안이 결정되면 이 안도
    #     대기가 아니다 — 「후보」 낱말(`state`)은 그대로 둔다.
    pending: bool = Field(
        description=(
            "이 안이 속한 요청(품목·날)에 아직 결정이 없으면 참. 같은 요청의 다른 안이 "
            "결정되면 거짓이다"
        )
    )
    approved: bool = Field(default=False, description="이미 승인된 안인가")
    #  `approved` 를 지우지 않는다 — 읽는 자리가 있다: 대시보드 서버
    #     (`api/dashboard/presenter.py` `_state` → `master/domain/plan_state.state_of(approved=…)`).
    #     매입 화면은 `state` 를 읽고 이 칸은 안 읽는다.
    #     `state` 는 그것이 못 가르는 것(승인 vs 실매입 기록됨)을 마저 가른다.
    state: str = Field(
        default="후보",
        description=(
            "이 안이 실제로 어느 상태인가 — 후보 · 승인됨 · 매입 기록됨 · 반려 중 하나. "
            "낱말과 판정 규칙은 마스터의 매입안 상태 규칙(`app/master/domain/plan_state.py`) "
            "한 곳이 정하고 대시보드도 같은 규칙을 쓴다. 화면은 이 넷 밖의 말을 만들지 않는다."
        ),
    )
    #  말로 한 승인이 짚을 자리다. 콘솔에서 "기본으로 해" 라고 하면 화면이 이 목록에서
    #     라벨로 안을 찾아 `target_request_id` 로 싣는다. 라벨만으로는 어느 실행의 안인지 짚을
    #     수 없어, 이 칸이 없으면 그 채팅에서 방금 만든 안이 아닐 때 승인이 멈춘다
    #     (`MasterConsole.confirm`).
    request_id: str | None = Field(
        default=None,
        description="이 안을 낸 실행의 업무 키. 못 읽으면 None 이고 지어내지 않는다",
    )
    #  업무 키 하나에 실행이 여러 행이다 (실측 75행). 그 사이 재실행이 있으면
    #     업무 키만으로는 본 것과 다른 안이 승인된 것으로 남는다 — 그래서 행 id 를
    #     짝으로 싣는다 (`master/service/decision.py` 의 `history_run_id` 와 같은 값).
    history_run_id: str | None = Field(
        default=None,
        description="이 안을 낸 실행 이력 행 id(master_agent_runs.run_id). 못 읽으면 None",
    )
    #  `None` 은 «못 읽었다» 가 아니라 «걷기 밖» 이다. 축이 붙기 전에 만든
    #     실행이거나 손으로 돌린 것이고, 그 사실이 화면에 보여야 한다 (마스터 청구).
    #     걷기와 손 실행이 같아 보이면 보는 사람이 둘을 한 세상으로 읽는다.
    sim_run_id: str | None = Field(
        default=None,
        description="어느 걷기의 실행인가. None 이면 걷기 밖(손 실행·축이 생기기 전)이다",
    )


class PurchaseTab(BaseModel):
    stats: list[Stat] = Field(description="오늘 제안 · 승인 대기 · 확정 매입액 · 입고 예정")
    plans: list[Plan] = Field(description="오늘 낸 안들. 비면 화면이 «안이 없다» 고 적는다")
    plans_note: Note = Field(description="안이 왜 이 개수인가")
    #  «사람이» 라고 쓰지 않는다 (마스터 통보 「백필 승인은 사람 승인이 아닙니다」). 걷기
    #     구간은 `decided_by="AUTO-BACKFILL"` 로 채운다. 이 표에는 사람이 누른 것과 자동으로
    #     채운 것이 같이 실리므로 «사람이 고른 뒤에» 는 거짓말이 된다. «승인을 거친 뒤» 는
    #     두 경우 모두 참이다.
    #
    #  미결정: «누가 승인했나» 를 이 표에 칸으로 더하는 것은 아직 하지 않았다. 원장에 그 값이
    #     없고(`purchases` 에 승인자 칸 없음), 마스터가 `purchases.decision_id`(FK)로
    #     `master_decisions` 를 가리키게 하기로 정했다. 칸이 선 뒤에 조인해 읽는다.
    committed: Table = Field(description="승인을 거친 뒤에 생기는 확정 매입")
    #  「승인 전에는 표가 빈다는 안내」는 빈 표 문구(`committed.empty_text`)의 일이고,
    #     이 글은 어떤 줄을 봤고 무엇을 뺐나를 적는다.
    committed_note: Note = Field(
        description="어떤 줄을 봤나 — 기준일까지 줄 수 · 도착일을 못 맞춘 줄 · 다른 걷기라 뺀 줄"
    )
    source: Source = Field(description="예시값인지 실제 값인지")
