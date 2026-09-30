"""질의응답 그래프가 들고 다니는 상태 (`app/ml/service/qa_graph.py`).

🟢 **자리 (2026-09-29 · 재구성 BL-017).** 전에는 `qa_graph.py` 안에 있었다. 그래프(service),
  판단(domain), 답 조립(readmodel)이 같은 상태를 읽어 모델 자리로 뗐다. 칸 · 주석은 그대로다.
"""

from __future__ import annotations

from datetime import date
from typing import Any, TypedDict

from app.ml.schemas.qa import QaMeta, QaRequest


class QaState(TypedDict, total=False):
    """그래프가 들고 다니는 것. 판정 결과만 담고 원본은 표에 둔다."""

    request: QaRequest
    routes: list[str]          # ★ 무엇을 물었나 — forecast · batch · perf (2026-09-16)
    item: str                  # 조합이 하나면 그 값 · 여럿이면 첫 번째 (옛 이름)
    kind: str
    items: list[str]           # ★ 답할 품목 전부 (2026-09-15)
    kinds: list[str]           # ★ 답할 가격 종류 전부
    asks: list[dict[str, Any]]     # ★ 짝지어진 물음 [{item, kind, dates}]
    blocks: list[dict[str, Any]]   # 조합마다 읽어 온 것 — 표 하나가 블록 하나다
    base_dt: date
    wants_today: bool
    asked: list[date]          # LLM 이 고른 날짜 (요청이 직접 주면 그쪽이 이긴다)
    targets: list[date]        # 전달표에서 읽을 날 (D+1 ~ D+18)
    out_of_range: list[date]
    used_default: bool         # 날짜를 안 말해 **오늘**을 기본값으로 썼다
    default_fell_back: bool    # 그 오늘 값마저 없어 내일로 물러섰다
    rows: list[dict[str, Any]]
    today: dict[str, Any] | None
    accuracy: dict[str, Any] | None
    usability: dict[str, Any] | None
    status: str
    message: str               # 되묻기·거절일 때 쓸 본문
    note: str                  # 답 맨 앞에 붙일 한 줄 (무엇을 무시했는지)
    markdown: str
    meta: QaMeta

    #   ── 배치 갈래가 읽어 온 것 ──────────────────────────────────────
    #   🔴 «못 읽었다»(error) 와 «없다»(empty) 를 **한 칸에 담지 않는다.**
    #     둘을 뭉치면 창고가 죽은 날과 아직 안 돈 날이 같은 문장으로 나간다.
    batch_on: date
    batch_row: dict[str, Any] | None
    batch_fails: list[dict[str, Any]]
    batch_read: str            # ok · empty · error
    check_row: dict[str, Any] | None
    check_read: str
    #   ★ 날짜가 여럿일 수 있다 (2026-09-16). 위 다섯 칸은 **첫 날**을 가리키는 옛 이름.
    batch_days: list[date]
    batch_blocks: list[dict[str, Any]]
    batch_trimmed: bool
    #   ★ 기준일보다 **뒤인 날**을 물었나 (2026-09-16). 읽지 않고 한 줄로 밝힌다.
    batch_ahead: list[date]
    #   ── 성능 갈래 ────────────────────────────────────────────────
    retrain_rows: list[dict[str, Any]]
    perf_read: str
    perf_days: list[date]
    perf_trimmed: bool
    perf_ahead: list[date]
    #   ★ 지금 무엇이 도나 (2026-09-16). 이름은 교체해도 그대로라 만든 날·학습 끝이
    #     같이 있어야 가려진다.
    models: list[dict[str, Any]]
    models_read: str           # ok · error
    cutover_read: str          # ok · absent · error
    #   ★ 바꿀 수 있는 후보가 있나 (2026-09-16). 이것만 DB 가 아니라 ML 콘솔에
    #     서버가 직접 물어 온다 — 「아직 안 눌렀나」는 그래프만 안다
    #     (`ml_backend.retrain_pending`).
    pending: list[dict[str, Any]]
    pending_read: str          # ok · error
    #   ★ 질문에 없어서 **전부로 채운** 자리 (2026-09-16 · 사용자 지시).
    filled_defaults: list[str]
