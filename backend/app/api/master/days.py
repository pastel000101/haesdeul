"""하루 단계 일곱 — `POST /master/days/{as_of}/<단계>`.

단계: open · retry-transitions · receive · collect · issue-receivables · ship · close.

HTTP 만: 실행 축을 검사(`_walk_axis`)하고 **하루 시뮬레이션(`service/scheduler.run_scheduled_day`)이
부르는 것과 같은** `master/service/<단계>.py` 경계 함수를 부른다. 연결 대여 · commit · rollback 은
그 경계 함수가 쥔다.

★ 2026-09-30 재구성 BL-019: `app/master/router.py` 에서 옮겼다 — 핸들러 이름 · docstring(OpenAPI
  설명) · URL · 상태 코드 · 문구 그대로.
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
    """장부를 바꾸는 손 호출이 쓸 실행 축. **요청이 준 값만 쓴다.**

    🔴 **기본값으로 메우지 않는다** (2026-09-14). 전에는 `/days/{as_of}/*` 가 축을
       안 받아 번인 상수로 떨어졌고, 그래서 손으로 부른 하루가 **번인 장부에** 쌓였다.
       실행 축 사고는 지금까지 전부 *"축이 빠졌거나 기본값으로 메워진"* 자리였다.

    🔴 **번인은 거부한다.** 번인은 기초 상태 시드 전용이고 걷지 않는다
       (`sim_run_open.reset_sim_run` · `backfill_runner.run_backfill` 과 같은 태도).

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
    """`as_of` 날 상태 행이 없으면 전날에서 물려받아 만든다.

    🔴 **명시적 호출이다. 실행의 부작용이 아니다.** `run_procurement` 이 시작할 때
       자동으로 열지 않는다 — 판단 한 번이 장부를 바꾸면 *"같은 as_of 로 백번 돌려도
       같은 답"* 이 깨진다. 하루가 넘어가는 것은 **사건**이고, 사건에는 자기 자리가 있다.

    ★ **멱등이다.** 같은 날을 두 번 열면 두 번째는 아무것도 안 한다 — 파트마다
      `opened` 가 빈 목록으로 나가는 것이 *"이미 열려 있었다"* 다.

    | 상태 | 언제 |
    |---|---|
    | 200 | 열었다 · 이미 열려 있었다 · 막혔다 · 미등록이다 — 전부 **그날의 사실**이다 |
    | 400 | `sim_run_id` 가 비었거나 번인 실행이다 (`_walk_axis`) |
    | 422 | `sim_run_id` 쿼리를 안 줬다 |

    ★ **실패도 200 이다** (`/master/request` 와 같은 태도). 미등록·상한 초과는 오류가
      아니라 상태이고, 적재 실패는 롤백되어 어제 그대로다 — 사유가 본문에 실린다.
    """
    return run_open_day(as_of, sim_run_id=_walk_axis(sim_run_id))


@router.post(
    "/days/{as_of}/retry-transitions",
    response_model=RetryOut,
    summary="미적용 전이를 다시 세운다 — 개장 바로 뒤이고 입고 앞이다",
)
def master_retry_pending_transitions(as_of: date, sim_run_id: str) -> RetryOut:
    """어제까지의 승인 중 **원장에 안 닿은 것**을 다시 세운다.

    🔴 **왜 자기 엔드포인트인가.**

      바로 위 `master_open_day` 가 적어 둔 원칙 그대로다 — *"명시적 호출이다. 실행의
      부작용이 아니다. 사건에는 자기 자리가 있다."* **전이도 사건이다.** 다시 선
      승인은 매입 원장 · 매입채무 · 입고 일정을 만든다.

    🔴 **여기서 새 로직을 만들지 않는다.** `retry_pending_transitions` 를 그대로
       부른다 — `run_scheduled_day` 가 부르는 함수와 사람이 부르는 함수가 같아야
       손으로 넘긴 하루와 걷기가 같은 코드를 지난다 (`/days/{as_of}/close` 와 같은 말).

    ★ **개장 바로 뒤, 입고 앞이다** (`scheduler.py` 가 못 박은 순서). 승인일이 `D` 면
      상태가 설 날은 `D+1` 이라 어제 적은 실매입가는 **오늘 이 단계에서** 원장에 선다.
      뒤로 밀면 그날 도착이 하루 더 밀린다.

    | 상태 | 언제 |
    |---|---|
    | 200 | 다시 세웠다 · 미적용이 없었다 · 못 찾았다 — 전부 **그날의 사실**이다 |
    | 400 | `sim_run_id` 가 비었거나 번인 실행이다 (`_walk_axis`) |
    | 422 | `sim_run_id` 쿼리를 안 줬다 |

    ★ **실패도 200 이다** (`/days/{as_of}/open` 과 같은 태도). 하나가 터져도 나머지를
      계속 세우고, 결과는 `retried` 마다 어휘로 실린다.
    """
    return run_retry_pending_transitions(as_of, sim_run_id=_walk_axis(sim_run_id))


