"""마스터가 보낸 payload 를 **읽는 규칙** — 필수 입력 검사, 쓰면 안 되는 예측, 재고 거르기.

```text
validate_payload · validate_forecast   없는 것 · 모양이 다른 것의 이름 목록 (missing_data)
not_ready_reason                        그 목록을 사람이 읽는 사유로 (셋을 가른다)
absorb_inventory · approved_commitments  payload → State 로 옮길 때 거르는 규칙
```

★ **값을 만들지 않는다** — 없는 것은 이름으로 돌려주고, 모양 검사와 옮기기만 한다 (규칙 3).

🟢 **자리 (2026-09-29 · 재구성 BL-016).** 전에는 `adapter.py` 안에 있었다. 함수 몸통은 그대로이고,
  어댑터(회신 번역)와 `service/scenarios.py`(State 만들기)가 함께 부른다. 비공개였던
  `_approved_commitments` · `_not_ready_reason` 만 공개 이름으로 올렸다.

🟢 **부서 선언(`constraints.yaml`)을 여기서 읽지 않는다 (2026-09-29 · BL-016 보완).** 검사가
  보는 임계 · 판정일 · 커버 창은 부르는 쪽(어댑터 — 요청마다 한 번)이 `load_constraints()` 로
  읽어 `constraints` 로 넘긴다. 이 모듈은 넘겨받은 dict 만 본다.
"""

from collections.abc import Mapping
from datetime import date, timedelta
from decimal import Decimal
from math import isfinite
from typing import Any

from app.purchase_agent.config import (
    ThresholdNotDeclared,
    ci_width_threshold,
    threshold_missing_data_name,
    threshold_not_declared_reason,
)
from app.purchase_agent.domain.classify_situation import is_gate_excluded

# ── 수신 검증 ─────────────────────────────────────────────────────────────


def validate_forecast(
    forecast: Any, as_of: date, constraints: Mapping[str, Any]
) -> list[str]:
    """받은 예측이 **as_of 이전에 만들어진 것인가**, **날짜 축이 맞는가**.

    마스터가 이미 한 겹 건다 — ``generated_at > as_of``면 아예 싣지 않는다. 그런데
    **시점 필드가 없으면 판단하지 않고 그대로 싣는다**(필요데이터 §1.3-②). 그 구멍이
    여기서 막힌다. 누수는 에러를 내지 않고 손익만 좋아지므로 양쪽에서 본다.

    ``daily`` 축을 보는 이유: 판정 기준일이 **D+14 = ``daily[13]``**이라(``ci_judgment_day``)
    축이 하루만 밀려도 **다른 날을 보게 된다.** 에러가 나지 않아 아무도 모른다.

    돌려주는 것은 ``missing_data``에 실을 이름 목록이다 — 비어 있으면 통과.
    """
    if not isinstance(forecast, Mapping):
        return ["forecast"]

    missing: list[str] = []
    generated_at = forecast.get("generated_at")
    if not isinstance(generated_at, str) or not generated_at.strip():
        missing.append("forecast.generated_at")
    elif generated_at[:10] > as_of.isoformat():
        # 마스터가 걸렀어야 하는 값이 왔다 — 통과시키면 look-ahead가 성립한다.
        missing.append("forecast.generated_at")

    # 그래프가 실제로 꺼내는 최상위 키. 없으면 노드 안에서 KeyError가 난다.
    for key in ("current_price", "horizon_days", "model_version"):
        if forecast.get(key) is None:
            missing.append(f"forecast.{key}")

    daily = forecast.get("daily")
    horizon = forecast.get("horizon_days")
    if not isinstance(daily, list) or not daily:
        missing.append("forecast.daily")
        return sorted(set(missing))

    if isinstance(horizon, int) and len(daily) != horizon:
        missing.append("forecast.daily")
    # **축 전체를 본다.** 처음엔 ``daily[0]``만 D+1인지 봤는데, 첫 행만 맞춰 두고 이후를
    # 하루씩 밀면 그대로 통과했다 (Codex 교차검증 P1). 판정 기준일을 **배열 인덱스**로
    # 고르므로(``classify_situation.judgment_row``) 중간부터 밀리면 D+15를 D+14로
    # 착각한 채 조용히 돈다 — 에러가 나지 않는 look-ahead다.
    for index, row in enumerate(daily):
        if not isinstance(row, Mapping):
            missing.append("forecast.daily")
            break
        if row.get("date") != (as_of + timedelta(days=index + 1)).isoformat():
            missing.append("forecast.daily")
            break
        if any(row.get(key) is None for key in ("predicted", "lower", "upper")):
            missing.append("forecast.daily")
            break

    if "forecast.daily" not in missing:
        missing.extend(_unusable_forecast_names(forecast, daily, constraints))
    return sorted(set(missing))


