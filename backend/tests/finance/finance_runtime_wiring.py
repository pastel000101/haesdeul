"""재무 에이전트 경로의 검사 대역 자리 — 컨텍스트 적재와 Controller 생성.

★ 2026-09-29 재구성 BL-014: 종전 `app/finance/adapter.py` 한 모듈에 있던 자리가 service 로
  나뉘었다.

```text
컨텍스트 적재     load_runtime_context      정의  service/agent_run.py
                                            부름  agent_run(Controller 경계) · status_query ·
                                                  pre_sales_facts
Controller 생성   FinanceAgentController    부름  service/agent_run.py (run_controller)
```

종전 검사는 `adapter._load_context` 한 자리를 바꿔 끼우면 세 경로가 모두 대역을 탔다.
`wire_context` 가 같은 범위 — 그 이름을 부르는 세 모듈 — 를 한 번에 바꿔 끼운다. 부르는
모듈이 늘면 `tests/finance/test_finance_adapter_sim_run_axis.py` 의 원문 검사가 이 목록과
어긋나 빨간불을 켠다.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

#: `load_runtime_context` 를 부르는 모듈 (정의한 `agent_run` 포함).
CONTEXT_CALLERS = (
    "app.finance.service.agent_run",
    "app.finance.service.status_query",
    "app.finance.service.pre_sales_facts",
)


def wire_context(monkeypatch: pytest.MonkeyPatch, load: Callable[..., Any]) -> None:
    """세 경로가 모두 `load` 로 컨텍스트를 받게 한다 (종전 `adapter._load_context` 대역)."""
    for module in CONTEXT_CALLERS:
        monkeypatch.setattr(f"{module}.load_runtime_context", load)


def wire_controller(monkeypatch: pytest.MonkeyPatch, factory: Callable[..., Any]) -> None:
    """Controller 를 `factory(port)` 로 만들게 한다 (종전 `adapter.FinanceAgentController` 대역)."""
    monkeypatch.setattr("app.finance.service.agent_run.FinanceAgentController", factory)
