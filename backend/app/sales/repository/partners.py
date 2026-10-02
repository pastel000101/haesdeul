"""거래처 SQL — `partners` 한 건 읽기 · 만들기 · 고치기. 표에 있는 칸만.

연결을 인자로 받는다. 입력 검증과 트랜잭션은 `service/partners.py`, 한 건 조회는
`readmodel/partners.py` 다.

쓰기는 두 걸음이다 — SQL 을 짓는 함수(`insert_partner_statement` ·
`update_partner_statement`)와 받은 연결로 실행하는 함수(`write_partner`). service 는 연결을
빌리기 전에 SQL 을 짓는다(그때 `DB_SCHEMA` 를 읽는다). 순서가 바뀌면 설정이 빈 날 어느
오류가 먼저 나는지가 달라진다.
"""

from typing import Any, NamedTuple

from psycopg import Connection, sql

from app.core.settings import get_db_schema
from app.sales.repository._cursor import fetch_all, returning_one
from app.sales.schemas.partners import CREATABLE_FIELDS, EDITABLE_FIELDS


class PartnerWrite(NamedTuple):
    """거래처 쓰기 SQL 한 건과 그 인자."""

    query: sql.Composed
    params: list[Any]


def find_partner(conn: Connection, *, partner_id: str) -> dict[str, Any] | None:
    """거래처 행 한 건. 없으면 `None` 이다."""
    schema = get_db_schema()
    statement = sql.SQL(
        """
        SELECT partner_id, partner_name, partner_type, client_type, factory_region,
               factory_city, factory_area, sales_collection_days, pricing_contract_type,
               active, provisional, note
        FROM {}.partners WHERE partner_id = %s
        """
    ).format(sql.Identifier(schema))
    rows = fetch_all(conn, statement, [partner_id])
    return None if not rows else rows[0]


def update_partner_statement(*, partner_id: str, changes: dict[str, Any]) -> PartnerWrite:
    """준 칸만 고치는 SQL. 없는 거래처면 실행해도 행이 안 나온다.

    돌려받는 것은 입력이 아니라 `RETURNING` 이다 — 화면이 «고쳐졌다고 믿는 값» 대신
    저장된 값을 보게 된다. 둘이 다를 수 있고, 다를 때 알아야 한다.

    주의: 고칠 칸은 `schemas.partners.EDITABLE_FIELDS` 안의 것만 넣는다 — 걸러 넘기는 것은
    `service/partners.py` 다. 여기서도 그 목록 밖의 이름은 SQL 에 싣지 않는다.
    """
    schema = get_db_schema()
    assignments = [
        sql.SQL("{} = %s").format(sql.Identifier(name))
        for name in changes
        if name in EDITABLE_FIELDS
    ]
    params: list[Any] = [changes[name] for name in changes if name in EDITABLE_FIELDS]
    params.append(partner_id)
    statement = (
        sql.SQL("UPDATE {}.partners SET ").format(sql.Identifier(schema))
        + sql.SQL(", ").join(assignments)
        + sql.SQL(
            """
            WHERE partner_id = %s
            RETURNING partner_id, partner_name, partner_type, client_type,
                      factory_region, factory_city, factory_area,
                      sales_collection_days, pricing_contract_type,
                      active, provisional, note
            """
        )
    )
    return PartnerWrite(statement, params)


def insert_partner_statement(*, values: dict[str, Any]) -> PartnerWrite:
    """거래처 한 건을 만드는 SQL. 같은 코드가 있으면 실행해도 행이 안 나온다.

    `update_partner_statement` 와 같은 규율이다 — 돌려받는 것은 입력이 아니라
    `RETURNING` 이다. 기본값(`provisional`)은 DB 가 채우므로 그 값도 함께 온다.

    덮어쓰지 않는다 (`ON CONFLICT … DO NOTHING`). 같은 코드가 이미 있으면 만들지 못한
    것이고, 그 사실이 사용자에게 가야 한다 — `service/partners.py` 가
    `PartnerAlreadyExists` 로 올린다.
    """
    columns = [name for name in CREATABLE_FIELDS if name in values]
    statement = (
        sql.SQL("INSERT INTO {}.partners (").format(sql.Identifier(get_db_schema()))
        + sql.SQL(", ").join(sql.Identifier(name) for name in columns)
        + sql.SQL(") VALUES (")
        + sql.SQL(", ").join(sql.Placeholder() for _ in columns)
        + sql.SQL(
            """
            )
            ON CONFLICT (partner_id) DO NOTHING
            RETURNING partner_id, partner_name, partner_type, client_type,
                      factory_region, factory_city, factory_area,
                      sales_collection_days, pricing_contract_type,
                      active, provisional, note
            """
        )
    )
    return PartnerWrite(statement, [values[name] for name in columns])


def write_partner(conn: Connection, write: PartnerWrite) -> dict[str, Any]:
    """받은 연결로 쓰기를 실행하고 저장된 행을 돌려준다.

    행이 안 나오면 `RuntimeError` 다 — 만들 때는 `DO NOTHING` 충돌(«이미 있다»), 고칠 때는
    없는 거래처(«없다»)라는 뜻이다.
    """
    return returning_one(conn, write.query, write.params)
