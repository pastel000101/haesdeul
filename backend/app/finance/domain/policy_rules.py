"""재무 정책 행 검증 — 닫힌 키 목록 · 값 칸 · 출처로 `FinancePolicy` 를 세운다.

★ 2026-09-29 재구성 BL-014: `finance/db.py` 에서 옮겼다(몸통 그대로). 정책 행 SQL 은
  `repository/policy.py`, 조회
  연결은 `readmodel/policy.py`.
"""

from decimal import Decimal

from app.finance.schemas.agent import FinanceDebtPolicy, FinancePolicy
from app.finance.schemas.policy import FINANCE_POLICY_USAGE_SCOPE, FINANCE_POLICY_VERSION

_NUMERIC_POLICY_KEYS = {
    "purchase_payment_days",
    "payroll_date",
    "margin_defense_floor_rate",
    "monthly_labor_cost_krw",
    "minimum_cash_balance_krw",
    "cashflow_projection_days",
    "cash_priority_high_ratio",
    "cash_priority_medium_ratio",
}
_TEXT_POLICY_KEYS = {"cash_priority_reference"}
_OPTIONAL_POLICY_KEYS = {
    "purchase_payment_days",
    "margin_defense_floor_rate",
    "monthly_labor_cost_krw",
}
_REQUIRED_POLICY_KEYS = (_NUMERIC_POLICY_KEYS | _TEXT_POLICY_KEYS) - _OPTIONAL_POLICY_KEYS
_KNOWN_POLICY_KEYS = _REQUIRED_POLICY_KEYS | _OPTIONAL_POLICY_KEYS
_DEBT_NUMERIC_POLICY_KEYS = {
    "debt_principal_krw",
    "debt_annual_rate",
    "debt_term_months",
    "debt_grace_months",
}
_DEBT_TEXT_POLICY_KEYS = {
    "debt_runtime_status",
    "debt_execution_date",
    "debt_grace_payment_mode",
    "debt_repayment_method",
    "debt_payment_frequency",
    "debt_payment_day_rule",
    "debt_first_payment_rule",
    "debt_interest_method",
}
_REQUIRED_DEBT_POLICY_KEYS = _DEBT_NUMERIC_POLICY_KEYS | _DEBT_TEXT_POLICY_KEYS


def build_finance_policy(rows: list[dict[str, object]]) -> FinancePolicy:
    """정책 행을 닫힌 키 목록으로 검증해 `FinancePolicy` 로 옮긴다."""
    values: dict[str, object] = {}
    source_refs: dict[str, str] = {}

    for row in rows:
        key = row.get("policy_key")
        if key not in _KNOWN_POLICY_KEYS:
            continue
        if key in values:
            raise ValueError(f"Duplicate Finance policy key: {key}")
        if row.get("policy_version") != FINANCE_POLICY_VERSION:
            raise ValueError(f"Finance policy_version mismatch: {key}")
        if row.get("usage_scope") != FINANCE_POLICY_USAGE_SCOPE:
            raise ValueError(f"Finance policy usage_scope mismatch: {key}")

        kind = row.get("value_kind")
        expected_kind = "NUMERIC" if key in _NUMERIC_POLICY_KEYS else "TEXT"
        if kind != expected_kind:
            raise ValueError(f"Invalid value_kind for Finance policy {key}: {kind}")
        selected_column = "value_numeric" if kind == "NUMERIC" else "value_text"
        unused_columns = {"value_numeric", "value_text", "value_json"} - {selected_column}
        value = row.get(selected_column)
        if value is None and key in _OPTIONAL_POLICY_KEYS:
            if any(row.get(column) is not None for column in unused_columns):
                raise ValueError(f"Inconsistent value columns for Finance policy: {key}")
            values[key] = None
            source_ref = row.get("source_ref")
            if isinstance(source_ref, str) and source_ref:
                source_refs[key] = source_ref
            continue
        if value is None or any(row.get(column) is not None for column in unused_columns):
            raise ValueError(f"Inconsistent value columns for Finance policy: {key}")
        if kind == "NUMERIC" and (isinstance(value, bool) or not isinstance(value, Decimal)):
            raise TypeError(f"Invalid Python NUMERIC value for Finance policy: {key}")
        if kind == "TEXT" and not isinstance(value, str):
            raise TypeError(f"Invalid Python TEXT value for Finance policy: {key}")

        source_ref = row.get("source_ref")
        if not isinstance(source_ref, str) or not source_ref:
            raise ValueError(f"Missing source_ref for Finance policy: {key}")
        values[key] = value
        source_refs[key] = source_ref

    missing = _REQUIRED_POLICY_KEYS - values.keys()
    if missing:
        raise LookupError(f"Required Finance policies were not found: {', '.join(sorted(missing))}")

    values.setdefault("purchase_payment_days", None)
    values.setdefault("margin_defense_floor_rate", None)
    values.setdefault("monthly_labor_cost_krw", None)
    for key in ("purchase_payment_days", "payroll_date", "cashflow_projection_days"):
        numeric = values[key]
        if numeric is None:
            continue
        assert isinstance(numeric, Decimal)
        if numeric != numeric.to_integral_value():
            raise ValueError(f"Finance policy must be an integer: {key}")
        values[key] = int(numeric)

    return FinancePolicy(
        **values,
        policy_version=FINANCE_POLICY_VERSION,
        usage_scope=FINANCE_POLICY_USAGE_SCOPE,
        source_refs=source_refs,
    )


