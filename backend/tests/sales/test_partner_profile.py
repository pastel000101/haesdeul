"""거래처 기본정보 — 스키마가 가진 칸만, 남의 도메인 값은 거절.

★ 2026-09-29 BL-013: 한 건 조회는 `readmodel/partners`, 수정은 `service/partners.update_partner`
  (SQL 은 `repository/partners`)다. 판매 라우터와 마스터 ask 가 같은 수정 함수를 부른다.
  없는 거래처는 `None` 대신 업무 예외(`PartnerNotFound`)로 올라온다 — 라우터가 404 로 옮긴다.
"""

from contextlib import contextmanager

import pytest
from pydantic import ValidationError

from app.core.settings import MissingDatabaseEnvironment
from app.sales.readmodel.partners import get_partner_profile
from app.sales.schemas.partners import (
    FOREIGN_FIELDS,
    PartnerInputRejected,
    PartnerNotFound,
    PartnerProfileUpdate,
)
from app.sales.service.partners import update_partner
from tests.sales.sales_fake_connection import lend

PARTNER = "KIMCHI_FACTORY_001"


def _row(**over) -> dict:
    return {
        "partner_id": PARTNER,
        "partner_name": "김치제조공장",
        "partner_type": "CUSTOMER",
        "client_type": "김치제조공장",
        "factory_region": "경기도",
        "factory_city": "안성시",
        "factory_area": "원곡면",
        "sales_collection_days": 30,
        "pricing_contract_type": "MARKET_LINKED_COST_PLUS_CM",
        "active": True,
        "provisional": True,
        "note": None,
        **over,
    }


class _Writer:
    """`UPDATE … RETURNING` 흉내. 없는 거래처면 행이 안 나온다."""

    def __init__(self, row: dict | None = None):
        self.row = row
        self.statements: list[tuple[str, list]] = []

    def __call__(self, query, params):
        self.statements.append((str(query), list(params)))
        return [] if self.row is None else [dict(self.row)]


def _patch(monkeypatch, *, writer=None, rows=None):
    monkeypatch.setattr("app.sales.repository.partners.get_db_schema", lambda: "haetdeul")

    def answer(query, params):
        if "UPDATE" in str(query):
            assert writer is not None, "이 검사는 쓰지 않아야 한다"
            return writer(query, params)
        return list(rows or [])

    return lend(monkeypatch, answer)


def test_reading_a_partner_returns_the_stored_row(monkeypatch):
    _patch(monkeypatch, rows=[_row()])

    profile = get_partner_profile(partner_id=PARTNER)

    assert profile is not None
    assert profile.partner_name == "김치제조공장"
    assert profile.sales_collection_days == 30
    # 🔴 여신은 이 행에 없다. 어디에 물어야 하는지를 칸으로 말한다.
    assert profile.credit_source == "finance:partner_credit_limits"


def test_an_unknown_partner_is_not_invented(monkeypatch):
    _patch(monkeypatch, rows=[])

    assert get_partner_profile(partner_id="NOPE") is None


def test_only_the_given_fields_are_written(monkeypatch):
    """⚠️ 안 준 칸은 안 고친다 — 전체 덮어쓰기가 아니다."""
    writer = _Writer(_row(partner_name="새 이름"))
    conn = _patch(monkeypatch, writer=writer)

    profile = update_partner(PARTNER, {"partner_name": "새 이름"})

    #  ★ 한 수정 = 연결 하나 · 트랜잭션 하나.
    assert conn.borrows == ["write"]
    assert conn.events == ["commit", "returned:write"]
    assert profile.partner_name == "새 이름"
    statement, params = writer.statements[0]
    assert "partner_name" in statement
    #  준 칸 하나와 WHERE 의 거래처 하나 — 다른 칸은 SET 에 없다.
    assert params == ["새 이름", PARTNER]
    assert "factory_region" not in statement.split("WHERE")[0]


def test_updating_an_unknown_partner_reports_missing(monkeypatch):
    conn = _patch(monkeypatch, writer=_Writer(None))

    with pytest.raises(PartnerNotFound) as raised:
        update_partner("NOPE", {"partner_name": "x"})

    assert raised.value.message == "거래처를 찾지 못했습니다."
    #  ★ 행이 안 나온 쓰기는 되돌리고 연결을 돌려준다.
    assert conn.events == ["rollback", "returned:write"]


