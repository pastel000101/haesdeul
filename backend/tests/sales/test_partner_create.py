"""거래처 등록 — 표가 가진 칸만, 덮어쓰지 않고, 여신은 건드리지 않는다.

★ 2026-09-29 BL-013: 쓰기는 `service/partners.create_partner`(SQL 은 `repository/partners`)이고,
  판매 라우터와 마스터 ask 가 같은 함수를 부른다. 가짜 DB 는 풀 대여 입구를 바꿔 끼운다
  (`tests/sales/sales_fake_connection.py`) — 한 쓰기가 연결 하나 · 트랜잭션 하나인지도 여기서 잰다.
"""

from contextlib import contextmanager

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.sales.partners import add_partner_profile
from app.core.settings import MissingDatabaseEnvironment
from app.sales.schemas.partners import (
    PARTNER_TYPES,
    PartnerAlreadyExists,
    PartnerInputRejected,
    PartnerProfileCreate,
)
from app.sales.service.partners import create_partner
from tests.sales.sales_fake_connection import lend

NEW = "TEST_FACTORY_900"


def _stored(**over) -> dict:
    return {
        "partner_id": NEW,
        "partner_name": "새 김치공장",
        "partner_type": "CUSTOMER",
        "client_type": "김치제조공장",
        "factory_region": "충청북도",
        "factory_city": "괴산군",
        "factory_area": None,
        "sales_collection_days": 30,
        "pricing_contract_type": None,
        "active": True,
        #  ★ DB 기본값이다. 입력에는 없고 `RETURNING` 으로만 온다.
        "provisional": False,
        "note": None,
        **over,
    }


class _Writer:
    """`INSERT … RETURNING` 흉내. 충돌이면(`DO NOTHING`) 행이 안 나온다."""

    def __init__(self, row: dict | None):
        self.row = row
        self.statements: list[tuple[str, list]] = []

    def __call__(self, query, params):
        self.statements.append((str(query), list(params)))
        return [] if self.row is None else [dict(self.row)]


def _patch(monkeypatch, writer: _Writer):
    monkeypatch.setattr("app.sales.repository.partners.get_db_schema", lambda: "haetdeul")
    return lend(monkeypatch, writer)


def _create(**fields) -> dict:
    return PartnerProfileCreate(**fields).model_dump()


def test_a_new_partner_is_created_and_the_stored_row_comes_back(monkeypatch):
    writer = _Writer(_stored())
    conn = _patch(monkeypatch, writer)

    profile = create_partner(
        _create(
            partner_id=NEW,
            partner_name="새 김치공장",
            partner_type="CUSTOMER",
            client_type="김치제조공장",
            factory_region="충청북도",
            factory_city="괴산군",
            sales_collection_days=30,
        )
    )

    #  ★ 한 쓰기 = 풀에서 빌린 연결 하나 · 트랜잭션 하나 (종전 `execute_returning_one` 경계).
    assert conn.borrows == ["write"]
    assert conn.events == ["commit", "returned:write"]
    assert profile.partner_id == NEW
    assert profile.partner_name == "새 김치공장"
    #  ★ 돌려주는 것은 입력이 아니라 저장된 행이다 — DB 기본값도 함께 온다.
    assert profile.provisional is False
    assert profile.credit_source == "finance:partner_credit_limits"


def test_the_insert_writes_only_columns_the_table_has(monkeypatch):
    writer = _Writer(_stored())
    _patch(monkeypatch, writer)

    create_partner(_create(partner_id=NEW, partner_name="새 김치공장", partner_type="CUSTOMER"))

    statement, params = writer.statements[0]
    assert "INSERT INTO" in statement
    #  ★ 쓰는 칸은 `RETURNING` 앞쪽이다. 뒤쪽은 읽어 오는 칸이라 목록이 다르다.
    written = statement.split("RETURNING")[0]
    #  🔴 자료 등급 칸은 화면이 정하는 값이 아니다 — DB 기본값으로 둔다.
    assert "provisional" not in written
    #  🔴 여신 정본 표를 판매가 건드리지 않는다.
    assert "partner_credit_limits" not in statement
    assert "credit" not in written
    assert len(params) == written.count("Placeholder()")