#: 값이 **왔는데 쓰지 말라고 표시된** 입력. ``missing_data`` 에 이 이름만 실리면
#: 사유 문장이 *"없다"* 가 아니라 *"쓰지 말라고 왔다"* 가 된다 (``_generate_scenarios``).
UNUSABLE_FORECAST_NAMES: tuple[str, ...] = (
    "forecast.use_recommended",
    "forecast.daily.gate_reason",
)


def _unusable_forecast_names(
    forecast: Mapping[str, Any], daily: list[Any], constraints: Mapping[str, Any]
) -> list[str]:
    """**왔는데 쓰면 안 되는** 예측인가 (#213 · ML 회신 2026-08-27).

    ``missing_data`` 는 원래 *"안 왔다"* 를 담는 칸인데 여기 둔다. **같은 함수에 선례가
    있다** — ``generated_at > as_of`` 도 값이 왔지만 쓰면 look-ahead 라 ``missing`` 에
    넣는다. *"쓸 수 없는 입력"* 이라는 점이 같고, 봉투가 ``RUNTIME_NOT_READY`` 에
    요구하는 것은 **이름**이지 부재의 증명이 아니다.

    ⚠️ **노드에서 예외를 던지지 않는 이유.** 노드가 죽으면 ``missing_data`` 가 비어
      마스터는 *"매입이 왜 안 돌았는지"* 를 못 받는다. 봉투도 빈 ``missing_data`` 의
      ``RUNTIME_NOT_READY`` 를 거부한다 (``ContractViolation``).

    거는 것은 둘이다::

        use_recommended is False    ML: "FALSE 면 쓰지 마세요" — 조합 전체가 무효
        판정일 행이 quality 게이트   ci_width 를 그 행 하나로 재므로 판정이 성립 안 한다
        가장 짧은 커버 구간이 전부   max_price 를 정할 행이 남지 않는다 (규칙 5 재무 상한)
          quality 게이트

    ★ **세 번째가 가장 짧은 창인 이유**: 큰 창은 짧은 창을 포함하므로, 짧은 창에 쓸
      행이 하나라도 있으면 ``usable_forecast_window`` 는 어느 안에서도 안 빈다.

    🔴 **``None`` 은 안 건다** (규칙 3). mock 예측에는 이 칸들이 아예 없고,
      *"권고가 없다"* 와 *"쓰지 말라고 했다"* 는 다른 사실이다. ``is False`` 로 본다.
    """
    names: list[str] = []
    if forecast.get("use_recommended") is False:
        names.append("forecast.use_recommended")

    day = constraints["situation"]["ci_judgment_day"]
    if len(daily) >= day and is_gate_excluded(daily[day - 1]):
        names.append("forecast.daily.gate_reason")

    shortest = min(constraints["coverage_days"]["by_label"].values())
    window = daily[:shortest]
    if window and all(is_gate_excluded(row) for row in window):
        names.append("forecast.daily.gate_reason")
    return names


#: 물류가 로트마다 싣는 키 (`master/adapters/logistics.py` · 2026-08-28 실측).
#: **이름을 바꾸지 않는다** — 물류가 alias 를 만들지 않기로 했고(#78 §6), 매입이
#: 물류 어휘를 그대로 읽기로 합의했다(#76). 매핑 표를 두면 어긋날 자리가 하나 더 생긴다.
LOT_REQUIRED_KEYS = ("lot_id", "available_qty_kg")

#: 로트가 어느 품목 것인지 밝히는 키. 없으면 품목을 가려낼 수 없다.
LOT_ITEM_KEY = "item"