def test_a_foreign_field_is_refused_before_any_connection(monkeypatch):
    """🔴 남의 도메인 값은 조용히 무시하지 않는다 — service 가 거절하고 연결도 안 빌린다."""
    conn = _patch(monkeypatch, writer=_Writer(_row()))

    with pytest.raises(PartnerInputRejected) as raised:
        update_partner(PARTNER, {"credit_limit": 1})

    assert str(raised.value) == FOREIGN_FIELDS["credit_limit"]
    assert conn.borrows == []


def test_the_result_is_what_was_stored_not_what_was_sent(monkeypatch):
    """★ `RETURNING` 을 돌려준다 — 화면이 «고쳐졌다고 믿는 값» 이 아니라 저장된 값이다."""
    writer = _Writer(_row(partner_name="장부가 가진 이름"))
    _patch(monkeypatch, writer=writer)

    profile = update_partner(PARTNER, {"partner_name": "보낸 이름"})

    assert profile.partner_name == "장부가 가진 이름"


def test_credit_limit_is_not_a_partner_field():
    """🔴 여신 한도를 거래처 기본정보로 중복 저장하지 않는다."""
    assert "credit_limit" in FOREIGN_FIELDS
    assert "credit_limit_krw" in FOREIGN_FIELDS
    with pytest.raises(ValidationError):
        PartnerProfileUpdate.model_validate({"credit_limit": 10_000_000})


@pytest.mark.parametrize("name", ["contact", "phone", "email"])
def test_fields_the_schema_lacks_are_refused_not_stuffed_into_note(name):
    """🔴 없는 칸을 `note` 에 밀어 넣지 않는다 — 검색도 검증도 안 되는 값이 된다."""
    assert name in FOREIGN_FIELDS
    with pytest.raises(ValidationError):
        PartnerProfileUpdate.model_validate({name: "값"})


def test_a_negative_collection_day_is_refused():
    with pytest.raises(ValidationError):
        PartnerProfileUpdate.model_validate({"sales_collection_days": -1})


def test_an_empty_update_touches_nothing(monkeypatch):
    writer = _Writer(_row())
    conn = _patch(monkeypatch, writer=writer, rows=[_row()])

    profile = update_partner(PARTNER, {})

    assert profile.partner_id == PARTNER
    #  🔴 빈 수정은 `UPDATE` 를 돌리지 않는다 — 안 바뀐 행을 굳이 다시 쓰지 않는다.
    assert writer.statements == []
    #  ★ 지금 행을 읽기만 한다 — 조회 연결 하나, commit 없음.
    assert conn.borrows == ["read"]
    assert conn.events == ["returned:read"]


def test_the_partner_row_is_not_run_scoped(monkeypatch):
    """★ 거래처 원장은 실행과 무관하다 — `sim_run_id` 를 받지도 걸지도 않는다."""
    writer = _Writer(_row())
    _patch(monkeypatch, writer=writer)

    update_partner(PARTNER, {"active": False})

    statement, _ = writer.statements[0]
    assert "sim_run_id" not in statement


@contextmanager
def _borrow_without_connection_settings():
    """풀에서 연결을 빌리는 순간 접속 설정(`DB_HOST` 등)이 비어 있다."""
    raise MissingDatabaseEnvironment("Missing required database environment variables: DB_HOST")
    yield  # pragma: no cover


def test_a_setting_error_while_borrowing_still_reads_as_not_found(monkeypatch):
    """⚠️ 종전 동작 그대로 — 쓰기 헬퍼의 `RuntimeError` 를 통째로 «없다» 로 읽었다
    (설계서 §변경 제안). 이동 중에 바뀌지 않았는지를 잰다."""
    monkeypatch.setattr("app.sales.repository.partners.get_db_schema", lambda: "haetdeul")
    monkeypatch.setattr("app.core.db.connection", _borrow_without_connection_settings)

    with pytest.raises(PartnerNotFound) as raised:
        update_partner(PARTNER, {"partner_name": "새 이름"})

    assert isinstance(raised.value.__cause__, MissingDatabaseEnvironment)


def test_a_missing_schema_name_stops_the_update_before_any_connection_is_borrowed(monkeypatch):
    """★ 종전 순서 그대로 — SQL 을 지을 때 `DB_SCHEMA` 를 읽고, 연결은 그 뒤에 빌린다."""

    def no_schema() -> str:
        raise MissingDatabaseEnvironment(
            "Missing required database environment variables: DB_SCHEMA"
        )

    monkeypatch.setattr("app.sales.repository.partners.get_db_schema", no_schema)
    conn = lend(monkeypatch, lambda _query, _params: [_row()])

    with pytest.raises(MissingDatabaseEnvironment, match="DB_SCHEMA"):
        update_partner(PARTNER, {"partner_name": "새 이름"})

    assert conn.borrows == []
