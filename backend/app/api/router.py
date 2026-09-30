"""HTTP 입구 전부 — 여기서 모아 `app/main.py` 에 한 번에 넘긴다.

★ 2026-09-30 재구성 BL-019: 부서 · 마스터 라우터(`finance/router.py` · `sales/router.py` ·
  `master/router.py` · `master/critic/router.py` · `ml/router.py` · `ml/console_proxy.py`)를
  `api/<부서>/<자원>.py` 로 옮기고, 등록도 `app/main.py` 에서 이리로 옮겼다. **등록 순서는
  종전 그대로다** — OpenAPI 의 경로 순서가 이 순서다. 부서 폴더에는 FastAPI 코드가 없다.

화면용 API — `/api` 아래 전부(`screen_router`).

★ **부서 입구와 이름이 겹칩니다. 주소로 가릅니다.**

    /finance/agent        에이전트를 돌린다      (부서 입구 · `api/finance/agent.py`)
    /api/finance          화면에 값을 준다       (화면 탭 · `api/finance/routes.py`)

  `/api` 로 시작하면 화면용입니다. 규칙은 이 하나뿐입니다.

★ **`/api` 아래는 GET 만 둡니다.** 화면은 읽기만 합니다. 쓰기는 기존
  경로가 합니다 (`POST /master/…/decision` 처럼). 그래서 화면 탭 코드가
  실수로 데이터를 건드릴 길이 아예 없습니다.

★ **왜 따로 있나.** 기존 라우터 36개 중 GET 은 12개인데 전부
  `runs/{run_id}` 계열입니다. 화면은 `run_id` 를 모릅니다 — **날짜** 하나만
  압니다. "1월 6일 현금 잔액 얼마?" 를 물을 자리가 없어서 새로 만듭니다.

화면 탭은 폴더 하나 = 부서 하나입니다. 각자 자기 폴더의 `presenter.py` 만 고칩니다.
"""

from fastapi import APIRouter

from app.api.console.routes import router as console_router
from app.api.critic import runs as critic_runs
from app.api.critic import verdicts as critic_verdicts
from app.api.dashboard.routes import router as dashboard_router
from app.api.finance import agent as finance_agent
from app.api.finance import cash_adjustments as finance_cash_adjustments
from app.api.finance import collections as finance_collections
from app.api.finance import credit_limits as finance_credit_limits
from app.api.finance import expenses as finance_expenses
from app.api.finance import runs as finance_runs
from app.api.finance.console_routes import router as finance_console_router
from app.api.finance.routes import router as finance_router
from app.api.forecast.routes import router as forecast_router
from app.api.logistics.routes import router as logistics_router
from app.api.master import ask as master_ask
from app.api.master import days as master_days
from app.api.master import decision as master_decision
from app.api.master import flows as master_flows
from app.api.master import history as master_history
from app.api.ml import console as ml_console
from app.api.ml import qa as ml_qa
from app.api.purchase.routes import router as purchase_router
from app.api.sales import console_proposal as sales_console_proposal
from app.api.sales import partners as sales_partners
from app.api.sales import proposal as sales_proposal
from app.api.sales import runs as sales_runs
from app.api.sales.console_routes import router as sales_console_router
from app.api.sales.routes import router as sales_router

screen_router = APIRouter(prefix="/api")

for _child in (
    dashboard_router,
    forecast_router,
    purchase_router,
    finance_router,
    logistics_router,
    sales_router,
    finance_console_router,
    sales_console_router,
    console_router,
):
    screen_router.include_router(_child)

router = APIRouter()

for _child in (
    # 화면용 API (`/api/…`). **부서 라우터와 주소로 가른다** — `/finance/agent` 는
    # 에이전트를 돌리고, `/api/finance` 는 화면에 값을 준다. `/api` 아래는 GET 뿐이다.
    screen_router,
    # ML 예측 API. 2026-09-11 에 `/ml/forecast` · `/ml/forecast/push` 를 **주석 처리**했다 —
    # 부르는 곳이 없다. 마스터는 `ml_price_forecasts` 를 DB 에서 직접 읽고, 매입의
    # `get_forecast` 는 아직 mock 이다. 되살릴 때를 위해 라우터 연결은 남겨 둔다.
    ml_qa.router,
    # ML 운영 콘솔(`/ml/console/…`). **우리 ML 백엔드로 넘기는 프록시다** —
    # 재학습·에이전트는 학습 꾸러미가 있는 곳에서만 돌 수 있다.
    ml_console.router,
    finance_credit_limits.router,
    finance_expenses.router,
    finance_collections.router,
    finance_cash_adjustments.router,
    finance_agent.router,
    finance_runs.router,
    # 🔴 **물류에는 자기 HTTP 라우터가 없다** (2026-09-15). 종전 `/logistics/…` 16 경로는
    #    화면도 마스터도 안 불렀다 — 화면은 `/api/logistics`(`app/api/logistics/routes.py`)를
    #    치고, 마스터는 `app/logistics/adapter.logistics_port` 를 **파이썬으로** 부른다
    #    (`master/registry/bootstrap.py` 의 `register_agent("inventory", logistics_port)`).
    #
    #    ⚠️ 같은 콘솔 조회가 두 주소로 나가면 어느 쪽이 정본인지 갈린다. 물류 HTTP 경계는
    #       `app/api/logistics` 하나다.
    master_flows.router,
    master_ask.router,
    master_history.router,
    master_decision.router,
    master_days.router,
    critic_verdicts.router,
    critic_runs.router,
    sales_console_proposal.router,
    sales_proposal.router,
    sales_runs.router,
    sales_partners.router,
):
    router.include_router(_child)