@router.post(
    "/days/{as_of}/receive",
    response_model=InboundOut,
    summary="그날 도착분을 받는다 — 개장 다음이고 판단과는 별개다",
)
def master_receive_arrivals(as_of: date, sim_run_id: str) -> InboundOut:
    """`as_of` 에 도착 예정인 것을 파트마다 받는다.

    🔴 **왜 자기 엔드포인트인가** (물류 물음 2026-09-07).

      바로 위 `master_open_day` 가 적어 둔 원칙 그대로다 — *"명시적 호출이다. 실행의
      부작용이 아니다. 사건에는 자기 자리가 있다."* **입고도 사건이다.** 도착분을
      받으면 Receipt · Lot · Inventory Move 가 생기고 그것은 장부가 바뀌는 것이다.

    ★ **`run_procurement` 안으로 넣지 않는다.** 넣으면 판단 한 번이 재고를 늘리고,
      *"같은 `as_of` 로 백번 돌려도 같은 답"* 이 깨진다. 개장을 판단 밖에 둔 이유와
      같다.

    ⚠️ **상위 `run_day` 하나로 묶지도 않는다.** 물류가 그 안을 주셨는데(`B`), 묶으면
      **실패 조합을 한 응답으로 못 낸다.**

      ```text
      개장 성공 · 입고 BLOCKED · 판단 성공     ← 이 상태를 한 status 로 어떻게 적나
      ```

      `#316` 에서 `BLOCKED` 를 `NOTHING_DUE` 로 접었다가 물류가 잡아 준 것과 같은
      병이다. **사건 셋은 상태 셋이고, 순서는 부르는 쪽이 지킨다.**

    ★ **순서는 문장이 아니라 Gate 가 지킨다.** 안 열린 날 부르면 `NOT_OPENED` 로
      돌아서고 `next_action` 이 `OPEN_DAY_REQUIRED` 를 준다 — 전에는 docstring 에
      *"`open_day` 다음이다"* 라고만 적혀 있어 코드가 아무것도 안 봤다.

    ⚠️ **달력일이다.** 창고는 토요일에도 받는다. 그래서 이 호출은 실행일 판정을 안
      본다 — 토요일에 `open_day` · `receive` 는 돌고 `run_procurement` 만 안 돈다.

    | 상태 | 언제 |
    |---|---|
    | 200 | 받았다 · 받을 게 없었다 · 막혔다 · 안 열렸다 — 전부 **그날의 사실**이다 |
    | 400 | `sim_run_id` 가 비었거나 번인 실행이다 (`_walk_axis`) |
    | 422 | `sim_run_id` 쿼리를 안 줬다 |

    ★ **실패도 200 이다** (`/days/{as_of}/open` 과 같은 태도). `FAILED` 는 롤백되어
      아무것도 안 바뀐 상태이고, 사유가 본문에 실린다.
    """
    return run_receive_arrivals(as_of, sim_run_id=_walk_axis(sim_run_id))


