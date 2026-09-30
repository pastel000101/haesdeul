"""거래처 기본정보 계약 — 입력 · 저장 모양, 받으면 거절하는 이름, 업무 예외.

★ 2026-09-29 BL-013: 모델과 거절 목록은 `sales/partner_profile.py`, 칸 이름 사전은
  `sales/router.py`(`_FIELD_LABELS`)에서 옮겼다. 쓰기 순서는 `service/partners.py`,
  SQL 은 `repository/partners.py`, 한 건 조회는 `readmodel/partners.py` 다.

★ **예외가 곧 업무 결과다.** service 는 HTTP 상태 코드를 모른다 — 판매 라우터가
  `PartnerAlreadyExists` 를 409, `PartnerNotFound` 를 404, `PartnerInputRejected` 를 422 로,
  마스터 ask 가 같은 문장의 거절로 옮긴다.
"""

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: 고칠 수 있는 칸. **표에 있는 것만** 적는다.
EDITABLE_FIELDS = (
    "partner_name",
    "partner_type",
    "client_type",
    "factory_region",
    "factory_city",
    "factory_area",
    "sales_collection_days",
    "pricing_contract_type",
    "active",
    "note",
)

#: 새 거래처에 쓸 수 있는 칸. **`EDITABLE_FIELDS` 에 `partner_id` 만 더한다.**
#:
#: ★ 두 목록이 갈리면 «수정은 되는데 생성은 안 되는 칸» 이 생긴다. 생성은 식별자를
#:   받아야 하므로 그 하나만 다르다.
CREATABLE_FIELDS = ("partner_id", *EDITABLE_FIELDS)


#: 받으면 **거절**하는 이름. 조용히 무시하면 사용자는 고쳐진 줄 안다.
FOREIGN_FIELDS = {
    "credit_limit": "여신 한도는 재무 정본입니다 (partner_credit_limits).",
    "credit_limit_krw": "여신 한도는 재무 정본입니다 (partner_credit_limits).",
    "contact": "담당자 칸이 partners 에 없습니다 — 마이그레이션이 필요합니다.",
    "phone": "전화 칸이 partners 에 없습니다 — 마이그레이션이 필요합니다.",
    "email": "이메일 칸이 partners 에 없습니다 — 마이그레이션이 필요합니다.",
}


class PartnerProfileUpdate(BaseModel):
    """거래처 기본정보 수정 입력. **안 준 칸은 안 고친다.**

    ⚠️ `None` 과 «안 줬다» 를 가른다 — `extra="forbid"` 라 모르는 칸은 거절되고,
      준 칸만 `UPDATE` 에 들어간다. 전체 덮어쓰기가 아니다.
    """

    model_config = ConfigDict(extra="forbid")

    partner_name: str | None = Field(default=None, min_length=1)
    partner_type: str | None = Field(default=None, min_length=1)
    client_type: str | None = None
    factory_region: str | None = None
    factory_city: str | None = None
    factory_area: str | None = None
    sales_collection_days: int | None = Field(default=None, ge=0)
    pricing_contract_type: str | None = None
    active: bool | None = None
    note: str | None = None


class PartnerProfile(BaseModel):
    """저장된 거래처 기본정보 그대로."""

    model_config = ConfigDict(extra="forbid")

    partner_id: str
    partner_name: str
    partner_type: str
    client_type: str | None
    factory_region: str | None
    factory_city: str | None
    factory_area: str | None
    sales_collection_days: int | None
    pricing_contract_type: str | None
    active: bool
    provisional: bool
    note: str | None
    #: 🔴 여신은 여기 없다. 재무에 물어야 한다는 사실을 칸으로 말한다.
    credit_source: str = "finance:partner_credit_limits"

#: `partners_partner_type_check` 가 실제로 허용하는 값 (2026-09-14 실측).
#:
#: 🔴 **여기 없는 값을 보내면 DB 제약이 거절한다.** 그 예외는 psycopg 원문이라
#:    사용자에게 그대로 보이면 «CHECK constraint» 라는 말이 화면에 뜬다. 미리 거른다.
PARTNER_TYPES = ("CUSTOMER", "SUPPLIER", "LOGISTICS_PROVIDER", "MARKET_REFERENCE", "OTHER")


class PartnerAlreadyExists(ValueError):
    """같은 `partner_id` 가 이미 있다. **덮어쓰지 않는다.**

    ★ `INSERT … ON CONFLICT DO UPDATE` 로 조용히 덮으면 «새 거래처를 만들었다» 는
      화면이 실제로는 **남의 거래처 이름을 바꾼 것**이 된다.
    """

    def __init__(self, partner_id: str) -> None:
        super().__init__(partner_id)
        self.partner_id = partner_id
        #: 사용자에게 가는 말. 판매 라우터(409)와 마스터 ask 가 **같은 문장**을 쓴다.
        self.message = f"이미 등록된 거래처 코드입니다: {partner_id}"


class PartnerProfileCreate(BaseModel):
    """새 거래처 입력. **표에 있는 칸만, 기본값은 DB 가 가진 것과 같게.**

    🔴 **`provisional` 을 받지 않는다.** 그 칸은 «이 행이 잠정 자료인가» 라는 자료
       등급이고, 화면에서 고를 값이 아니다 — DB 기본값(`false`) 그대로 둔다.

    🔴 **여신 한도 칸이 없다.** 정본은 재무의 `partner_credit_limits` 다
       (`FOREIGN_FIELDS` 가 이름으로 거절한다).
    """

    model_config = ConfigDict(extra="forbid")

    #: 사용자에게는 «내부 거래처 코드» 로 보인다. **형식을 코드가 만들지 않는다** —
    #: 저장소에 `partner_id` 생성 규칙이 없어(2026-09-14 전수 확인) 임의 형식을
    #: 지어내면 그날부터 그것이 규칙이 된다.
    partner_id: str = Field(min_length=1, max_length=64)
    partner_name: str = Field(min_length=1)
    partner_type: str = Field(min_length=1)
    client_type: str | None = None
    factory_region: str | None = None
    factory_city: str | None = None
    factory_area: str | None = None
    sales_collection_days: int | None = Field(default=None, ge=0, le=365)
    pricing_contract_type: str | None = None
    active: bool = True
    note: str | None = None

    @model_validator(mode="after")
    def partner_type_is_one_the_table_allows(self) -> "PartnerProfileCreate":
        if self.partner_type not in PARTNER_TYPES:
            raise ValueError(
                "거래처 유형은 " + " · ".join(PARTNER_TYPES) + " 중 하나여야 합니다."
            )
        return self


#: 칸 이름을 사용자가 읽는 말로 바꾼다. **값은 바꾸지 않는다.**
FIELD_LABELS = {
    "partner_id": "내부 거래처 코드",
    "partner_name": "거래처명",
    "partner_type": "거래처 유형",
    "sales_collection_days": "결제일수",
}


class PartnerNotFound(LookupError):
    """고칠 거래처가 없다. **만들지 않는다.**"""

    def __init__(self, partner_id: str) -> None:
        super().__init__(partner_id)
        self.partner_id = partner_id
        #: 사용자에게 가는 말. 판매 라우터(404)와 마스터 ask 가 **같은 문장**을 쓴다.
        self.message = "거래처를 찾지 못했습니다."


class PartnerInputRejected(ValueError):
    """입력을 받을 수 없다 — 남의 도메인 칸이거나 칸 검증에 걸렸다.

    ★ **문장이 곧 사용자에게 가는 말이다** (`str(error)`). 판매 라우터는 422 의 `detail`
      로, 마스터 ask 는 같은 문장의 거절로 싣는다.
    """
