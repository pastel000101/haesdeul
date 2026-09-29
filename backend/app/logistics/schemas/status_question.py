"""질문형 STATUS_QUERY 의 품목 해석 결과와 답 한 벌.

★ 2026-09-30 재구성 BL-015: `logistics/query/status_query.py` 에서 옮겼다. 순서는
  `service/status_question.py`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# item resolver — 🔴 **DB 가 정본, allowlist 가 gate** (언제나 결정론)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class ResolvedItems:
    allowed: dict[str, str]
    excluded: dict[str, str]
    not_found: tuple[str, ...]


# ---------------------------------------------------------------------------
# Orchestration — LLM tool-calling loop
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class StatusQueryAnswer:
    payload: Mapping[str, Any]
    missing_data: tuple[str, ...] = ()
    reasoning: str = ""
