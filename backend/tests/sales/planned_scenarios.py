"""그래프 밖에서 세 안을 세운다 — **그래프가 하는 순서 그대로.**

★ 2026-09-29 BL-013: 안 생성(`domain/proposal.generate_scenarios`)은 전략 계획을 **받기만**
  한다. 전에는 계획을 안 주면 그 함수(`proposal._generate_scenarios`)가 전략 Planner 를
  불렀고(그 안에서 모델이 불린다), 검사들이 그 길로 안을 세웠다. 이제 검사도 그래프
  (`service/proposal.py` 의 `plan_strategy` → `generate_candidates`)와 같은 순서로 계획을 먼저
  세우고 안을 만든다. 판매 테스트 conftest 가 모델을 꺼 두므로, 모델을 갈아 끼우지 않은
  검사에서 계획은 규칙 템플릿이다 — 종전과 같다.
"""

from app.sales.domain.proposal import all_feedback_replies, generate_scenarios
from app.sales.schemas.proposal import SalesProposalInput, SalesScenario
from app.sales.service.strategy import plan_strategies


def plan_and_generate_scenarios(request: SalesProposalInput) -> list[SalesScenario]:
    plan, _signals = plan_strategies(request, all_feedback_replies(request))
    return generate_scenarios(request, plan)
