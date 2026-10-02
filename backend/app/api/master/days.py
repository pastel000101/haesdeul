"""하루 단계 일곱 — `POST /master/days/{as_of}/<단계>`.

단계: open · retry-transitions · receive · collect · issue-receivables · ship · close.

HTTP 만: 실행 축을 검사(`_walk_axis`)하고 하루 시뮬레이션(`service/scheduler.run_scheduled_day`)이
부르는 것과 같은 `master/service/<단계>.py` 경계 함수를 부른다. 연결 대여 · commit · rollback 은
그 경계 함수가 쥔다.
"""

from datetime import date

from fastapi import APIRouter, HTTPException, status

from app.master.domain.sim_run import BURN_IN_SIM_RUN_ID
from app.master.schemas.closing import ClosingOut
from app.master.schemas.collection import CollectionOut
from app.master.schemas.day_open import DayOpenOut
from app.master.schemas.inbound import InboundOut
from app.master.schemas.outbound_flow import OutboundOut
from app.master.schemas.pending_transition import RetryOut
from app.master.schemas.receivable import ReceivableOut
from app.master.service.closing import close_day as run_close_day
from app.master.service.collection import collect_receipts as run_collect_receipts
from app.master.service.day_open import open_day as run_open_day
from app.master.service.inbound import receive_arrivals as run_receive_arrivals
from app.master.service.outbound_flow import ship_due_sales as run_ship_due_sales
from app.master.service.pending_transition import (
    retry_pending_transitions as run_retry_pending_transitions,
)
from app.master.service.receivable import issue_receivables as run_issue_receivables

router = APIRouter(prefix="/master", tags=["master"])


def _walk_axis(sim_run_id: str) -> str:
    """장부를 바꾸는 손 호출이 쓸 실행 축. 요청이 준 값만 쓴다.

    기본값으로 메우지 않는다. 축이 빠지면 번인 상수로 떨어지고, 그러면 손으로 부른 하루가
    번인 장부에 쌓인다. 실행 축 사고는 "축이 빠졌거나 기본값으로 메워진" 자리에서 난다.

    번인은 거부한다. 번인은 기초 상태 시드 전용이고 걷지 않는다
    (`repository/sim_run_open.reset_sim_run_ledger` · `cli/backfill_runner.run_backfill` 과 같은
    태도).

    :raises HTTPException: 400 — 축이 비었거나 번인 실행일 때.
    """
    axis = sim_run_id.strip()
    if not axis:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="sim_run_id 없이 장부를 바꿀 수 없다 — 어느 실행인지를 지어내지 않는다",
        )
    if axis == BURN_IN_SIM_RUN_ID:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"번인 실행({BURN_IN_SIM_RUN_ID})의 장부는 손으로 안 바꾼다"
                " — 모든 실행이 그 장부를 기초 상태로 읽는다. 걷는 실행의 축을 넘겨라"
            ),
        )
    return axis

@router.post(
    "/days/{as_of}/open",
    response_model=DayOpenOut,
    summary="하루를 연다 — 그날 상태 행을 파트마다 보장한다",
)
def master_open_day(as_of: date, sim_run_id: str) -> DayOpenOut:
    """`as_of` 날 상태 행을 파트마다 보장한다. 없으면 전날 행을 물려받아 만든다.

    하루를 넘기는 것은 명시적 호출로만 한다. `run_procurement` 은 시작할 때 하루를
    자동으로 열지 않는다. 판단 한 번이 장부를 바꾸면 같은 `as_of` 로 다시 돌렸을 때
    같은 답이 나오지 않기 때문이다.

    하루가 열리면 결제기일이 지난 미수 채권을 수금 사건으로 옮기고, 그 결과를
    `collection_seed_status` 에 싣는다. 사건 생성이 실패해도 개장 결과는 바뀌지 않는다.

    멱등이다. 같은 날을 두 번 열면 두 번째 호출은 행을 만들지 않고 `ALREADY_OPENED`
    (파트별로는 `PART_ALREADY_OPENED`)를 돌려준다.

    | 상태 | 언제 |
    |---|---|
    | 200 | 열었다 · 이미 열려 있었다 · 못 열었다 · 상한을 넘겼다 — 모두 그날의 결과다 |
    | 400 | `sim_run_id` 가 비었거나 번인 실행이다 |
    | 422 | `sim_run_id` 쿼리를 주지 않았다 |

    200 의 `status` 는 `OPENED` · `ALREADY_OPENED` · `NOT_OPENED`(파트 실패 · 미등록 ·
    적재 실패) · `REJECTED_GAP`(상한 초과) 중 하나다.

    실패 처리: 미등록 · 상한 초과는 오류가 아니라 상태로 200 에 실린다. 적재가
    실패하면 롤백되어 전날 상태 그대로이고, 사유는 `reason` 에 실린다.
    """
    return run_open_day(as_of, sim_run_id=_walk_axis(sim_run_id))


