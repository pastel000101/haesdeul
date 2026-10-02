"""봉투 요청을 판매 제안 입력으로 — payload 에서 칸을 고르고 실행 식별자를 붙인다.

마스터(`sales_port`)와 운영 화면(`POST /sales/console-proposal`)이 같은 봉투 모양으로
들어와 `service/proposal_generation.py` 에서 같은 길을 탄다.
"""

from collections.abc import Callable, Mapping
from typing import Any

from app.contracts.envelope import AgentRequest
from app.sales.schemas.proposal import SalesProposalInput


def proposal_input_data(request: AgentRequest, run_id: str) -> dict[str, Any]:
    """판매 제안 입력이 될 칸. 거래처 결제일수를 채우고 검증하는 것은 부르는 쪽이다."""
    data: dict[str, Any] = {
        key: value
        for key, value in request.payload.items()
        if key in SalesProposalInput.model_fields
    }
    feedback_attempt = request.payload.get("feedback_attempt", 0)
    data["execution_identity"] = {
        "request_id": request.context.request_id,
        "run_id": run_id,
        "as_of": request.context.as_of,
        "policy_version": request.context.policy_version,
        "feedback_attempt": feedback_attempt,
    }
    if "feedback_attempt" in request.payload:
        data["feedback_attempt"] = feedback_attempt
    if int(feedback_attempt or 0) > 0:
        data["is_refeed"] = True
    return data


def with_partner_payment_days(
    data: Mapping[str, Any],
    *,
    lookup: Callable[[str], int | None],
) -> dict[str, Any]:
    """요청이 결제일수를 말하지 않았으면 거래처 계약 결제일수를 싣는다.

    ```text
    요청이 결제일수를 들고 있다             → 그대로 둔다 (사람·걷기 규칙이 정한 값이 이긴다)
    계약 이행                               → 그대로 둔다 (계약서가 정본이다)
    갱신인데 이전 계약이 결제일수를 들고 있다 → 그대로 둔다 (계약 상속이 이긴다)
    그 밖, 거래처가 있고 계약 결제일수가 있다 → partners.sales_collection_days 를 싣는다
    거래처 계약값을 못 읽었다                → 그대로 둔다 → 재무가 «결제일수 없음» 으로 닫는다
    ```

    정본은 거래처 계약이다 (`partners.sales_collection_days`). 화면 입력칸에 30 을
    미리 채워 두거나 코드에 일수를 박으면, 거래처와 7일 결제로 계약을 바꾼 날에도
    판매안은 30일로 선다.

    0일은 값이다 — «당일 결제» 라는 정해진 조건이라 `None` 과 가른다.

    계약값을 읽는 `lookup` 은 부르는 쪽이 넘긴다 — 이 함수는 DB 를 부르지 않는다.
    판매 후보 생성은 `service/proposal_generation.py` 가 조회 함수를 넘긴다.
    """
    user = data.get("user_request")
    if not isinstance(user, Mapping) or user.get("preferred_payment_days") is not None:
        return dict(data)
    mode = data.get("business_mode")
    contract = data.get("contract_context")
    contract = contract if isinstance(contract, Mapping) else {}
    if mode == "CONTRACT_FULFILLMENT":
        return dict(data)
    if mode == "CONTRACT_PROPOSAL_RENEWAL" and contract.get("contract_payment_days") is not None:
        return dict(data)
    partner_id = user.get("partner_id") or contract.get("partner_id")
    if not isinstance(partner_id, str) or not partner_id.strip():
        return dict(data)
    days = lookup(partner_id)
    if days is None:
        return dict(data)
    return {**data, "user_request": {**user, "preferred_payment_days": days}}