def _lot_shape_problems(lots: Any) -> list[str]:
    """``lots``가 **있을 때** 모양을 본다. 없는 것과 모양이 다른 것은 다르다.

    ``lots``는 여전히 **선택 항목**이다 — 빠지면 등급 배분이 단일 등급으로 내려갈 뿐
    돌아간다(M-1 제출 §5). 그래서 부재는 잡지 않는다.

    ⚠️ **막으려는 것은 "있는데 모양이 다른" 경우다.** 그 구멍으로 실연동이
    ``KeyError: 'remaining_kg'`` 로 죽었는데(2026-08-28), 전 스위트는 green 이었다 —
    선택 항목이라 검사 자체가 없었고 노드 안에서야 터졌다. 어댑터에서 잡으면
    마스터가 ``missing_data`` 로 **무엇이 어긋났는지** 받는다 (#76).
    """
    if lots is None:
        return []
    if not isinstance(lots, list):
        return ["constraints.inventory.lots"]
    problems: list[str] = []
    for index, lot in enumerate(lots):
        if not isinstance(lot, Mapping):
            problems.append(f"constraints.inventory.lots[{index}]")
            continue
        # 값이 ``None``인 것은 통과시킨다 — 물류가 "모른다"를 그렇게 표현한다(§1.2-10).
        # 여기서 막는 것은 **키 자체가 없는** 경우다.
        problems.extend(
            f"constraints.inventory.lots[{index}].{key}"
            for key in LOT_REQUIRED_KEYS
            if key not in lot
        )
    return sorted(set(problems))


