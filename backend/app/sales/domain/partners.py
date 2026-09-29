"""거래처 입력을 받을 수 있는가 — 남의 도메인 칸 거절과 사용자 문장.

★ 2026-09-29 BL-013: `sales/router.py` 의 외래 칸 검사와 `_readable` 을 옮겼다. 전에는 이
  검사가 HTTP 핸들러 안에 있어, 마스터 ask 가 같은 검사를 받으려고 **라우터 핸들러를 함수로
  불렀다**. 이제 `service/partners.py` 가 부르고, 라우터와 마스터가 그 service 를 부른다.
"""

from collections.abc import Mapping

from pydantic import ValidationError

from app.sales.schemas.partners import FIELD_LABELS, FOREIGN_FIELDS


def foreign_field_problem(body: Mapping[str, object]) -> str | None:
    """남의 도메인 칸을 받았으면 **그 주인을 말하는 문장**, 아니면 `None`.

    🔴 **조용히 무시하지 않고 거절한다.** 무시하면 사용자는 고쳐진 줄 알고 화면을 닫는다 —
       여신 한도가 특히 그렇다.
    """
    foreign = sorted(name for name in body if name in FOREIGN_FIELDS)
    if not foreign:
        return None
    return " ".join(FOREIGN_FIELDS[name] for name in foreign)


def readable_validation_error(error: ValidationError) -> str:
    """Pydantic 오류를 사용자 문장으로 옮긴다. **원인을 숨기지 않는다.**

    ⚠️ `str(error)` 를 그대로 내면 `1 validation error for PartnerProfileCreate` 같은
      내부 모델 이름이 화면에 뜬다. 어느 칸이 왜 막혔는지는 그대로 나른다.
    """
    lines = []
    for item in error.errors():
        field = ".".join(str(part) for part in item["loc"]) or "입력"
        lines.append(f"{FIELD_LABELS.get(field, field)}: {item['msg']}")
    return " / ".join(lines)
