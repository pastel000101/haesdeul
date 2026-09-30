"""하루 넘김 SQL — 그날 활성 실행 · 전날 행에서 물려받는 `INSERT … SELECT`.

★ 2026-09-30 재구성 BL-015: `logistics/day_open.py` 의 `LogisticsDayOpening` 안 SQL 을 함수로
  옮겼다(문면 그대로).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from psycopg import sql

from app.logistics.repository.rows import get_db_schema
from app.logistics.schemas.vocabulary import USAGE_SCOPE

# ── 실행 축 조건 ────────────────────────────────────────────────────────
#
# 🔴 **두 질의가 같은 조건을 쓴다. 한 곳에 적는다.** `is_open` 과 carry-forward INSERT
#    가 각자 조건을 적으면 언젠가 한쪽만 바뀌고, 그 순간 *"열렸다고 본 행"* 과
#    *"물려받은 행"* 이 갈린다 — 이 파일이 고치려는 그 버그 그대로다.
#
# ★ 별칭 때문에 둘로 나뉘어 있다 (`is_open` 은 단일 표, carry-forward 는 `base`).
#   조각을 다시 조립하지 않고 통짜로 적는 이유는 **읽을 때 SQL 한 줄로 보여야** 하기
#   때문이다 (`sql.SQL` 을 f-string 으로 짓지 않는 규율도 함께 지킨다).
_NO_RUN_PREDICATE = sql.SQL("")
_RUN_PREDICATE = sql.SQL("AND sim_run_id = %(sim_run_id)s")
_BASE_RUN_PREDICATE = sql.SQL("AND base.sim_run_id = %(sim_run_id)s")


def _fixture_params(sim_run_id: str | None, **값: object) -> dict[str, object]:
    """공통 파라미터에 `sim_run_id` 를 **받았을 때만** 얹는다.

    ★ **조건과 파라미터가 같은 값 하나(`sim_run_id is not None`)로 갈린다.**
      그래서 *"조건은 걸렸는데 값이 없다"* 도 *"값은 실렸는데 조건이 없다"* 도
      성립할 수 없다.

    ⚠️ 안 받았는데 `None` 을 실으면 조건이 없는 질의에 남는 열쇠가 되고, 나중에
       조건을 더할 때 `sim_run_id = NULL` 이 조용히 0건을 만든다.
    """
    params: dict[str, object] = {"usage_scope": USAGE_SCOPE, **값}
    if sim_run_id is not None:
        params["sim_run_id"] = sim_run_id
    return params


def _carry_forward_query(schema: sql.Identifier, *, pinned: bool) -> sql.Composed:
    """전날 행에서 물려받는 INSERT.

    ```text
    물려받는다     confirmed_outbound · zone_capacity
                   usage_scope · evidence_grade · approved_by · sim_run_id
    새로 둔다      as_of · fixture_id · source_ref · note · is_active
    안 물려받는다  lot_priority          (CONFIRMED_ZERO · [])
                   in_transit            status 만 CONFIRMED_ZERO   ← W3-3/4
                   confirmed_inbound     status 만 CONFIRMED_ZERO   ← W3-3/4
    ```

    ★ **입고 두 JSON 칸을 아예 안 쓴다 (W3-4).** 그 칸은 이제 production 어디에서도
      업무 사실로 안 읽히고 DROP 대상이라
      (`database/migrations/logistics/logistics_drop_inbound_json.sql`),
      여기서 값을 넣으면 그 migration 뒤에 이 INSERT 가 깨진다.

    🔴 **`confirmed_outbound_json` 도 안 쓴다 (WP-3).** 미래 확정 출고의 정본이
       `sales` · `sale_items` 로 옮겨 갔다 — 그 칸을 물려받으면 **아무도 안 읽는
       값을 날마다 복제**하는 것이 되고, 입고 축에서 사고를 냈던 바로 그 모양이다
       (`outbound_schedules.confirmed_outbound_at`).

       ⚠️ **`confirmed_outbound_status` 도 물려받지 않고 `CONFIRMED_ZERO` 로 새로
          둔다.** 입고 두 축과 같은 이유다 — 물려받은 `UNRESOLVED` 를 이으면 그날
          이후가 전부 `UNRESOLVED` 로 굳어 Reader 가 목록을 통째로 숨긴다.

    🔴 **입고 예정을 다음 날로 복제하지 않는다 (W3-3).** 그 복제가 사고의 원인이었다
       — 미래 날짜 행이 **먼저 열려 있으면** 그 행은 나중에 난 승인을 모른 채 굳는다.
       지금은 `inbound_schedules` 한 행이 날짜에 안 묶여 있고 Reader 가 날짜로
       질의하므로 **복제할 것이 없다.**

       ⚠️ **`CONFIRMED_ZERO` 로 새로 둔다 — 물려받은 `UNRESOLVED` 를 잇지 않는다.**
          이으면 그날 이후가 전부 `UNRESOLVED` 로 굳어 **Reader 가 일정을 통째로
          숨긴다** (`domain/snapshot.schedule_source`).

    🔴 **`lot_priority` 는 판단이라 물려받지 않는다.** 씨앗 SQL 이 그렇게 적었고
       (`database/seed/logistics/logistics_runtime_fixture_20260105_20260106.sql` 124행)
       그대로 옮긴다. 어제 어느 로트를 먼저 내보내기로
       했는지는 어제의 판단이지 오늘의 사실이 아니다.

    🔴 **`sim_run_id` 칸은 여전히 `base.sim_run_id` 다.** 마스터 상수를 가져다 쓰지
       않는다 — 쓰는 값은 물려받는 것이지 정하는 것이 아니다 (씨앗 SQL 머리말 §3 과
       같은 이유: 손으로 적으면 실행이 여럿이 되는 날 이 코드만 옛 값을 들고 남는다).

    ⚠️ **`WHERE` 에 얹는 `sim_run_id` 는 다른 이야기다.** 그것은 *"어느 전날 행을
       고를 것인가"* 이고, 안 좁히면 같은 날에 선 다른 실행의 행까지 함께 물려받아
       **한 번의 하루 넘김이 남의 실행 행도 만든다.**

    ★ `fixture_id` 도 씨앗 SQL 과 같은 식으로 만든다 —
      `LOG-RUNTIME-{sim_run_id}-{YYYYMMDD}`. 같은 날을 두 번 열어도 같은 id 가
      나와야 `ON CONFLICT` 가 걸린다.
    """
    return sql.SQL(
        """
        INSERT INTO {}.logistics_runtime_fixture (
            fixture_id, sim_run_id, as_of,
            in_transit_status,
            confirmed_inbound_status,
            confirmed_outbound_status,
            lot_priority_status,       lot_priority_json,
            zone_capacity_status,      guaranteed_capacity_by_zone_json,
            usage_scope, evidence_grade, approved_by, source_ref, is_active, note
        )
        SELECT
            'LOG-RUNTIME-' || base.sim_run_id || '-' || to_char(%(as_of)s::date, 'YYYYMMDD'),
            base.sim_run_id,
            %(as_of)s::date,
            'CONFIRMED_ZERO',
            'CONFIRMED_ZERO',
            'CONFIRMED_ZERO',
            'CONFIRMED_ZERO',               '[]'::JSONB,
            base.zone_capacity_status,      base.guaranteed_capacity_by_zone_json,
            base.usage_scope,
            base.evidence_grade,            base.approved_by,
            %(source_ref)s,
            TRUE,
            %(note)s
        FROM {}.logistics_runtime_fixture base
        WHERE base.as_of = %(carry_from)s::date
          AND base.usage_scope = %(usage_scope)s
          AND base.is_active
          {}
        ON CONFLICT DO NOTHING
        """
    ).format(schema, schema, _BASE_RUN_PREDICATE if pinned else _NO_RUN_PREDICATE)


def select_open_runs(conn: Any, *, as_of: date, sim_run_id: str | None) -> list[Any]:
    """그날 활성 fixture 의 **실행들** (`SELECT DISTINCT sim_run_id … LIMIT 2`).

    ★ `sim_run_id` 를 받으면 그 실행으로 좁힌다(조건과 파라미터가 같은 값 하나로 갈린다).
    """
    schema = sql.Identifier(get_db_schema())
    pinned = sim_run_id is not None
    query = sql.SQL(
        """
        SELECT DISTINCT sim_run_id
        FROM {}.logistics_runtime_fixture
        WHERE as_of = %(as_of)s
          AND usage_scope = %(usage_scope)s
          AND is_active
          {}
        LIMIT 2
        """
    ).format(schema, _RUN_PREDICATE if pinned else _NO_RUN_PREDICATE)

    with conn.cursor() as cursor:
        cursor.execute(query, _fixture_params(sim_run_id, as_of=as_of))
        실행들 = cursor.fetchall()
    return 실행들


def carry_forward_fixture(
    conn: Any, *, as_of: date, carry_from: date, sim_run_id: str | None
) -> None:
    """`carry_from` 날 행에서 물려받아 `as_of` 날 fixture 행을 `INSERT … SELECT` 로 세운다.

    ★ `ON CONFLICT DO NOTHING` — 두 번 열려도 두 번째는 아무 일도 안 한다. 물려받을 행이
      있는지 보는 가드는 부르는 쪽(`open_logistics_day`)이다.
    """
    schema = sql.Identifier(get_db_schema())
    with conn.cursor() as cursor:
        cursor.execute(
            _carry_forward_query(schema, pinned=sim_run_id is not None),
            _fixture_params(sim_run_id,
                as_of=as_of,
                carry_from=carry_from,
                # ⚠️ **승인이 만든 것처럼 보이면 안 된다.** 01-02 씨앗이
                #    `MASTER-APPROVAL:RT-1` 이라 없는 승인을 가리키는 문제가 있었다
                #    (2026-09-04 실측). 이 행을 만든 것은 승인이 아니라 하루 넘김이다.
                source_ref=f"MASTER-DAY-OPEN:{as_of}",
                note=(
                    f"하루 넘김이 {carry_from} 행에서 물려받아 세운 행이다."
                    " 승인이 만든 행이 아니다 - 그날 승인이 나면"
                    " persist_inventory 가 status 두 칸을 CONFIRMED 로 세운다."
                    " 입고 예정 목록은 inbound_schedules 가 들고 있다."
                ),
            ),
        )