def validate_payload(
    payload: Mapping[str, Any], as_of: date, constraints: Mapping[str, Any]
) -> list[str]:
    """필수 4키와 그 하위 계약. 없는 것의 **이름**을 돌려준다.

    ``missing_data``가 비면 ``RUNTIME_NOT_READY``를 낼 수 없다(봉투가 ``ContractViolation``).
    무엇이 없는지 이름이 있어야 마스터가 사용자에게 요청할 수 있기 때문이다.

    ⚠️ **조언자가 ``READY``를 못 내면 키 자체가 없다** — 빈 dict가 아니다
    (필요데이터 §1.3-①). 그래서 ``in`` 으로 존재를 먼저 본다.

    ``constraints`` 는 부서 선언(``constraints.yaml``)이다 — payload 안의 부서 제약
    (``payload["constraints"]`` · 아래 ``dept_constraints``)과 다른 것이다.
    """
    missing: list[str] = []
    item = payload.get("item")
    if not item:
        missing.append("item")
    else:
        # 🔴 **임계가 품목별이라 품목을 알아야 볼 수 있다.** 그래서 ``item`` 검사 다음이다.
        #   여기서 안 막으면 ①이 ``ThresholdNotDeclared`` 로 죽는데, **노드가 죽으면
        #   ``missing_data`` 가 비어** 마스터는 *"매입이 왜 안 돌았는지"* 를 못 받는다
        #   (봉투가 빈 ``missing_data`` 의 ``RUNTIME_NOT_READY`` 를 거부한다).
        #   ``_unusable_forecast_names`` 를 노드가 아니라 여기 둔 것과 같은 이유다.
        #
        # ⚠️ **다른 ``missing`` 과 성격이 다르다** — 마스터가 다시 보낸다고 풀리지 않는다.
        #   그 사실은 이름이 말한다 (``ThresholdNotDeclared.missing_data_name``).
        try:
            ci_width_threshold(item, constraints)
        except ThresholdNotDeclared as undeclared:
            missing.append(undeclared.missing_data_name)

    dept_constraints = payload.get("constraints")
    if not isinstance(dept_constraints, Mapping):
        missing.append("constraints")
    else:
        for dept in ("finance", "inventory"):
            if not isinstance(dept_constraints.get(dept), Mapping):
                missing.append(f"constraints.{dept}")
        # **컨테이너 모양만 보면 안 된다.** ``constraints.finance``가 dict이기만 하면
        # 통과시켰더니, 안이 비어 있을 때 ``build_state``가 ``KeyError``로 죽었다.
        # 죽으면 *"무엇이 없는지"*가 ``missing_data``에 남지 않아 마스터가 사용자에게
        # 요청할 대상을 모른다 — 계약이 막으려는 상태가 그대로 된다.
        # **여기서 꺼내 쓰는 키만** 적는다. 목록이 실제 참조보다 길면 안 쓰는 값을
        # 요구하게 되고, 짧으면 다시 KeyError가 난다.
        finance = dept_constraints.get("finance")
        if isinstance(finance, Mapping):
            # ⚠️ **``margin_defense_floor_rate``는 넣지 않는다.** 재무가 ``READY``인 채로
            # null을 줄 수 있고(Codex 교차검증 P1), 어느 노드도 그 값을 쓰지 않는다 —
            # 참조값으로 실려만 간다. 필수로 걸면 정상 요청이 어댑터에서 막힌다.
            #
            # ``finance_cap_amount_krw``는 **필수다.** 없으면 ``purchase_budget_krw``가
            # mock 폴백(60% 비율)을 타는데, 어댑터 경로에서 그 길로 가면 B6("같은 목적
            # 60% 재적용 금지")가 조용히 되살아난다. 실운영에서 재무 경계 미수신은
            # 애초에 ``RUNTIME_NOT_READY``이므로(M-1 제출 §4) 필수로 두는 것이 맞다.
            for key in ("base_projected_cash_min", "finance_cap_amount_krw"):
                if finance.get(key) is None:
                    missing.append(f"constraints.finance.{key}")

        # 물류도 같다. **필드명이 아직 미확정이라**(물류 미제출 — 필요데이터 §1.3-①)
        # 이름이 어긋난 payload가 실제로 올 수 있는데, 그때 ``warehouse_cap_kg``가
        # ``KeyError``로 죽으면 마스터는 *"물류 이름이 다르다"*를 알 길이 없다.
        # ``lots``는 빠져도 돌아간다 — 등급 배분이 단일 등급으로 내려갈 뿐이라
        # 필수가 아니다 (M-1 제출 §5).
        inventory = dept_constraints.get("inventory")
        if isinstance(inventory, Mapping):
            missing.extend(_capacity_input_problems(inventory))
            missing.extend(_lot_shape_problems(inventory.get("lots")))
            missing.extend(_arrival_input_problems(inventory))

    if "forecast" not in payload:
        # 마스터가 오염 판정으로 싣지 않은 경우가 여기다 (필요데이터 §1.3-②).
        missing.append("forecast")
    else:
        missing.extend(validate_forecast(payload["forecast"], as_of, constraints))

    orders = payload.get("confirmed_orders")
    if not isinstance(orders, Mapping):
        missing.append("confirmed_orders")
    else:
        # ③이 ``total_kg``으로 일평균 수요를, ⑤가 ``orders[]``로 납품일 매칭을 한다.
        if orders.get("total_kg") is None:
            missing.append("confirmed_orders.total_kg")
        if not isinstance(orders.get("orders"), list):
            missing.append("confirmed_orders.orders")

    policy = payload.get("policy_values")
    if not isinstance(policy, Mapping):
        missing.append("policy_values")
    elif not policy.get("item_mix_ratio") or not isinstance(policy["item_mix_ratio"], Mapping):
        # 스칼라로 오면 mix 게이팅의 max()가 성립하지 않는다 (답변 §4-4).
        # **빈 dict도 거부한다.** 통과시키면 근거가 관측된 적 없는 최대비를 ``0.0``으로
        # 적고 "0.000 < 0.7 → mix 제외"라는 **스스로 모순된 문장**을 낸다 — 미결을 0으로
        # 채우지 않는다는 규칙 3 위반이다 (Codex 교차검증 P1).
        missing.append("policy_values.item_mix_ratio")
    # ⚠️ ``contract_price_krw``는 **필수가 아니다.** 미수령이면 ``None``이고, 그때
    # ``margin_warning``·``expected_margin_rate``가 함께 null로 나가는 것이 계약이다
    # (state.py · IO명세 §2 동기화 규칙). 필수로 걸면 정상 경로가 막힌다
    # (Codex 교차검증 P1).
    return sorted(set(missing))


