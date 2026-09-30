"""걷기 출처 기록 SQL — `sim_runs.config_json` 에 걸은 시각을 적는다(받은 연결).

★ 2026-09-30 재구성 BL-018: `master/walk_provenance.py` 에서 옮겼다 — `WALKED_NOW_KEY`,
  `WalkedNowStamp`, `WalkedNowConflict`, `stamp_walked_now`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from psycopg import sql

from app.core.settings import get_db_schema
from app.master.domain.sim_run import PROVENANCE_CONFIG_KEY
from app.master.repository.sim_run_open import RUN_TABLE

#: `provenance` 칸 안에서 **걷기가 받은 `--now`** 가 앉는 키 이름.
#:
#: 🔴 **여기가 유일한 주인이다.** 읽는 쪽이 생기면 이 이름을 가져다 쓴다
#:   (`PROVENANCE_CONFIG_KEY` · `BACKFILL_CONFIG_KEY` 와 같은 이유).
WALKED_NOW_KEY = "walked_now"

#: 한 번 잰 결과. **두 값뿐이다** — 다르면 값이 아니라 예외다.
#:
#: ```text
#: WRITTEN        없어서 적었다
#: ALREADY_SAME   같은 문자열이 이미 있었다 · 아무것도 안 썼다
#: ```
WalkedNowStamp = Literal["WRITTEN", "ALREADY_SAME"]


class WalkedNowConflict(ValueError):
    """🔴 **그 실행에 이미 다른 기준 시각이 있다.** 걷기 전에 막는다.

    ★ `ValueError` 를 잇는다 — 걷기가 걷기 전에 막는 다른 사유(범위 · 시간대 ·
      `--auto-approve` 규칙)와 같은 결로 잡힌다.
    """


def stamp_walked_now(conn: Any, *, sim_run_id: str, walked_now: str) -> WalkedNowStamp:
    """그 실행에 **걷기가 받은 시각**을 잰다 · 없으면 적는다. 🔴 **커밋하지 않는다.**

    :param walked_now: 🔴 **받은 문자열 그대로** 싣는다 — 여기서 파싱도 정규화도
        안 한다. 같은지도 **문자열로** 잰다.
    :raises WalkedNowConflict: 이미 **다른** 값이 있을 때. 🔴 덮지 않는다.
    :raises LookupError: 그 실행 행이 없을 때. **지어내지 않는다.**
    :raises ValueError: `provenance` 칸이 객체가 아닐 때. 한 키를 붙일 자리가 없다.
    """
    # 🔴 **칸 이름의 주인에서 읽는다 — 여기서 `"provenance"` 를 다시 적지 않는다.**
    #   (2026-09-30 재구성 BL-018: 칸 이름이 `domain/sim_run.py` 로 내려가 CLI 모듈과의
    #   import 고리가 없어졌다 — 전에는 그 고리 때문에 함수 안에서 들여왔다.)
    표 = sql.SQL("{}.{}").format(sql.Identifier(get_db_schema()), sql.Identifier(RUN_TABLE))
    with conn.cursor() as cursor:
        cursor.execute(
            sql.SQL("SELECT config_json FROM {} WHERE sim_run_id = %s FOR UPDATE").format(표),
            [sim_run_id],
        )
        row = cursor.fetchone()
        if row is None:
            raise LookupError(
                f"실행 {sim_run_id!r} 이 없다 — 기준 시각을 남길 자리가 없다."
                " 실행을 먼저 열어라(`sim_run_runner`)"
            )

        config = row["config_json"]
        자취 = config.get(PROVENANCE_CONFIG_KEY) if isinstance(config, Mapping) else None
        if 자취 is not None and not isinstance(자취, Mapping):
            raise ValueError(
                f"실행 {sim_run_id!r} 의 {PROVENANCE_CONFIG_KEY} 칸이 객체가 아니다"
                f" ({type(자취).__name__}) — 한 키를 붙일 자리가 없다. 덮지 않는다"
            )

        이미 = None if 자취 is None else 자취.get(WALKED_NOW_KEY)
        if 이미 is not None:
            if 이미 == walked_now:
                # ★ **이어 걷기는 정상이다.** 같은 값을 다시 쓰지도 않는다.
                return "ALREADY_SAME"
            # 🔴 **덮지 않는다. 덮는 옵션도 없다.**
            raise WalkedNowConflict(
                f"실행 {sim_run_id!r} 은 이미 기준 시각 {이미!r} 로 걸렸다 — 받은 값은"
                f" {walked_now!r} 다. 한 실행을 두 기준 시각으로 이어 걸으면 그 판의"
                " 절반은 마감 전, 절반은 마감 뒤가 되어 아무것도 증명하지 않는다."
                " 다른 시각으로 걸려면 `sim_run_runner --reset` 으로 새로 열어라"
            )

        # 🔴 **한 키만 붙인다.** `commit` 은 여는 자리 것이라 안 건드린다 —
        #    칸을 통째로 덮으면 여는 자리가 적은 커밋이 사라진다.
        cursor.execute(
            sql.SQL(
                "UPDATE {} SET config_json = jsonb_set("
                "config_json, ARRAY[%s]::text[],"
                " COALESCE(config_json -> %s::text, '{{}}'::jsonb)"
                " || jsonb_build_object(%s::text, %s::text), true)"
                " WHERE sim_run_id = %s"
            ).format(표),
            [PROVENANCE_CONFIG_KEY, PROVENANCE_CONFIG_KEY, WALKED_NOW_KEY, walked_now, sim_run_id],
        )
    return "WRITTEN"
