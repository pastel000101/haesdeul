"""
rules.py — 여러 파트가 같이 쓰는 판정·계산 규칙 (`core.py` 타입으로 계산한다)

타입 · 어휘와 판정·계산을 가른다. 함수가 쓰는 상수 · 예외 · 어휘는 타입 쪽(`core.py`)에
있고, 여기는 그것으로 계산하는 함수만 둔다.

    require_value                  §1.2-10   미결값(NULL)을 계산에 넣지 않는다
    gate_variant_axes              §3.5.1    허용 축 게이팅
    compute_sales_cash_priority    §7.4.1    판매 시점 현금 시급도
    check_triple_identity          §3-2      수량 · 금액 삼중 일치
    compute_has_unmet_obligation   §5.0      확정 납품 의무 미충족
    is_bankrupt                    §⑥-5      파산선
    resolve_end_code               §5.0·5.1  하루 종료 코드

타입이 스스로 하는 일 — 생성 시 검사(`__post_init__`)와 `CycleBState.in_transit` ·
`cap_by_date_overlay` 같은 파생 값 — 은 타입의 일부라 `core.py` 에 있다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.contracts.core import (
    IDENTITY_TOL_KG,
    IDENTITY_TOL_KRW,
    ITEM_CONCENTRATION_THRESHOLD,
    CycleBState,
    EndCode,
    ItemCode,
    SourcingLot,
    SplitLeg,
    UnresolvedValueError,
    VariantAxis,
)

__all__ = [
    "check_triple_identity",
    "compute_has_unmet_obligation",
    "compute_sales_cash_priority",
    "gate_variant_axes",
    "is_bankrupt",
    "require_value",
    "resolve_end_code",
]


# ---------------------------------------------------------------------------
# 미결값 — §1.2-10 (예외 `UnresolvedValueError` 는 core.py)
# ---------------------------------------------------------------------------


def require_value(name: str, value, blocker: str = ""):
    """
    §1.2-10 — 0 과 NULL 을 엄격히 구분한다.

        0    = 값이 확정되었고 그 값이 0        예) initial_debt = 0 (무차입 BASE)
        NULL = 아직 결정되지 않았다              예) purchase_payment_days (N5)

    NULL 을 0 으로 읽으면 에러 없이 손익만 달라진다.
    purchase_payment_days 를 0 으로 처리하면 D+0 즉시 지급이 되어
    운전자금이 과대 계상된다. 그래서 기본값을 주지 않고 계산 자체를 차단한다.
    값이 채워져 있으면 그대로 돌려준다 (§9.1).
    """
    if value is None:
        raise UnresolvedValueError(
            f"'{name}' 는 아직 미결(NULL)이다. 0 으로 대체하지 말 것 — "
            f"계산이 조용히 틀린다." + (f" 차단 원인: {blocker}" if blocker else "")
        )
    return value


# ---------------------------------------------------------------------------
# 허용 축 게이팅 — §3.5.1 (임계 `ITEM_CONCENTRATION_THRESHOLD` 는 core.py)
# ---------------------------------------------------------------------------


def gate_variant_axes(
    item_mix_ratio_amount: Mapping[ItemCode, float],
    split_entry_ok: bool = True,
    threshold: float = ITEM_CONCENTRATION_THRESHOLD,
) -> tuple[VariantAxis, ...]:
    """
    허용 축 게이팅 — T0 에서 한 번 계산해 전원에게 배포한다 (§3.5.1).

        quantity : 항상 허용
        timing   : 분할 진입 조건 충족 시 (총량 임계 초과 또는 지속 상승 궤적)
        mix      : item_mix_ratio 최대값 < item_concentration_threshold

    주의: timing 은 조건부다. mix 가 게이팅으로 빠진 상태에서 timing 까지 빠지면 허용
    축이 quantity 하나뿐이고, 그러면 붕괴 시 회송할 축이 없어 그날은 무조건 단일안이
    된다. → build_feedback() 이 이 경우를 감지해 회송을 생략한다 (§3.5.2 연동).
    """
    axes: list[VariantAxis] = ["quantity"]
    if split_entry_ok:
        axes.append("timing")
    if item_mix_ratio_amount and max(item_mix_ratio_amount.values()) < threshold:
        axes.append("mix")
    return tuple(axes)


# ---------------------------------------------------------------------------
# 판매 시점 현금 시급도 — §7.4.1
# ---------------------------------------------------------------------------


def compute_sales_cash_priority(state: CycleBState, base_priority: str) -> dict[str, str]:
    """
    §7.4.1 — 영업이 쓰는 것은 `sales_cash_priority` 다.

    H1 에서 대규모 선매입이 승인되면 그만큼 미래 현금이 묶이므로 판매 시점의
    현금 시급도가 T0 시점과 달라진다. 두 값을 같은 이름으로 덮어쓰지 않고
    basis 를 함께 담는다. H1 승인 매입이 없으면 두 값이 같을 수 있지만,
    그래도 basis 는 정확히 기록한다.
    """
    basis = "POST_H1_COMMITMENT" if state.commitment else "BASE_T0"
    priority = base_priority
    if state.commitment:
        fin = state.snapshot.finance
        remaining = fin.purchasable_krw - state.committed_outflow_krw
        if remaining < fin.minimum_operating_cash_krw:
            priority = "HIGH"
    out = {"cash_priority": priority, "basis": basis}
    out.update({k: v for k, v in state.provenance().items() if v})
    return out


# ---------------------------------------------------------------------------
# 삼중 일치 불변조건 (검토의견 §3-2) — 허용 오차 `IDENTITY_TOL_*` 는 core.py
# ---------------------------------------------------------------------------


def check_triple_identity(
    qty_kg: Mapping[ItemCode, float],
    split_plan: Sequence[SplitLeg],
    sourcing_plan: Sequence[SourcingLot],
    amount_krw: float | None = None,
    tol_kg: float = IDENTITY_TOL_KG,
    tol_krw: float = IDENTITY_TOL_KRW,
) -> list[str]:
    """
        수량:  total == Σ split == Σ sourcing
        금액:  total_amount == Σ(sourcing.qty × grade_unit_price)
        금액:  total_amount == Σ split == Σ sourcing (품목별)

    위반 사유 리스트를 반환한다. 빈 리스트면 통과.

    이 함수는 self_check(매입 주장값)와 Critic(DB 재조회값)이 같이 쓴다.
    계약서 §6.4 — 룰은 공유, 입력만 분리.
    Critic 은 원안이 아니라 클리핑된 값에 대해 호출해야 한다.
    원안에 대고 검사하면 클리핑되는 날마다 FAIL 이 난다 (B1).

    split 금액 변은 `SplitLeg.amount_krw` 가 실려 있을 때만 돈다. 안 실렸으면
    통째로 건너뛰고, 그것이 위반이 아니다. 실려 있으면 `Σ 회차금액 ≠ total_amount_krw`
    가 잡힌다 — 그 어긋남이 그대로 원장의 `purchases.total_amount_krw` 와
    `purchase_items.line_amount_krw` 로 가기 때문이다.
    금액 허용 오차는 따로 두지 않고 `tol_krw`(IDENTITY_TOL_KRW) 를 쓴다.
    같은 금액 축에 상수가 둘이면 왜 다른지를 아무도 모른다.
    """
    problems: list[str] = []

    for i, q in qty_kg.items():
        if split_plan:
            s = sum(leg.qty_kg.get(i, 0.0) for leg in split_plan)
            if abs(s - q) > tol_kg:
                problems.append(f"수량 항등식 위반 [{i}]: total {q:,.1f} ≠ Σsplit {s:,.1f}")
        if sourcing_plan:
            g = sum(lot.qty_kg for lot in sourcing_plan if lot.item == i)
            if abs(g - q) > tol_kg:
                problems.append(f"수량 항등식 위반 [{i}]: total {q:,.1f} ≠ Σsourcing {g:,.1f}")

    if amount_krw is not None and sourcing_plan:
        a = sum(lot.amount_krw for lot in sourcing_plan)
        if abs(a - amount_krw) > tol_krw:
            problems.append(f"금액 항등식 위반: total {amount_krw:,.0f}원 ≠ Σsourcing {a:,.0f}원")

    problems.extend(_split_amount_problems(split_plan, sourcing_plan, amount_krw, tol_krw))

    return problems


def _split_amount_problems(
    split_plan: Sequence[SplitLeg],
    sourcing_plan: Sequence[SourcingLot],
    amount_krw: float | None,
    tol_krw: float,
) -> list[str]:
    """`total ↔ split` 의 금액 변.

    수량 변은 total·split·sourcing 세 축을 다 본다. 금액도 sourcing 한 변만 보면
    `Σ 회차금액 ≠ total_amount_krw` 가 아무도 안 보는 채로 지나가므로 이 변을 따로 본다.

    금액이 한 회차도 안 실렸으면 전부 건너뛴다. 위반이 아니다.
    """
    if not split_plan:
        return []

    loaded = [leg.amount_krw for leg in split_plan if leg.amount_krw is not None]
    if not loaded:
        return []
    if len(loaded) < len(split_plan):
        # 같은 제안에서 회차마다 있고 없고는 자기모순이다. 실린 회차만 더해 총액과
        #   비교하면 출처가 섞인 값으로 판정하게 된다 — 섞지 않고 위반으로 본다.
        partial = (
            f"회차 금액이 {len(split_plan)}회차 중 {len(loaded)}회차만 실려 있다"
            " — 출처를 섞지 않는다"
        )
        return [partial]

    problems: list[str] = []

    if amount_krw is not None:
        s = sum(sum(leg.values()) for leg in loaded)
        if abs(s - amount_krw) > tol_krw:
            problems.append(f"금액 항등식 위반: total {amount_krw:,.0f}원 ≠ Σsplit {s:,.0f}원")

    if sourcing_plan:
        items = {i for leg in loaded for i in leg} | {lot.item for lot in sourcing_plan}
        for i in sorted(items):
            s = sum(leg.get(i, 0.0) for leg in loaded)
            g = sum(lot.amount_krw for lot in sourcing_plan if lot.item == i)
            if abs(s - g) > tol_krw:
                problems.append(f"금액 항등식 위반 [{i}]: Σsplit {s:,.0f}원 ≠ Σsourcing {g:,.0f}원")

    return problems


# ---------------------------------------------------------------------------
# 하루 종료 판정 (§5.0 · §5.1) — 어휘 `EndCode` 는 core.py
# ---------------------------------------------------------------------------


def compute_has_unmet_obligation(
    confirmed_obligation_kg: Mapping[ItemCode, float],
    fulfilled_kg: Mapping[ItemCode, float],
) -> bool:
    """
    산출 주체는 오케스트레이터(S3)다.

        has_unmet_obligation = (확정 납품 의무 > 0) AND (승인된 A·B로 충족 불가)

    부서는 자기 도메인 사실만 반환한다. 매입의 `no_proposal` 이 곧 E5 를 뜻하지
    않는다 — 재고에 여유가 있으면 매입 0 이어도 납품은 가능하다.
    """
    if not confirmed_obligation_kg:
        return False
    return any(
        fulfilled_kg.get(i, 0.0) < q - 1e-6 for i, q in confirmed_obligation_kg.items() if q > 0
    )


def is_bankrupt(projected_cash_min_krw: float, minimum_cash_balance_krw: float) -> bool:
    """
    파산선은 0원이 아니다 (유저플로우 §⑥-5).
    FIN-H01 이 projected_cash_min ≥ minimum_cash_balance 이므로,
    현재 정책안(1개월 급여 Reserve) 기준 12,941,280원이 판정선이다.
    0 원으로 잡으면 급여를 못 주는 상태를 정상으로 통과시킨다.
    """
    return projected_cash_min_krw < minimum_cash_balance_krw


def resolve_end_code(
    *,
    approved: bool,
    base_state_violated: bool,
    has_unmet_obligation: bool,
    both_cycles_empty: bool,
) -> EndCode:
    """
    §5.0 + §5.1 판정. E5 판정 주체는 오케스트레이터(S3)다.

    | 상황                                   | 코드 |
    |---|---|
    | 매입 0 + 판매 정상                      | E1 — 사지 않는 것도 정상 결정 |
    | 매입 정상 + 판매 0                      | E1 — 오늘 안 파는 것도 정상 결정 |
    | 매입 0 + 판매 0 + 확정 납품 의무 없음   | E2 보류 |
    | 매입 0 + 판매 0 + 확정 납품 의무 있음   | E5 |
    | BASE 상태 자체가 하드 제약 위반         | E3 (+ 백테스트 종료 검토) |

    E5 는 AI 에게 넘기지 않는다. 계약 의무를 지킬 수 없는 상황을 골라 달라고
    요청하는 것은 하드 제약의 예외를 LLM 이 만들게 하는 것과 같다.
    """
    if base_state_violated:
        return "E3_REJECTED"
    if approved:
        return "E1_APPROVED"
    if both_cycles_empty:
        return "E5_NO_FEASIBLE_PLAN" if has_unmet_obligation else "E2_HELD"
    return "E1_APPROVED"
