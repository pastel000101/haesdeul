"""거래처 기본정보 쓰기 — 스키마가 가진 칸만.

판매가 소유한다. 거래처는 판매가 관계를 맺는 상대이고, 그 기본정보를 고치는 일은 판매
업무다. 실행(`sim_run_id`)과 무관한 원장 행이라 실행 축을 받지 않는다 — 받으면 «A 실행의
거래처 이름» 같은 없는 개념이 생긴다.

여신 한도를 여기서 고치지 않는다. 그 정본은 재무의 `partner_credit_limits` 이고
유효기간·근거등급·승인자를 가진 계약 행이다. 거래처 기본정보에 `credit_limit` 칸을 하나 더
두면 두 곳이 서로 다른 한도를 말하는 날이 오고, 그때 어느 쪽으로 판정이 났는지 아무도
답할 수 없다.

없는 칸을 만들지 않는다. 프로토타입이 요구한 담당자·전화·이메일은 `partners` 에 없다.
여기서 `note` 에 밀어 넣으면 그 값은 검색도 검증도 안 되는 문자열이 된다 — 필요하면
마이그레이션으로 칸을 내는 것이 맞다.

쓰기 두 유스케이스가 여기 있다 — 판매 라우터(`POST /sales/partners` ·
`PATCH /sales/partners/{id}/profile`)와 마스터 ask(`PARTNER_CREATE` · `PARTNER_UPDATE`)가 같은
함수를 부른다. 이 파일은 HTTP 를 모르고 업무 예외(`schemas/partners.py`)를 낸다 — 라우터
핸들러의 `HTTPException` 이 ask 경로로 새어 나가지 않게 하려는 것이다.

한 쓰기 = 풀에서 빌린 연결 하나 · 트랜잭션 하나. SQL 을 먼저 짓고(여기서 `DB_SCHEMA` 를
읽는다 — 비었으면 그 오류가 그대로 올라간다), 연결을 빌려 실행하는 동안 난 `RuntimeError` 를
«이미 있다» · «없다» 로 읽는다. 주의: 그 `RuntimeError` 에는 «행이 안 나왔다» 말고 연결을 열다
난 설정 누락(`MissingDatabaseEnvironment`)도 들어간다. 좁힐지는 설계서 §변경 제안에 적혀 있다.
"""

from pydantic import ValidationError

from app.core import db as core_db
from app.sales.domain.partners import foreign_field_problem, readable_validation_error
from app.sales.readmodel.partners import get_partner_profile
from app.sales.repository.partners import (
    insert_partner_statement,
    update_partner_statement,
    write_partner,
)
from app.sales.schemas.partners import (
    EDITABLE_FIELDS,
    PartnerAlreadyExists,
    PartnerInputRejected,
    PartnerNotFound,
    PartnerProfile,
    PartnerProfileCreate,
    PartnerProfileUpdate,
)


def create_partner(body: dict[str, object]) -> PartnerProfile:
    """새 거래처를 만들고 저장된 행을 돌려준다.

    실행 축(`sim_run_id`)을 받지 않는다. 거래처는 실행과 무관한 원장 행이라
    «A 실행의 거래처» 라는 개념이 없다 — `update_partner` 와 같은 규율이다.

    여신 한도는 여기서 만들지 않는다. 정본은 재무의 `partner_credit_limits` 이고,
    같은 이름의 칸을 거래처 행에 두면 두 곳이 다른 한도를 말하는 날이 온다.

    :raises PartnerInputRejected: 남의 도메인 칸이거나 칸 검증에 걸렸다.
    :raises PartnerAlreadyExists: 같은 거래처 코드가 이미 있다. 덮어쓰지 않는다.
    """
    problem = foreign_field_problem(body)
    if problem is not None:
        raise PartnerInputRejected(problem)
    try:
        create = PartnerProfileCreate.model_validate(body)
    except ValidationError as error:
        raise PartnerInputRejected(readable_validation_error(error)) from error
    write = insert_partner_statement(values=create.model_dump())
    try:
        with core_db.connection() as conn, core_db.transaction(conn):
            row = write_partner(conn, write)
    except RuntimeError as error:
        #  `DO NOTHING` 이라 충돌하면 행이 안 나온다 — 그것이 «이미 있다» 의 신호다.
        raise PartnerAlreadyExists(create.partner_id) from error
    return PartnerProfile(**row)


def update_partner(partner_id: str, body: dict[str, object]) -> PartnerProfile:
    """준 칸만 고치고 저장된 결과를 돌려준다.

    남의 도메인 값은 조용히 무시하지 않고 거절한다. 무시하면 사용자는 고쳐진 줄 알고
    화면을 닫는다 — 여신 한도가 특히 그렇다.

    주의: 칸 검증 오류는 생성과 문장 모양이 다르다 — 생성은 칸 이름을 사람 말로 바꾸고
    (`readable_validation_error`), 수정은 검증 오류 원문(`str(error)`)을 싣는다.

    :raises PartnerInputRejected: 남의 도메인 칸이거나 칸 검증에 걸렸다.
    :raises PartnerNotFound: 그 거래처가 없다.
    """
    problem = foreign_field_problem(body)
    if problem is not None:
        raise PartnerInputRejected(problem)
    try:
        update = PartnerProfileUpdate.model_validate(body)
    except ValidationError as error:
        raise PartnerInputRejected(str(error)) from error
    changes = update.model_dump(exclude_unset=True)
    editable = [name for name in changes if name in EDITABLE_FIELDS]
    if not editable:
        #  고칠 칸이 없으면 쓰지 않고 지금 행을 돌려준다.
        profile = get_partner_profile(partner_id=partner_id)
        if profile is None:
            raise PartnerNotFound(partner_id)
        return profile
    write = update_partner_statement(
        partner_id=partner_id, changes={name: changes[name] for name in editable}
    )
    try:
        with core_db.connection() as conn, core_db.transaction(conn):
            row = write_partner(conn, write)
    except RuntimeError as error:
        #  고칠 행이 안 나왔다 — 없는 거래처라는 뜻이다.
        raise PartnerNotFound(partner_id) from error
    return PartnerProfile(**row)