def build_finance_debt_policy(rows: list[dict[str, object]]) -> FinanceDebtPolicy:
    """부채 정책 행을 닫힌 키 목록으로 검증해 `FinanceDebtPolicy` 로 옮긴다."""
    values: dict[str, object] = {}
    source_refs: dict[str, str] = {}
    for row in rows:
        key = row.get("policy_key")
        if key not in _REQUIRED_DEBT_POLICY_KEYS:
            continue
        if key in values:
            raise ValueError(f"Duplicate Finance debt policy key: {key}")
        if row.get("policy_version") != FINANCE_POLICY_VERSION:
            raise ValueError(f"Finance debt policy_version mismatch: {key}")
        if row.get("usage_scope") != FINANCE_POLICY_USAGE_SCOPE:
            raise ValueError(f"Finance debt policy usage_scope mismatch: {key}")
        if row.get("evidence_grade") != "SIM_FIXED":
            raise ValueError(f"Finance debt policy must be SIM_FIXED: {key}")

        kind = row.get("value_kind")
        expected_kind = "NUMERIC" if key in _DEBT_NUMERIC_POLICY_KEYS else "TEXT"
        if kind != expected_kind:
            raise ValueError(f"Invalid value_kind for Finance debt policy {key}: {kind}")
        selected_column = "value_numeric" if kind == "NUMERIC" else "value_text"
        unused_columns = {"value_numeric", "value_text", "value_json"} - {selected_column}
        value = row.get(selected_column)
        if value is None or any(row.get(column) is not None for column in unused_columns):
            raise ValueError(f"Inconsistent value columns for Finance debt policy: {key}")
        if kind == "NUMERIC" and (isinstance(value, bool) or not isinstance(value, Decimal)):
            raise TypeError(f"Invalid Python NUMERIC value for Finance debt policy: {key}")
        if kind == "TEXT" and not isinstance(value, str):
            raise TypeError(f"Invalid Python TEXT value for Finance debt policy: {key}")
        source_ref = row.get("source_ref")
        if not isinstance(source_ref, str) or not source_ref:
            raise ValueError(f"Missing source_ref for Finance debt policy: {key}")
        values[key] = value
        source_refs[key] = source_ref

    missing = _REQUIRED_DEBT_POLICY_KEYS - values.keys()
    if missing:
        raise LookupError(
            f"Required Finance debt policies were not found: {', '.join(sorted(missing))}"
        )
    for key in ("debt_term_months", "debt_grace_months"):
        numeric = values[key]
        assert isinstance(numeric, Decimal)
        if numeric != numeric.to_integral_value():
            raise ValueError(f"Finance debt policy must be an integer: {key}")
        values[key] = int(numeric)
    return FinanceDebtPolicy(
        **values,
        policy_version=FINANCE_POLICY_VERSION,
        usage_scope=FINANCE_POLICY_USAGE_SCOPE,
        source_refs=source_refs,
    )
