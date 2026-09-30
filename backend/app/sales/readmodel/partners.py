"""거래처 기본정보 한 건 조회 — 원장 행 그대로. 🔴 여신 한도는 여기 없다 (재무 정본).

★ 2026-09-29 BL-013: `sales/partner_profile.py::get_partner_profile` 을 옮겼다. SQL 은
  `repository/partners.py` 다. 판매 라우터(`GET /sales/partners/{id}/profile`), 거래처 수정
  (`service/partners.py` — 고칠 칸이 없을 때), 판매 후보 생성의 결제일수 보강이 부른다.
"""

from app.core import db as core_db
from app.sales.repository.partners import find_partner
from app.sales.schemas.partners import PartnerProfile


def get_partner_profile(*, partner_id: str) -> PartnerProfile | None:
    """거래처 한 건. 없으면 `None` 이다."""
    with core_db.read_connection() as conn:
        row = find_partner(conn, partner_id=partner_id)
    return None if row is None else PartnerProfile(**row)