@router.post(
    "/days/{as_of}/collect",
    response_model=CollectionOut,
    summary="그날 수금 사건을 반영한다 — 개장 다음이고 판단과는 별개다",
)
def master_collect_receipts(as_of: date, sim_run_id: str) -> CollectionOut:
    """`as_of` 의 수금 사건을 파트마다 반영한다.

    🔴 **왜 자기 엔드포인트인가.**

      바로 위 두 형제가 적어 둔 원칙 그대로다 — *"명시적 호출이다. 실행의 부작용이
      아니다. 사건에는 자기 자리가 있다."* **수금도 사건이다.** 채권이 돈으로 들어오면
      `receivables` 와 `finance_states` 가 바뀌고 그것은 장부가 바뀌는 것이다.

    ★ **`run_procurement` 안으로 넣지 않는다.** 넣으면 **판단 한 번이 현금을 움직이고**
      *"같은 `as_of` 로 백번 돌려도 같은 답"* 이 깨진다. 개장·입고를 판단 밖에 둔
      이유와 같고, 현금이라 더 그렇다.

    🔴 **마스터는 수금 대상이나 수금액을 스스로 정하지 않는다** (재무 결정 2026-09-07).

      ```text
      Finance   실제 수금 사건의 정본 의미 — receivable_id · collection_date
                · cumulative target_received_total_krw · (sim_run_id, financing_mode)
      Master    날짜에 해당하는 사건을 **운반하고 호출**한다
      Sales     계약 결제조건·채권 발생의 상업적 원천
      ```

      ⚠️ **`due_date` 경과는 수금이 아니다.** 기일이 지난 것과 돈이 들어온 것을 접으면
        **없는 현금으로 매입 판단이 돈다.**

    ★ **순서는 문장이 아니라 Gate 가 지킨다.** 안 열린 날 부르면 `NOT_OPENED` 로
      돌아서고 `next_action` 이 `OPEN_DAY_REQUIRED` 를 준다 — `/days/{as_of}/receive`
      와 같은 모양이다.

    ⚠️ **달력일이다.** 입금은 토요일에도 찍힌다. 그래서 이 호출은 실행일 판정을 안
      본다 — 토요일에 `open_day` · `receive` · `collect` 는 돌고 `run_procurement` 만
      안 돈다.

    | 상태 | 언제 |
    |---|---|
    | 200 | 수금했다 · 들어올 게 없었다 · 막혔다 · 안 열렸다 — 전부 **그날의 사실**이다 |
    | 400 | `sim_run_id` 가 비었거나 번인 실행이다 (`_walk_axis`) |
    | 422 | `sim_run_id` 쿼리를 안 줬다 |

    ★ **실패도 200 이다** (`/days/{as_of}/receive` 와 같은 태도). `FAILED` 는 롤백되어
      아무것도 안 바뀐 상태이고, 사유가 본문에 실린다.
    """
    return run_collect_receipts(as_of, sim_run_id=_walk_axis(sim_run_id))