@router.post(
    "/days/{as_of}/retry-transitions",
    response_model=RetryOut,
    summary="미적용 전이를 다시 세운다 — 개장 뒤이고 입고 앞이다",
)
def master_retry_pending_transitions(as_of: date, sim_run_id: str) -> RetryOut:
    """`as_of` 이전에 승인됐지만 매입 원장에 반영되지 않은 전이를 다시 세운다.

    다시 선 승인은 매입 원장 · 매입채무 · 입고 일정을 만든다. 사람 승인은 실매입
    기록이 있을 때만 다시 세운다. 하루 시뮬레이션(`run_scheduled_day`)이 부르는
    `retry_pending_transitions` 를 그대로 부르므로, 손으로 부른 결과와 하루 시뮬레이션
    결과가 같은 코드를 지난다.

    순서: 개장 뒤, 입고 앞이다. 승인일이 `D` 면 상태는 `D+1` 에 서므로 전날 기록한
    실매입가는 다음 날 이 단계에서 원장에 반영된다. 입고 뒤로 미루면 그날 도착분이
    하루 더 밀린다.

    | 상태 | 언제 |
    |---|---|
    | 200 | 다시 세웠다 · 미적용이 없었다 · 조회가 실패했다 — 모두 그날의 결과다 |
    | 400 | `sim_run_id` 가 비었거나 번인 실행이다 |
    | 422 | `sim_run_id` 쿼리를 주지 않았다 |

    200 의 `status` 는 `RAN`(미적용을 찾아 다시 세웠다 — 전부 성공했다는 뜻은 아니다) ·
    `NOTHING_DUE`(미적용이 없었다) · `FAILED`(미적용 조회가 실패했다) 중 하나다.

    실패 처리: 한 건이 실패해도 나머지를 계속 세운다. 건별 결과(`APPLIED` ·
    `NOT_APPLIED` · `FAILED` · `NOT_BUILDABLE`)는 `retried` 에 실린다.
    """
    return run_retry_pending_transitions(as_of, sim_run_id=_walk_axis(sim_run_id))


@router.post(
    "/days/{as_of}/receive",
    response_model=InboundOut,
    summary="그날 도착분을 받는다 — 개장 다음이고 판단과는 별개다",
)
def master_receive_arrivals(as_of: date, sim_run_id: str) -> InboundOut:
    """`as_of` 에 도착 예정인 입고를 파트마다 받는다.

    입고는 Receipt · Lot · Inventory Move 를 만들어 장부를 바꾸므로 판단
    (`run_procurement`) 안에 넣지 않고 따로 부른다. 판단 안에 넣으면 같은 `as_of` 로
    다시 돌렸을 때 같은 답이 나오지 않는다. 개장 · 입고 · 판단을 상위 호출 하나로 묶지도
    않는다. 묶으면 "개장 성공 · 입고 `BLOCKED` · 판단 성공" 같은 조합을 상태값 하나로
    표현할 수 없다. 단계마다 상태가 따로 있고, 순서는 부르는 쪽이 지킨다.

    개장 확인: 그날이 열리지 않았으면 받지 않고 `NOT_OPENED` 를 돌려준다. 이때
    `next_action` 에는 개장 Gate 가 준 다음 조치(예: `OPEN_DAY_REQUIRED`)가 그대로 실린다.

    달력일 기준이다. 창고는 토요일에도 받으므로 이 호출은 실행일 판정을 보지 않는다.
    토요일에는 개장 · 입고가 돌고 `run_procurement` 만 돌지 않는다.

    | 상태 | 언제 |
    |---|---|
    | 200 | 받았다 · 받을 게 없었다 · 막혔다 · 안 열렸다 · 실패했다 — 모두 그날의 결과다 |
    | 400 | `sim_run_id` 가 비었거나 번인 실행이다 |
    | 422 | `sim_run_id` 쿼리를 주지 않았다 |

    실패 처리: `FAILED` 도 200 이다. 롤백되어 아무것도 바뀌지 않은 상태이고, 사유는
    `reason` 에 실린다.
    """
    return run_receive_arrivals(as_of, sim_run_id=_walk_axis(sim_run_id))


