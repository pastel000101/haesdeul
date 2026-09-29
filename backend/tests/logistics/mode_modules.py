"""물류 mode 조립(service) 모듈 — 검사가 mode 가 부르는 이름을 바꿔 끼울 때 쓴다.

★ 2026-09-30 재구성 BL-015: 종전에는 `adapter.py` 한 파일에 네 mode 조립(STATUS_QUERY ·
  PRE_PURCHASE · PRE_SALES · SCENARIO_VALIDATION)이 있어 검사가 `adapter` 전역 이름 하나를 바꿔
  끼웠다. 이제 조립이 `service/` 네 파일로 갈려, 그 이름을 들여와 부르는 파일마다 바꾼다.
"""

from typing import Any

import pytest

from app.logistics.service import agent_status, pre_purchase, pre_sales, scenario_validation

MODE_MODULES = (agent_status, pre_purchase, pre_sales, scenario_validation)


def swap_in_modes(monkeypatch: pytest.MonkeyPatch, name: str, value: Any) -> None:
    """`name` 을 부르는 mode 조립 파일마다 `value` 로 바꾼다. 부르는 파일이 없으면 멈춘다.

    ★ 멈추는 이유: 바꿔 끼운 것이 아무 데도 안 닿으면 검사가 진짜 경로를 돌면서도 통과해
      «가짜로 막았다» 고 잘못 읽힌다.
    """
    users = [module for module in MODE_MODULES if name in vars(module)]
    assert users, f"{name} 을 부르는 mode 조립이 없다 — 바꿔 끼운 것이 아무 데도 안 닿는다"
    for module in users:
        monkeypatch.setattr(module, name, value)
