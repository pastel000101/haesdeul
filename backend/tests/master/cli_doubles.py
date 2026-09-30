"""CLI 진입점 검사가 쓰는 대역 — 풀 수명을 실제로 열지 않는다 (2026-10-01 재구성 BL-022 보완).

★ 진입점(`main`)은 `core_db.pool_lifespan()` 안에서 돈다. 접속 정보가 없는 자리에서는 그 수명이
  풀을 안 열고 넘어가지만, 접속 정보가 있는 자리(`.env`)에서는 루트 가드가 풀 열기를 막아 진입점
  검사가 **환경에 따라** 빨개졌다(2026-10-01 — 가짜 접속 정보를 넣은 전체 실행에서 7건). 인자
  전달을 재는 검사는 수명을 기록하는 대역으로 바꿔 둔다.

★ 진입점이 수명을 부르는지는 `tests/architecture/test_master_layers.py` 가, 수명 자체(열기 · 닫기 ·
  설정 없음)는 `tests/core/test_core_db.py` 가 잰다.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from types import ModuleType

import pytest


def record_pool_lifespan(monkeypatch: pytest.MonkeyPatch, cli_module: ModuleType) -> list[str]:
    """`cli_module` 이 부르는 `core_db.pool_lifespan` 을 기록 대역으로 바꾼다.

    :returns: 수명에 드나든 순서(`"enter"` · `"exit"`).
    """
    events: list[str] = []

    @contextmanager
    def lifespan() -> Iterator[None]:
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    monkeypatch.setattr(cli_module.core_db, "pool_lifespan", lifespan)
    return events