@router.post(
    "/days/{as_of}/collect",
    response_model=CollectionOut,
    summary="그날 수금 사건을 반영한다 — 개장 다음이고 판단과는 별개다",
)
def master_collect_receipts(as_of: date, sim_run_id: str) -> CollectionOut:
    """`as_of` 의 수금 사건을 파트마다 반영한다.

    수금은 `receivables` 와 `finance_states` 를 바꾸므로 판단(`run_procurement`) 안에
    넣지 않고 따로 부른다. 판단 안에 넣으면 판단 한 번이 현금을 움직여, 같은 `as_of` 로
    다시 돌렸을 때 같은 답이 나오지 않는다.

    마스터는 수금 대상이나 수금액을 스스로 정하지 않는다.

      ```text
      Finance   실제 수금 사건의 정본 의미 — receivable_id · collection_date
                · cumulative target_received_total_krw · (sim_run_id, financing_mode)
      Master    날짜에 해당하는 사건을 운반하고 호출한다
      Sales     계약 결제조건 · 채권 발생의 상업적 원천
      ```

    주의: `due_date` 경과는 수금이 아니다. 기일이 지난 것을 돈이 들어온 것으로 다루면
    없는 현금으로 매입 판단이 돈다.

    개장 확인: 그날이 열리지 않았으면 반영하지 않고 `NOT_OPENED` 를 돌려준다. 이때
    `next_action` 에는 개장 Gate 가 준 다음 조치(예: `OPEN_DAY_REQUIRED`)가 그대로 실린다.

    달력일 기준이다. 입금은 토요일에도 찍히므로 이 호출은 실행일 판정을 보지 않는다.
    토요일에는 개장 · 입고 · 수금이 돌고 `run_procurement` 만 돌지 않는다.

    | 상태 | 언제 |
    |---|---|
    | 200 | 수금했다 · 들어올 게 없었다 · 막혔다 · 안 열렸다 · 실패했다 — 모두 그날의 결과다 |
    | 400 | `sim_run_id` 가 비었거나 번인 실행이다 |
    | 422 | `sim_run_id` 쿼리를 주지 않았다 |

    실패 처리: `FAILED` 도 200 이다. 롤백되어 아무것도 바뀌지 않은 상태이고, 사유는
    `reason` 에 실린다.
    """
    return run_collect_receipts(as_of, sim_run_id=_walk_axis(sim_run_id))


@router.post(
    "/days/{as_of}/issue-receivables",
    response_model=ReceivableOut,
    summary="그날 확정된 판매를 채권으로 세운다 — 수금보다 앞이고 판단과는 별개다",
)
def master_issue_receivables(as_of: date, sim_run_id: str) -> ReceivableOut:
    """`as_of` 가 `sale_date` 인 확정 판매를 파트마다 채권으로 세운다.

    채권이 서면 `receivables` 에 행이 늘고 `finance_states.receivables_krw` 가 올라가므로
    판단(`run_procurement`) 안에 넣지 않고 따로 부른다. 판단 안에 넣으면 판단 한 번이
    채권 잔액을 움직여, 같은 `as_of` 로 다시 돌렸을 때 같은 답이 나오지 않는다.
    `receivables_krw` 는 매입 cap 이 보는 값이다.

    하루 시뮬레이션(`run_scheduled_day`)이 부르는 함수와 같은 함수를 부르므로, 손으로
    부른 결과와 하루 시뮬레이션 결과가 같은 코드를 지난다.

    마스터는 채권 금액도 기일도 스스로 정하지 않는다.

      ```text
      Sales     확정된 판매의 정본 — sale_date · collection_due_date · total_amount_krw
      Finance   채권 원장의 정본 — receivable_id · (sim_run_id, financing_mode) 축
      Master    그날 확정분을 운반하고 호출한다
      ```

    제약: `collection_due_date` 가 비어 있으면 기일을 지어내지 않고 `BLOCKED` 로 막는다.
    마스터가 만든 기일로 수금 판정이 돌면 틀려도 오류가 나지 않기 때문이다.

    멱등이다. 같은 날을 두 번 불러도 채권은 하나다. 두 번째 호출은 `ISSUED` 이면서
    새로 만든 건수(`created`)가 0 이고, 이것이 정상이다.

    개장 확인: 그날이 열리지 않았으면 세우지 않고 `NOT_OPENED` 를 돌려준다. 이때
    `next_action` 에는 개장 Gate 가 준 다음 조치(예: `OPEN_DAY_REQUIRED`)가 그대로 실린다.

    달력일 기준이다. `sales.sale_date` 가 정본이고 실행일 달력으로 밀지 않는다.

    | 상태 | 언제 |
    |---|---|
    | 200 | 세웠다 · 세울 게 없었다 · 막혔다 · 안 열렸다 · 실패했다 — 모두 그날의 결과다 |
    | 400 | `sim_run_id` 가 비었거나 번인 실행이다 |
    | 422 | `sim_run_id` 쿼리를 주지 않았다 |

    실패 처리: `FAILED` 도 200 이다. 롤백되어 아무것도 바뀌지 않은 상태이고, 사유는
    `reason` 에 실린다.
    """
    return run_issue_receivables(as_of, sim_run_id=_walk_axis(sim_run_id))