def _capacity_input_problems(inventory: Mapping[str, Any]) -> list[str]:
    """창고 상한 입력의 **부재와 모양을 함께** 본다.

    부재만 보면 ``warehouse_cap_kg``가 값을 받고도 죽거나 조용히 틀린다. 실측:

        True          → 창고 상한 1kg. 전 안이 창고에 눌려 죽는데 사유가 안 남는다
        '1000' · [1]  → 더하는 자리에서 ``TypeError``. 노드가 죽으면 **사유를 못 낸다**
        -500          → 상한이 음수. 수량이 음수로 클립된다

    죽으면 마스터는 *"무엇을 다시 달라고 해야 하는지"*를 모른다 — ``RUNTIME_NOT_READY``에
    ``missing_data``가 있어야 요청이 성립한다. 로트 ``shelf_life_days``·
    ``inbound_lead_days``와 **같은 종류의 값이라 같은 자리에서 막는다**.

    ``0``은 통과시킨다. ``rental_cap_kg``는 2026-08-27 물류 회신 §1로 **0 확정**이라
    미결이 아니다 (규칙 3).
    """
    problems: list[str] = []
    for key in ("warehouse_free_kg", "rental_cap_kg"):
        base = f"constraints.inventory.{key}"
        value = inventory.get(key)
        if value is None:
            problems.append(base)
        elif isinstance(value, bool) or not isinstance(value, int | float | Decimal):
            problems.append(f"{base}@수량이어야 한다")
        elif not isfinite(float(value)):  # NaN · ±Inf
            problems.append(f"{base}@유한한 수여야 한다")
        elif float(value) < 0:
            problems.append(f"{base}@음수일 수 없다 (받은 값 {value})")
    return problems


def _arrival_input_problems(inventory: Mapping[str, Any]) -> list[str]:
    """도착일 계산 입력의 **모양**을 본다. 부재는 잡지 않는다 — 둘 다 선택 필드다.

    ⚠️ ``inbound_lead_days``를 정수로 강제하는 이유: 2.5가 오면 도착일은
    ``date + timedelta(days=2.5)``에서 **2일로 잘리는데** ⑤의 소진 창 계산은 2.5를
    그대로 쓴다. 두 계산이 다른 리드타임을 보게 되고, 결과는 멀쩡해 보인다.
    조용히 반올림하지 않고 여기서 세운다 — 계약이 "일" 단위이기 때문이다.

    ``bool``을 따로 막는 것은 ``True``가 ``1일``로 통과하기 때문이다
    (``schemas/proposal.py``의 ``_reject_boolean``과 같은 이유).
    """
    problems: list[str] = []
    lead = inventory.get("inbound_lead_days")
    if lead is not None:
        base = "constraints.inventory.inbound_lead_days"
        if isinstance(lead, bool) or not isinstance(lead, int | float):
            problems.append(f"{base}@정수여야 한다")
        elif lead != int(lead):
            problems.append(f"{base}@일 단위 정수여야 한다 (받은 값 {lead})")
        elif lead < 0:
            problems.append(f"{base}@음수일 수 없다 (받은 값 {lead})")

    cap = inventory.get("cap_by_date")
    if cap is not None:
        base = "constraints.inventory.cap_by_date"
        if not isinstance(cap, Mapping):
            problems.append(f"{base}@날짜→수용량 매핑이어야 한다")
        else:
            for day, value in cap.items():
                if not isinstance(day, str):
                    # 날짜 객체로 오면 ISO 문자열 조회가 **전부 미스**가 되고,
                    # 값이 와 있는데도 "받지 못했다"로 고지된다.
                    problems.append(f"{base}@키가 ISO 날짜 문자열이어야 한다")
                    break
            for day, value in cap.items():
                if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
                    problems.append(f"{base}[{day}]@수량이어야 한다")
                    break
                if not isfinite(value):  # NaN · ±Inf
                    problems.append(f"{base}[{day}]@유한한 수여야 한다")
                    break
    return problems


# ── 재고 흡수 ─────────────────────────────────────────────────────────────