def test_a_duplicate_code_is_refused_instead_of_overwriting(monkeypatch):
    """🔴 이미 있는 코드를 덮으면 «새로 만들었다» 가 남의 이름을 바꾼 것이 된다."""
    writer = _Writer(None)
    conn = _patch(monkeypatch, writer)

    with pytest.raises(PartnerAlreadyExists) as raised:
        create_partner(
            _create(
                partner_id="KIMCHI_FACTORY_001",
                partner_name="덮어쓰기 시도",
                partner_type="CUSTOMER",
            )
        )

    #  ★ 만들지 못한 쓰기는 되돌리고 연결을 돌려준다. 사용자에게 가는 문장은 예외가 든다.
    assert conn.events == ["rollback", "returned:write"]
    assert raised.value.message == "이미 등록된 거래처 코드입니다: KIMCHI_FACTORY_001"
    statement, _ = writer.statements[0]
    assert "ON CONFLICT (partner_id) DO NOTHING" in statement
    assert "DO UPDATE" not in statement


def test_required_fields_are_not_invented():
    with pytest.raises(ValidationError):
        PartnerProfileCreate(partner_name="이름만", partner_type="CUSTOMER")
    with pytest.raises(ValidationError):
        PartnerProfileCreate(partner_id=NEW, partner_type="CUSTOMER")
    with pytest.raises(ValidationError):
        PartnerProfileCreate(partner_id=NEW, partner_name="", partner_type="CUSTOMER")


def test_a_partner_type_the_table_rejects_is_caught_before_the_database():
    """⚠️ DB CHECK 까지 보내면 사용자가 «constraint» 라는 말을 화면에서 읽는다."""
    with pytest.raises(ValidationError):
        PartnerProfileCreate(partner_id=NEW, partner_name="이름", partner_type="고객")

    for allowed in PARTNER_TYPES:
        assert PartnerProfileCreate(
            partner_id=NEW, partner_name="이름", partner_type=allowed
        ).partner_type == allowed


def test_a_negative_or_absurd_collection_term_is_refused():
    with pytest.raises(ValidationError):
        PartnerProfileCreate(
            partner_id=NEW, partner_name="이름", partner_type="CUSTOMER",
            sales_collection_days=-1,
        )
    with pytest.raises(ValidationError):
        PartnerProfileCreate(
            partner_id=NEW, partner_name="이름", partner_type="CUSTOMER",
            sales_collection_days=9999,
        )


def test_an_unknown_field_is_refused_rather_than_silently_dropped():
    with pytest.raises(ValidationError):
        PartnerProfileCreate(
            partner_id=NEW, partner_name="이름", partner_type="CUSTOMER",
            provisional=True,
        )


def test_the_service_refuses_a_credit_limit_without_touching_the_database(monkeypatch):
    """🔴 남의 도메인 칸은 **업무 예외**다 — HTTP 를 모르는 service 가 거절하고 연결도 안 빌린다."""
    conn = _patch(monkeypatch, _Writer(_stored()))

    with pytest.raises(PartnerInputRejected) as raised:
        create_partner(
            {
                "partner_id": NEW,
                "partner_name": "이름",
                "partner_type": "CUSTOMER",
                "credit_limit_krw": 10_000_000,
            }
        )

    assert "partner_credit_limits" in str(raised.value)
    assert conn.borrows == []


def test_the_route_refuses_a_credit_limit_instead_of_ignoring_it():
    """🔴 조용히 무시하면 사용자는 한도가 저장된 줄 알고 화면을 닫는다."""
    with pytest.raises(HTTPException) as raised:
        add_partner_profile(
            {
                "partner_id": NEW,
                "partner_name": "이름",
                "partner_type": "CUSTOMER",
                "credit_limit_krw": 10_000_000,
            }
        )

    assert raised.value.status_code == 422
    assert "partner_credit_limits" in str(raised.value.detail)