@router.post(
    "/days/{as_of}/issue-receivables",
    response_model=ReceivableOut,
    summary="그날 확정된 판매를 채권으로 세운다 — 수금보다 앞이고 판단과는 별개다",
)
def master_issue_receivables(as_of: date, sim_run_id: str) -> ReceivableOut:
    """`as_of` 가 `sale_date` 인 확정 판매를 파트마다 채권으로 세운다.

    🔴 **왜 자기 엔드포인트인가.**

      바로 위 세 형제가 적어 둔 원칙 그대로다 — *"명시적 호출이다. 실행의 부작용이
      아니다. 사건에는 자기 자리가 있다."* **채권 발행도 사건이다.** 판매가 확정되면
      `receivables` 가 한 줄 늘고 `finance_states.receivables_krw` 가 올라가며,
      그것은 장부가 바뀌는 것이다.

    ★ **`run_procurement` 안으로 넣지 않는다.** 넣으면 판단 한 번이 채권 잔액을
      움직이고 *"같은 `as_of` 로 백번 돌려도 같은 답"* 이 깨진다. `receivables_krw` 는
      매입 cap 이 보는 값이라 더 그렇다.

    🔴 **`run_scheduled_day` 가 부르는 함수와 사람이 부르는 함수가 같다.** 둘이
      갈리면 손으로 부른 결과와 걷기 결과가 다른 코드를 지난다.

    🔴 **마스터가 채권 금액도 기일도 스스로 정하지 않는다** (판매·재무 결정 2026-09-09).

      ```text
      Sales     확정된 판매의 정본 — sale_date · collection_due_date · total_amount_krw
      Finance   채권 원장의 정본 — receivable_id · (sim_run_id, financing_mode) 축
      Master    그날 확정분을 **운반하고 호출**한다
      ```

      ⚠️ **`collection_due_date` 가 비어 있으면 지어내지 않고 막는다.** 기일을 마스터가
        발명하면 그 값으로 수금 판정이 돌고, 틀려도 에러가 안 난다.

    ★ **멱등이다.** 같은 날을 두 번 걸어도 채권은 하나다 — 두 번째는 `ISSUED` 인데
      새로 만든 건수가 0 이고, 그것이 정상이다.

    ★ **순서는 문장이 아니라 Gate 가 지킨다.** 안 열린 날 부르면 `NOT_OPENED` 로
      돌아서고 `next_action` 이 `OPEN_DAY_REQUIRED` 를 준다 — `/days/{as_of}/collect`
      와 같은 모양이다.

    ⚠️ **달력일이다.** `sales.sale_date` 가 정본이고 실행일 달력으로 밀지 않는다.

    | 상태 | 언제 |
    |---|---|
    | 200 | 세웠다 · 세울 게 없었다 · 막혔다 · 안 열렸다 — 전부 **그날의 사실**이다 |
    | 400 | `sim_run_id` 가 비었거나 번인 실행이다 (`_walk_axis`) |
    | 422 | `sim_run_id` 쿼리를 안 줬다 |

    ★ **실패도 200 이다** (`/days/{as_of}/collect` 와 같은 태도). `FAILED` 는 롤백되어
      아무것도 안 바뀐 상태이고, 사유가 본문에 실린다.
    """
    return run_issue_receivables(as_of, sim_run_id=_walk_axis(sim_run_id))


@router.post(
    "/days/{as_of}/ship",
    response_model=OutboundOut,
    summary="그날 나갈 판매를 내보낸다 — 판단 뒤이고 마감 앞이다",
)
def master_ship_due_sales(as_of: date, sim_run_id: str) -> OutboundOut:
    """`as_of` 에 나갈 확정 판매를 순서대로 내보낸다.

    🔴 **왜 자기 엔드포인트인가.**

      앞의 형제들이 적어 둔 원칙 그대로다 — *"명시적 호출이다. 실행의 부작용이
      아니다. 사건에는 자기 자리가 있다."* **출고도 사건이다.** 나가면 예약 · 할당 ·
      재고 이동이 서고 그것은 장부가 바뀌는 것이다.

    ★★ **판매 판단이 아니다.** 이미 확정된 판매를 내보내는 단계다 — 이름 때문에
      판매가 서 있는 것처럼 보였고, 그래서 걷기 179일에 판매 판단이 0건이었다
      (`scheduler.py`). 여기를 판매 승인 자리로 쓰지 않는다.

    🔴 **여기서 새 로직을 만들지 않는다.** `ship_due_sales` 를 그대로 부른다 —
       `run_scheduled_day` 가 부르는 함수와 사람이 부르는 함수가 같아야 손으로 넘긴
       하루와 걷기가 같은 코드를 지난다.

    ★ **마감 앞이다** (`scheduler.py` 가 못 박은 순서). 뒤에 두면 그날 확정된 안이
      다음 날에야 나갈 자리가 생긴다. Lot 선택은 여기 없다 — 물류가 잠금과 함께 돈다.

    | 상태 | 언제 |
    |---|---|
    | 200 | 내보냈다 · 나갈 게 없었다 · 못 했다 — 전부 **그날의 사실**이다 |
    | 400 | `sim_run_id` 가 비었거나 번인 실행이다 (`_walk_axis`) |
    | 422 | `sim_run_id` 쿼리를 안 줬다 |

    ★ **실패도 200 이다** (`/days/{as_of}/receive` 와 같은 태도). `FAILED` 는 시도조차
      못 한 것이고, `SHORT` 는 사업 결과라 품목별로 `items` 에 실린다.
    """
    return run_ship_due_sales(as_of, sim_run_id=_walk_axis(sim_run_id))


