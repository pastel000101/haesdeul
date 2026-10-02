"""판매 한 건의 흐름 응답 — `readmodel/console_lifecycle.py` 가 채운다."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

#: 한 단계가 가질 수 있는 상태.
#:
#: ``BLOCKED`` 는 "아직 안 만들었다" 가 아니라 연결을 저장한 곳이 없다는 뜻이다.
#: ``NOT_DUE`` 는 아직 그 단계에 올 때가 아니라는 사실이고, ``MISSING`` 은 올 때가
#: 지났는데 행이 없다는 사실이다 — 둘을 한 칸에 뭉치지 않는다.
LifecycleStatus = Literal["DONE", "OPEN", "NOT_DUE", "MISSING", "BLOCKED"]


class LifecycleStage(BaseModel):
    stage: str
    status: LifecycleStatus
    #: 이 단계를 가리키는 저장된 식별자. 없으면 `null` 이다.
    reference: str | None = None
    #: 그 단계가 기록된 시각/날짜. 없으면 `null` — 오늘로 메우지 않는다.
    occurred_at: date | datetime | None = None
    #: 사람이 읽을 사유. 왜 이 상태인지를 말한다.
    detail: str
    #: 되짚을 근거. 저장된 참조만 싣는다.
    evidence: list[str] = []


class ConsoleSaleLifecycle(BaseModel):
    sale_id: str
    sim_run_id: str
    as_of: date
    #: 확정 이후 구간이 저장된 연결키로 이어졌는가.
    confirmed_lineage: Literal["LIVE", "PARTIAL"]
    #: 후보 → 판매 구간. 업무 키(`source_order_id`)가 마스터 요청을 가리키면 `LIVE`,
    #: 그 키가 없는 옛 행이면 `BLOCKED` 다 — 추정으로 메우지 않는다.
    agent_lineage: Literal["LIVE", "BLOCKED"]
    stages: list[LifecycleStage]
