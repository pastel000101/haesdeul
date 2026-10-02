"""예측 질의응답이 읽는 사실 — 전부 SELECT 다.

숫자는 여기서만 나온다. 답변 문장이 어떻게 바뀌든, 값은 이 함수들이 읽은 것이어야
한다. 지어낸 숫자가 섞이면 그 답은 쓸 수 없다.

두 창고를 읽는다 (`app/core/db.py` 의 풀 두 개).

```text
서비스 창고  haetdeul.ml_price_forecasts   D+1~D+18 (달력일) · 매입 파트가 읽는 표   SERVICE_POOL
원본 창고    prediction_log                 채점 기록 · 리드 0(당일)                  ML_SOURCE_POOL
```

당일 값이 전달표에 없다. `offset_days` 가 1~18 이라 «오늘» 칸이 아예 없다.
그래서 오늘을 물으면 원본 창고의 리드 0 을 읽고, 답에 출처를 밝힌다.
두 표는 축이 달라서(달력일 vs 영업일) 출처를 안 밝히면 나중에
«왜 두 값이 다르냐» 가 나온다.

조회 한 번에 연결 하나. 함수마다 그 표가 있는 창고의 풀에서 조회 연결을 빌려
`repository/qa.py` 에 넘기고 돌려준다. SQL 을 둘 이상 보내는 `today_row` ·
`current_models` 도 연결 하나로 보낸다.

그래프(`service/qa_graph.py`)와 마스터 어댑터(`adapter.py`, 질문 없는 상태 조회)가 부른다.
«바꿀 모델이 있나» 는 DB 가 아니라 ML 백엔드에 물어서 `ml_backend.retrain_pending` 에 있다.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from psycopg import errors as pg_errors

from app.core import db as core_db
from app.ml import config
from app.ml.repository import qa as qa_sql


def latest_base_date(as_of: date | None = None) -> date | None:
    """전달표의 최신 기준일. `as_of` 를 주면 그 날 이하에서 고른다 (미래를 안 본다)."""
    with core_db.read_connection() as conn:
        return qa_sql.latest_base_date(conn, as_of)


def forecast_rows(item: str, kind: str, base_dt: date, targets: list[date]) -> list[dict[str, Any]]:
    """전달표에서 여러 대상일을 한 번에 읽는다. 날짜마다 묻지 않는다."""
    if not targets:
        return []
    with core_db.read_connection() as conn:
        return qa_sql.forecast_rows(conn, item, kind, base_dt, targets)


def today_row(item: str, kind: str, base_dt: date | None = None) -> dict[str, Any] | None:
    """당일(리드 0) 한 행. 원본 창고에서 읽는다 — 전달표에는 없는 값이다.

    기준일을 받아 그날 것만 읽는다 — 미래 값이 섞이지 않게 하려는 것이다. 원본 창고의
    전체 최신 기준일을 읽으면 화면이 과거 날짜를 시뮬레이션 중일 때도 그보다 뒤의 값이
    에러 없이 나간다.

    `base_dt` 는 그래프가 `as_of` 이하에서 고른 기준일이다. 안 주면 원본 창고의 최신
    기준일을 읽는다. 앱 안에서 부르는 곳(`service/qa_graph.py`)은 늘 `base_dt` 를 넘긴다.
    """
    model = config.OPS_MODEL[kind]
    with core_db.ML_SOURCE_POOL.read_connection() as conn:
        if base_dt is None:
            latest = qa_sql.latest_model_base_date(conn, model)
            if not latest or not latest["base_dt"]:
                return None
            base_dt = latest["base_dt"]
        return qa_sql.today_row(conn, model, item, base_dt)


def accuracy(item: str, kind: str) -> dict[str, Any] | None:
    """그 조합이 얼마나 맞히나. 화면에 적힌 값과 같은 표를 쓴다.

    `prediction_log` 로 다시 재지 않는다. 거기엔 실험용 모델이 섞여 있고, 리드·기간을
    어떻게 자르느냐에 따라 값이 달라진다(예: 같은 조합을 전 구간으로 집계하면 15.1%,
    화면은 19.7%). 한 사실에 두 숫자가 돌아다니면 어느 쪽이 맞는지 아무도 모른다.

    출처: 봉인 개봉(2026-09-01) · 운영 모델 · 홀드아웃 2024~2025 · 486 기준일 ·
    리드타임 3 이상. 화면 `app/api/forecast/presenter.py` 의 `_ACCURACY` 와 같은 값이고,
    거기에 없는 중도매가·소매가는 같은 실행의 나머지 여섯 칸이다.
    """
    return config.SEALED_ACCURACY.get((kind, item))


def usability(item: str, kind: str) -> dict[str, Any] | None:
    """판단에 써도 되는 조합인가. `use_recommended=False` 면 쓰지 말라는 뜻이다."""
    with core_db.read_connection() as conn:
        return qa_sql.usability(conn, item, kind)


def batch_run(on: date) -> dict[str, Any] | None:
    """그날 배치 한 행. 여러 번 돌았으면 마지막 것을 준다.

    하루에 두 행이 있는 날이 있다(2026-09-14 실측 · run 72 · 73). 토요일 06:00 경제지표
    작업과 09:00 일별 배치가 같은 한국 날짜에 든다. 물어보는 사람이 아는 «오늘 배치» 는
    나중 것이므로 시작 시각 내림차순 첫 행이다.
    """
    with core_db.ML_SOURCE_POOL.read_connection() as conn:
        return qa_sql.batch_run(conn, on)


def failed_stages(run_id: int) -> list[dict[str, Any]]:
    """그 실행에서 실패한 단계만. 성공한 단계는 세지 않는다 — 개수는 배치 행에 있다."""
    with core_db.ML_SOURCE_POOL.read_connection() as conn:
        return qa_sql.failed_stages(conn, run_id)


def agent_report(name: str, on: date) -> dict[str, Any] | None:
    """그날 그 이름의 보고서 한 건 (가장 나중 것).

    하루에 한 건만 나오는 보고서용이다(`claude_check`). 여러 건 나오는
    재학습 보고서는 `agent_reports()` 로 전부 읽는다.
    """
    with core_db.ML_SOURCE_POOL.read_connection() as conn:
        return qa_sql.agent_report(conn, name, on)


def agent_reports(name: str, on: date) -> list[dict[str, Any]]:
    """그날 그 이름의 보고서 전부 (돈 순서).

    하루에 여러 건이 남는다. `재학습판정`·`재학습검증` 은 가격 종류마다 한 건씩
    나온다(2026-09-16 실측 · 판정 3건 · 검증 2건). 마지막 하나만 읽으면 다른 가격 종류의
    «후보가 나쁩니다» 가 답에서 통째로 빠진다.

    payload 에 가격 종류 칸이 없다(2026-09-16 실측). 어느 종류인지는 제목 맨 앞 낱말에만
    있고, 검증 보고서는 그것도 없다 — 답에서 «(가격 종류 미상)» 으로 적는다.
    지어내지 않는다.
    """
    with core_db.ML_SOURCE_POOL.read_connection() as conn:
        return qa_sql.agent_reports(conn, name, on)


def current_models() -> dict[str, Any]:
    """지금 도는 모델 셋. 돌려주는 것은 `{"models": [...], "cutover_read": ...}`.

    ```text
    이름 · 만든 날   prediction_log 의 최신 기준일 행       (늘 있다)
    학습 끝 · 교체   model_cutover 의 kind 별 최신 행       (아직 없을 수 있다)
    ```

    `cutover_read` 는 셋 중 하나다.

    ```text
    ok      읽었다 (행이 없을 수는 있다)
    absent  표가 아직 없다        -> 답에 «교체 이력 없음»
    error   읽다가 실패했다        -> 답에 그렇게 적는다
    ```

    표가 없다고 실패로 끝내지 않는다. 이름과 만든 날만으로도 답할 것이 있다.
    """
    with core_db.ML_SOURCE_POOL.read_connection() as conn:
        cutovers: dict[str, dict[str, Any]] = {}
        cutover_read = "ok"
        try:
            for row in qa_sql.cutover_rows(conn):
                cutovers[str(row.get("kind") or "").upper()] = row
        except pg_errors.UndefinedTable:
            cutover_read = "absent"
        except Exception:                                        # noqa: BLE001
            cutover_read = "error"

        models: list[dict[str, Any]] = []
        for kind, model_ver in config.OPS_MODEL.items():
            now = qa_sql.model_now(conn, model_ver) or {}
            cut = cutovers.get(kind) or {}
            models.append({
                "kind": kind,
                "model_ver": model_ver,
                "created_at": now.get("model_created_at"),
                "base_dt": now.get("base_dt"),
                "train_end": cut.get("new_train_end"),
                "last_swapped_at": cut.get("swapped_at"),
                "last_swap_note": cut.get("note"),
                #   시각을 아는가(`time_known`). 백필한 행은 백업 폴더 이름에서
                #   날짜만 되짚은 것이 있어 시각이 `00:00` 으로 앉아 있다.
                #
                #   칸이 아직 없을 수 있다(2026-09-16 실측: 없다). `SELECT *` 로
                #   읽으니 그때는 열쇠가 아예 안 오고 `None` 이 된다 — `False`(모른다)
                #   와 다른 값이다. 답에서도 다르게 적는다.
                "last_swap_time_known": cut.get("time_known"),
            })
    return {"models": models, "cutover_read": cutover_read}
