"""발화 슬롯 해석 — 금액 · 정수 · 날짜 · 기간 · 빠진 슬롯을 입력값만으로 판정한다(DB · LLM 없음).

★ 2026-09-30 재구성 BL-018: `master/ask_service.py` 에서 옮겼다 — `_DOMAIN_REQUIRED`,
  `DomainClarification`, `slots_of`, `_slot`, `missing_domain_slots`, `_SLOT_LABELS`,
  `missing_message`, `dump`, `won`, `money`, `integer`, `user_date`, `period_of`,
  `has_report_period`.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from app.master.llm.schemas import DomainSlots, Intent

_DOMAIN_REQUIRED: dict[str, tuple[str, ...]] = {
    "FINANCE_CASH_ADJUSTMENT_CREATE": ("direction", "amount", "source_ref"),
    "FINANCE_CREDIT_LIMIT_GET": ("partner_ref",),
    "FINANCE_CREDIT_LIMIT_UPSERT": (
        "partner_ref",
        "credit_limit",
        "effective_from",
        "evidence_grade",
        "source_ref",
    ),
    "FINANCE_EXPENSE_CREATE": (
        "expense_date",
        "due_date",
        "expense_category",
        "amount",
        "evidence_id",
    ),
    "FINANCE_EXPENSE_SETTLE": ("expense_id", "paid_date"),
    "FINANCE_EXPENSE_CANCEL": ("expense_id",),
    "SALES_PROPOSAL_CREATE": ("partner_ref", "business_mode", "item", "requested_quantity_kg"),
    "PARTNER_CREATE": ("partner_id", "partner_name", "partner_type"),
    "PARTNER_DETAIL_GET": ("partner_ref",),
    "PARTNER_UPDATE": ("partner_ref",),
}


class DomainClarification(ValueError):
    pass


def slots_of(intent: Intent) -> DomainSlots:
    return intent.slots or DomainSlots()


def _slot(intent: Intent, name: str):
    if name == "item":
        return intent.item
    return getattr(slots_of(intent), name, None)


def missing_domain_slots(intent: Intent) -> list[str]:
    action = intent.domain_action
    if action is None:
        return ["domain_action"]
    missing = [
        name for name in _DOMAIN_REQUIRED.get(action, ()) if _slot(intent, name) in (None, "")
    ]

    if action == "FINANCE_COLLECTION_CREATE":
        slots = slots_of(intent)
        if not slots.receivable_id and not slots.partner_ref:
            missing.append("receivable_id 또는 partner_ref")
        if not slots.collect_all and not slots.amount:
            missing.append("amount 또는 collect_all")

    if action == "PARTNER_UPDATE":
        slots = slots_of(intent)
        editable = (
            slots.partner_name,
            slots.partner_type,
            slots.client_type,
            slots.factory_region,
            slots.factory_city,
            slots.factory_area,
            slots.sales_collection_days,
            slots.pricing_contract_type,
            slots.active,
            slots.note,
        )
        if all(value is None for value in editable):
            missing.append("수정할 거래처 정보")
    return missing


_SLOT_LABELS = {
    "partner_ref": "거래처",
    "credit_limit": "새 여신한도",
    "effective_from": "한도 적용 시작일",
    "evidence_grade": "한도 근거 등급(공식 계약/거래처 확인/시뮬레이션 고정)",
    "source_ref": "확인 자료",
    "direction": "입금/출금 구분",
    "amount": "금액",
    "expense_id": "비용 번호",
    "expense_category": "비용 분류",
    "expense_date": "비용 발생일",
    "due_date": "지급 예정일",
    "paid_date": "실제 지급일",
    "evidence_id": "비용 근거 번호",
    "business_mode": "판매 유형",
    "item": "품목",
    "requested_quantity_kg": "판매 요청 수량",
    "partner_id": "내부 거래처 코드",
    "partner_name": "거래처명",
    "partner_type": "거래처 유형",
    "receivable_id 또는 partner_ref": "받을 돈 번호 또는 거래처",
    "amount 또는 collect_all": "수금액 또는 전액 수금 여부",
    "수정할 거래처 정보": "수정할 거래처 정보",
}


def missing_message(missing: list[str]) -> str:
    names = [_SLOT_LABELS.get(name, name) for name in missing]
    if len(names) == 1:
        return f"{names[0]}을 알려주세요."
    return "계속하려면 " + ", ".join(names) + "을 알려주세요."


def dump(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return [dump(item) for item in value]
    return value


def won(value) -> str:
    if value is None:
        return "—"
    try:
        return f"{Decimal(str(value)):,.0f}원"
    except (InvalidOperation, ValueError, TypeError):
        return "—"


def money(raw: str | None, *, field: str, allow_zero: bool = False) -> Decimal:
    """사용자가 말한 금액 표현만 deterministic하게 원으로 바꾼다."""
    if not raw:
        raise DomainClarification(f"{field}을(를) 알려주세요.")
    text = raw.strip().replace(",", "").replace(" ", "")
    text = text.removesuffix("원")
    multipliers = (
        ("천만", Decimal(10_000_000)),
        ("백만", Decimal(1_000_000)),
        ("십만", Decimal(100_000)),
        ("억", Decimal(100_000_000)),
        ("만", Decimal(10_000)),
        ("천", Decimal(1_000)),
        ("백", Decimal(100)),
        ("십", Decimal(10)),
    )
    multiplier = Decimal(1)
    for suffix, factor in multipliers:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            multiplier = factor
            break
    if not re.fullmatch(r"\d+(?:\.\d+)?", text or ""):
        raise DomainClarification(
            f"{field}을(를) 원 단위가 분명하게 다시 말씀해 주세요. 예: 300만원, 3000000원"
        )
    value = Decimal(text) * multiplier
    if value < 0 or (value == 0 and not allow_zero):
        rule = "0 이상" if allow_zero else "0보다 크게"
        raise DomainClarification(f"{field}은(는) {rule} 알려주세요.")
    return value


def integer(raw: str | None, *, field: str, allow_zero: bool = True) -> int | None:
    if raw is None:
        return None
    text = raw.strip().replace("일", "")
    if not text.isdigit():
        raise DomainClarification(f"{field}을(를) 숫자로 알려주세요.")
    value = int(text)
    if value < 0 or (value == 0 and not allow_zero):
        raise DomainClarification(f"{field} 값이 올바르지 않습니다.")
    return value


def user_date(raw: str | None, *, as_of: date, field: str, required: bool = False) -> date | None:
    if raw is None:
        if required:
            raise DomainClarification(f"{field}을(를) 알려주세요.")
        return None
    text = raw.strip()
    relative = {
        "오늘": as_of,
        "금일": as_of,
        "어제": as_of - timedelta(days=1),
        "내일": as_of + timedelta(days=1),
    }
    if text in relative:
        return relative[text]
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    match = re.fullmatch(r"(\d{1,2})월\s*(\d{1,2})일", text)
    if match:
        try:
            return date(as_of.year, int(match.group(1)), int(match.group(2)))
        except ValueError:
            pass
    raise DomainClarification(
        f"{field}을(를) 날짜가 분명하게 다시 말씀해 주세요. 예: 오늘, 2026-09-17"
    )


def period_of(intent: Intent, *, as_of: date) -> tuple[date, date]:
    slots = slots_of(intent)
    if slots.start_date or slots.end_date:
        start = user_date(slots.start_date, as_of=as_of, field="시작일") or as_of
        end = user_date(slots.end_date, as_of=as_of, field="종료일") or as_of
        if start > end:
            raise DomainClarification("시작일은 종료일보다 늦을 수 없습니다.")
        return start, min(end, as_of)

    period = slots.period or "TODAY"
    if period == "TODAY":
        return as_of, as_of
    if period == "YESTERDAY":
        day = as_of - timedelta(days=1)
        return day, day
    if period == "THIS_WEEK":
        return as_of - timedelta(days=as_of.weekday()), as_of
    if period == "LAST_WEEK":
        this_monday = as_of - timedelta(days=as_of.weekday())
        return this_monday - timedelta(days=7), this_monday - timedelta(days=1)
    if period == "THIS_MONTH":
        return as_of.replace(day=1), as_of
    if period == "LAST_7_DAYS":
        return as_of - timedelta(days=6), as_of
    if period == "LAST_30_DAYS":
        return as_of - timedelta(days=29), as_of
    if period == "LAST_3_MONTHS":
        month = as_of.month - 2
        year = as_of.year
        if month <= 0:
            month += 12
            year -= 1
        return as_of.replace(year=year, month=month, day=1), as_of
    if period == "LAST_YEAR":
        try:
            return as_of.replace(year=as_of.year - 1), as_of
        except ValueError:  # 2월 29일의 전년은 2월 28일
            return as_of.replace(year=as_of.year - 1, day=28), as_of
    raise DomainClarification("기간을 확인해 주세요.")


def has_report_period(intent: Intent) -> bool:
    """Report generation must not silently turn an omitted period into TODAY."""
    slots = slots_of(intent)
    return bool(slots.period or slots.start_date or slots.end_date)