def test_the_route_reports_a_duplicate_as_a_conflict_not_a_bad_field(monkeypatch):
    _patch(monkeypatch, _Writer(None))

    with pytest.raises(HTTPException) as raised:
        add_partner_profile(
            {
                "partner_id": "KIMCHI_FACTORY_001",
                "partner_name": "이름",
                "partner_type": "CUSTOMER",
            }
        )

    assert raised.value.status_code == 409
    assert "KIMCHI_FACTORY_001" in str(raised.value.detail)


def test_the_route_names_the_field_a_user_sees_rather_than_the_model(monkeypatch):
    _patch(monkeypatch, _Writer(_stored()))

    with pytest.raises(HTTPException) as raised:
        add_partner_profile({"partner_name": "이름", "partner_type": "CUSTOMER"})

    detail = str(raised.value.detail)
    assert raised.value.status_code == 422
    assert "내부 거래처 코드" in detail
    #  ⚠️ 내부 모델 이름이 화면에 뜨지 않는다.
    assert "PartnerProfileCreate" not in detail


def test_creating_a_partner_returns_the_row_the_route_hands_back(monkeypatch):
    _patch(monkeypatch, _Writer(_stored()))

    profile = add_partner_profile(
        {"partner_id": NEW, "partner_name": "새 김치공장", "partner_type": "CUSTOMER"}
    )

    assert profile.partner_id == NEW
    assert profile.active is True


@contextmanager
def _borrow_without_connection_settings():
    """풀에서 연결을 빌리는 순간 접속 설정(`DB_HOST` 등)이 비어 있다."""
    raise MissingDatabaseEnvironment("Missing required database environment variables: DB_HOST")
    yield  # pragma: no cover


def test_a_setting_error_while_borrowing_still_reads_as_already_exists(monkeypatch):
    """⚠️ 종전 동작 그대로 — 쓰기 헬퍼의 `RuntimeError` 를 통째로 «이미 있다» 로 읽었다.

    설정 누락을 중복으로 알리는 것은 좁힐 만하지만, 재구성에서는 바꾸지 않는다
    (설계서 §변경 제안). 이 검사는 그 동작이 이동 중에 바뀌지 않았는지를 잰다.
    """
    monkeypatch.setattr("app.sales.repository.partners.get_db_schema", lambda: "haetdeul")
    monkeypatch.setattr("app.core.db.connection", _borrow_without_connection_settings)

    with pytest.raises(PartnerAlreadyExists) as raised:
        create_partner(_create(partner_id=NEW, partner_name="이름", partner_type="CUSTOMER"))

    assert isinstance(raised.value.__cause__, MissingDatabaseEnvironment)
    with pytest.raises(HTTPException) as via_route:
        add_partner_profile({"partner_id": NEW, "partner_name": "이름", "partner_type": "CUSTOMER"})
    assert via_route.value.status_code == 409


def test_a_missing_schema_name_stops_before_any_connection_is_borrowed(monkeypatch):
    """★ 종전 순서 그대로 — SQL 을 지을 때 `DB_SCHEMA` 를 읽고, 연결은 그 뒤에 빌린다.

    스키마 이름이 비면 그 오류가 «이미 있다» 로 바뀌지 않고 그대로 올라간다.
    """

    def no_schema() -> str:
        raise MissingDatabaseEnvironment(
            "Missing required database environment variables: DB_SCHEMA"
        )

    monkeypatch.setattr("app.sales.repository.partners.get_db_schema", no_schema)
    conn = lend(monkeypatch, _Writer(_stored()))

    with pytest.raises(MissingDatabaseEnvironment, match="DB_SCHEMA"):
        create_partner(_create(partner_id=NEW, partner_name="이름", partner_type="CUSTOMER"))

    assert conn.borrows == []