@router.post(
    "/days/{as_of}/ship",
    response_model=OutboundOut,
    summary="그날 나갈 판매를 내보낸다 — 판단 뒤이고 마감 앞이다",
)
def master_ship_due_sales(as_of: date, sim_run_id: str) -> OutboundOut:
    """`as_of` 에 나갈 확정 판매를 순서대로 내보낸다.

    출고는 예약 · 할당 · 재고 이동을 만들어 장부를 바꾸므로 따로 부르는 단계다.

    주의: 판매 판단이 아니다. 이미 확정된 판매를 내보내는 단계이고, 판매 승인 자리로
    쓰지 않는다.

    하루 시뮬레이션(`run_scheduled_day`)이 부르는 `ship_due_sales` 를 그대로 부르므로,
    손으로 부른 결과와 하루 시뮬레이션 결과가 같은 코드를 지난다.

    순서: 판단 뒤, 마감 앞이다. 마감 뒤에 두면 그날 확정된 안이 다음 날에야 나간다.
    Lot 선택은 이 단계가 하지 않는다. 물류가 잠금과 함께 고른다.

    | 상태 | 언제 |
    |---|---|
    | 200 | 내보냈다 · 나갈 게 없었다 · 시도하지 못했다 — 모두 그날의 결과다 |
    | 400 | `sim_run_id` 가 비었거나 번인 실행이다 |
    | 422 | `sim_run_id` 쿼리를 주지 않았다 |

    실패 처리: `FAILED` 도 200 이며 연결 · 조회 실패로 시도조차 하지 못한 것이다.
    확보량이 모자란 `SHORT` 는 오류가 아닌 사업 결과로, 품목별로 `items` 에 실린다.
    """
    return run_ship_due_sales(as_of, sim_run_id=_walk_axis(sim_run_id))


@router.post(
    "/days/{as_of}/close",
    response_model=ClosingOut,
    summary="그날을 닫는다 — 하루의 맨 끝이다. 숫자는 재무가 낸다",
)
def master_close_day(as_of: date, sim_run_id: str) -> ClosingOut:
    """`as_of` 를 파트마다 닫는다. 출고 뒤에 오는 하루의 마지막 단계다.

    그날이 닫히면 `daily_closings` 에 한 행이 서고, 그것이 손익 곡선의 한 점이 된다.

    마스터는 숫자를 계산하지 않는다.

      ```text
      마스터   "그날을 닫아 달라" 고 부르고, 결과 상태를 받아 적는다
      재무     그날 숫자 계산과 daily_closings 적재
      ```

    앱 기동 시 재무 마감 어댑터가 마감 등록소에 등록된다. 등록소가 비어 있는 실행에서만
    `NOTHING_DUE` 와 함께 `missing` 에 미등록 파트가 실린다.

    하루 시뮬레이션(`run_scheduled_day`)이 부르는 함수와 같은 함수를 부르므로, 손으로
    부른 결과와 하루 시뮬레이션 결과가 같은 코드를 지난다.

    `sim_run_id` 는 요청이 준다. `daily_closings` 의 PK 가 `(sim_run_id, close_date)` 라
    어느 실행의 장부인지 없으면 행이 앉을 자리가 정해지지 않는다. 번인 실행은 거부한다.

    개장 확인: 그날이 열리지 않았으면 닫지 않고 `NOT_OPENED` 를 돌려준다. 이때
    `next_action` 에는 개장 Gate 가 준 다음 조치(예: `OPEN_DAY_REQUIRED`)가 그대로 실린다.

    제약: 이 경로는 장부 관문 사유를 받지 않는다. 장부 관문이 막은 날 마감을 `BLOCKED`
    로 적는 것은 하루 시뮬레이션(`run_scheduled_day`)이 한다. 이 경로의 `BLOCKED` 는
    파트 어댑터가 막았다고 답한 경우다.

    | 상태 | 언제 |
    |---|---|
    | 200 | 닫았다 · 닫을 게 없었다 · 막혔다 · 안 열렸다 · 실패했다 — 모두 그날의 결과다 |
    | 400 | `sim_run_id` 가 비었거나 번인 실행이다 |
    | 422 | `sim_run_id` 쿼리를 주지 않았다 |

    실패 처리: `FAILED` 도 200 이다. 롤백되어 아무것도 바뀌지 않은 상태이고, 사유는
    `reason` 에 실린다.
    """
    return run_close_day(as_of, sim_run_id=_walk_axis(sim_run_id))
