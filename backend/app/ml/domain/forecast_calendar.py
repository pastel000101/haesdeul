"""개장일 예측을 달력일로 편다 — 적재 전 변환 (입력 → 출력만, DB 없음).

원본 창고에서 읽은 예측을 서비스 창고에 적재하기 전에 거친다. 두 창고의 **날짜 세는 법이
다르다** — 그 변환이 이 파일의 핵심이다.

    원본 창고   개장일만 센다. 토·일·공휴일은 건너뛴다
    서비스 창고  달력 날짜를 그대로 센다 (D+1 ~ D+18)

원본의 18개장일은 달력으로 24~26일에 걸쳐 있다. 그래서 앞에서부터 18달력일을
잘라 쓰고, 장이 안 서는 날은 **직전 개장일 값을 그대로** 넣는다.
그 칸에는 ``is_filled`` 를 켜 둔다.

🟢 **자리 (2026-09-29 · 재구성 BL-017).** 전에는 `app/ml/repository.py` 에 SQL 과 함께 있었다.
  SQL 은 `repository/forecasts.py`, 읽기 · 적재 순서는 `service/forecasts.py` 로 가고 변환만
  여기 남았다. 변환 규칙은 그대로다.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from app.ml.config import KIND_OF, KST
from app.ml.schemas.forecast import HORIZON_DAYS, SPEC


def to_calendar_rows(rows: list[dict[str, Any]], base_dt: date) -> list[tuple]:
    """개장일 예측을 달력일 D+1~D+18 로 다시 늘어놓는다.

    장이 안 서는 날은 직전 개장일 값을 그대로 쓰고 ``is_filled`` 를 켠다.
    첫 개장일 이전 칸은 만들지 않는다 — 없는 값을 지어내지 않는다.
    """
    generated = _generated_at(rows, base_dt)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((row["target_kind"], row["item_nm"]), []).append(row)

    out: list[tuple] = []
    for (kind, item), group in grouped.items():
        group.sort(key=lambda r: r["target_dt"])
        spec = SPEC[KIND_OF[kind]]
        by_date = {r["target_dt"]: r for r in group}
        current: dict[str, Any] | None = None
        for offset in range(1, HORIZON_DAYS + 1):
            day = base_dt + timedelta(days=offset)
            hit = by_date.get(day)
            if hit is not None:
                current, filled = hit, False
            elif current is not None:
                filled = True
            else:
                continue
            out.append((
                base_dt, item, KIND_OF[kind], offset, day,
                round(float(current["pred_prc"])),
                round(float(current["pred_lo"])),
                round(float(current["pred_hi"])),
                round(float(current["anchor_prc"])),
                current["unit"], current["model_ver"], generated,
                current["lead_biz_d"], filled, bool(current["gated"]),
                current["gate_reason"],
                spec["market"], spec["grade"], spec["desc"].get(item),
                spec["kg"].get(item),
                current["quality_note"], current["use_recommended"],
            ))
    return out


def _generated_at(rows: list[dict[str, Any]], base_dt: date) -> datetime:
    """예측을 만든 시각. 없으면 기준일 06:00 KST 로 둔다.

    **기준일보다 나중일 수 없다.** 나중이면 미래 정보로 과거를 맞힌 것이
    되어 성적이 무효가 된다. 계약 검사(``Forecast._no_lookahead``)도 막는다.
    """
    stamps = [r["model_created_at"] for r in rows if r.get("model_created_at")]
    fallback = datetime.combine(base_dt, time(6, 0), tzinfo=KST)
    if not stamps:
        return fallback
    latest = max(stamps)
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=KST)
    return latest if latest.date() <= base_dt else fallback