def absorb_inventory(inventory: Mapping[str, Any], item: str) -> dict[str, Any]:
    """물류 payload 의 재고를 **이 품목 것만** 남겨 넘긴다.

    ★ **품목 필터가 핵심이다.** 물류는 4품목 로트를 한 목록에 담아 보내는데
      (`LOT-…-BAECHU` · `-MU` · `-PIMANUL` · `-YANGPA`, 2026-08-28 실측),
      매입은 품목 하나씩 돈다. 거르지 않고 ``lots[0]`` 을 집으면 **다른 품목의
      로트를 근거로 삼는다** — 에러가 나지 않아 아무도 모른다. mock 은 품목별로
      나뉘어 있어 이 구멍이 보이지 않던 자리다.

    ★ ``item`` 키가 없는 로트는 **버리지 않고 남긴다.** 품목 축을 못 밝힌 것과
      "다른 품목"은 다르다 — 버리면 있는 재고를 없는 것으로 만든다. 판단은
      노드가 하고, 여기서는 가려낼 수 있는 것만 가려낸다.

    ★ **값을 만들지 않는다.** 없는 키를 기본값으로 채우면 미결이 사실이 된다
      (규칙 3). 모양 검사는 `validate_payload` 가 하고, 여기서는 옮기기만 한다.
    """
    out = dict(inventory)
    lots = inventory.get("lots")
    if not isinstance(lots, list):
        return out
    out["lots"] = [
        dict(lot)
        for lot in lots
        if isinstance(lot, Mapping) and lot.get(LOT_ITEM_KEY, item) == item
    ]
    return out


def approved_commitments(payload: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    """어제까지 승인된 약정을 State 로 옮긴다. **안 온 것과 0건을 구별한다** (규칙 3).

    ```text
    키 없음    → None   "마스터가 안 보냈다"
    []         → []     "보냈는데 어제 승인이 없었다"
    [{...}]    → 그대로  온 그대로 나른다
    ```

    ⚠️ **마스터는 지금 ``[]`` 를 보내지 않는다** — `flow.py._commitments_block` 이
      ``if not self.approved_commitments: return None`` 으로 칸 자체를 안 만든다.
      그래도 두 갈래를 다 두는 이유는, 그 규칙이 바뀌는 날 **여기가 조용히 틀리지
      않게** 하기 위해서다. 빈 배열을 ``None`` 으로 접으면 *"승인이 없었다"* 가
      *"안 왔다"* 로 둔갑한다.

    ★ **모양을 검사하지 않는다.** 이 값은 `missing_data` 대상이 아니다 — 없어도
      안이 만들어지고, 있으면 근거 문장 하나가 넓어질 뿐이다. 필수로 걸면 마스터가
      승인 이력 없이 부르는 첫날(어제가 없는 날)이 통째로 `RUNTIME_NOT_READY` 가
      된다.
    """
    raw = payload.get("approved_commitments")
    if raw is None:
        return None
    return [dict(item) for item in raw]


def not_ready_reason(missing: list[str], item: Any) -> str:
    """``RUNTIME_NOT_READY`` 의 **사람이 읽는 사유.** 이름 목록으로는 못 가르는 것을 가른다.

    ``missing_data`` 는 어느 쪽이든 이름을 싣지만, 사유가 같으면 **마스터가 누구에게
    무엇을 요청해야 하는지** 가 사라진다. 셋이 서로 다르다::

        임계 미선언   우리가 값을 정해야 풀린다      다시 보내도 그대로다
        쓰지 말라고 왔다  ML 이 표시한 것             예측을 다시 내야 풀린다
        그 밖          마스터가 값을 안 보냈다        다시 보내면 풀린다

    ★ **각각 "그것만이 사유일 때"만 말한다.** 임계도 없고 예측도 없으면 임계 문장만
      내보내는 것이 거짓이 된다 — 그때는 일반 문장으로 내리고, 무엇이 없는지는
      ``missing_data`` 가 전부 싣는다. 앞의 둘이 같은 모양(``len(...) == len(missing)``)인
      것은 우연이 아니라 같은 규칙이다.
    """
    if item and missing == [threshold_missing_data_name(item)]:
        return threshold_not_declared_reason(item)
    unusable = [name for name in missing if name in UNUSABLE_FORECAST_NAMES]
    if unusable and len(unusable) == len(missing):
        return "예측을 만든 쪽이 오늘 값을 쓰지 말라고 표시해 시나리오를 만들지 않았다."
    return "필수 입력이 없어 시나리오를 만들지 못했다."