@router.post(
    "/days/{as_of}/close",
    response_model=ClosingOut,
    summary="그날을 닫는다 — 하루의 맨 끝이다. 숫자는 재무가 낸다",
)
def master_close_day(as_of: date, sim_run_id: str) -> ClosingOut:
    """`as_of` 를 파트마다 닫는다. **출고 뒤이고 하루의 맨 끝이다.**

    🔴 **왜 자기 엔드포인트인가.**

      바로 위 네 형제가 적어 둔 원칙 그대로다 — *"명시적 호출이다. 실행의 부작용이
      아니다. 사건에는 자기 자리가 있다."* **마감도 사건이다.** 그날이 닫히면
      `daily_closings` 에 한 줄이 서고, 그것이 손익 곡선의 한 점이 된다.

    🔴 **마스터가 숫자를 계산하지 않는다.**

      ```text
      마스터   "그날을 닫아 달라" 고 부르고, 답을 어휘로 받아 적는다
      재무     그날 숫자와 daily_closings 적재
      ```

      ⚠️ **마감은 재무 원장 계산이다.** 마스터가 계산하면 조정자가 부서를 겸한다 —
        등록소를 일곱 개나 나눈 이유가 그것이다.

    🔴 **오늘은 이 경로가 매일 `NOTHING_DUE` + `missing=["finance"]` 를 낸다.**
      재무 마감 어댑터가 아직 없어서다. **그것이 맞는 상태다** — *"오늘 마감이 안
      돈다"* 를 정직하게 보이게 한다. 붙는 날 `bootstrap` 한 줄이면 된다.

    🔴 **`run_scheduled_day` 가 부르는 함수와 사람이 부르는 함수가 같다.** 둘이
      갈리면 손으로 부른 결과와 걷기 결과가 다른 코드를 지난다.

    🔴 **`sim_run_id` 는 요청이 준다** (2026-09-14). `daily_closings` 의 PK 가
      `(sim_run_id, close_date)` 라 **어느 실행의 장부인가**가 없으면 행이 어디에
      앉을지 정해지지 않는다. 전에는 번인 상수를 박아 손으로 부른 마감이 번인 장부에
      앉았다. 번인은 거부한다 (`_walk_axis`).

    ★ **순서는 문장이 아니라 Gate 가 지킨다.** 안 열린 날 부르면 `NOT_OPENED` 로
      돌아서고 `next_action` 이 `OPEN_DAY_REQUIRED` 를 준다 —
      `/days/{as_of}/issue-receivables` 와 같은 모양이다.

    ⚠️ **장부 관문은 이 경로에 없다.** 손으로 부르는 이 자리는 관문 사유를 안 받고,
      막힌 날 `BLOCKED` 를 내는 것은 걷기(`run_scheduled_day`)가 하는 일이다 —
      관문의 답을 아는 쪽이 거기이기 때문이다.

    | 상태 | 언제 |
    |---|---|
    | 200 | 닫았다 · 닫을 게 없었다 · 막혔다 · 안 열렸다 — 전부 **그날의 사실**이다 |
    | 400 | `sim_run_id` 가 비었거나 번인 실행이다 (`_walk_axis`) |
    | 422 | `sim_run_id` 쿼리를 안 줬다 |

    ★ **실패도 200 이다** (`/days/{as_of}/issue-receivables` 와 같은 태도). `FAILED` 는
      롤백되어 아무것도 안 바뀐 상태이고, 사유가 본문에 실린다.
    """
    return run_close_day(as_of, sim_run_id=_walk_axis(sim_run_id))
