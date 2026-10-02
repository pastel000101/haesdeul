"""판매 추가공급 문의가 낼 경계를 계산한다 (E4-7).

판매 후보가 물건이 모자랄 때 "얼마를 언제 얼마에 댈 수 있나" 를 묻는다. 답은
안이 아니라 경계이므로 8노드 그래프를 안 돈다.

.. code-block:: text

    그래프 완주  평균 11,163ms · 최대 136,604ms   (master_agent_runs · READY 911건, 7노드 시절)
    경계만       시세·창고·재무·단가 — 전부 순수 계산

우리가 그렇게 하겠다고 답했다 (`보낸회신/마스터/260909_…가능량이_남의_값입니다.md`
  §1.4). "매입을 부르면 10초 걸린다" 로 판매 사이클 예산을 잡지 않게 하려는 것이다.

「가능량」이 우리 값이 아니다. 재료가 물류·재무 봉투다::

    procurable_quantity_kg = min( 창고 여유(물류) , 매입 가능액(재무) ÷ 단가(우리) )

  마스터가 그 둘을 실어 준다 — `app/master/readmodel/procurement_boundary.py` 로 읽어
  판매 Flow(`master/service/sales_flow.py` 의 `_supply_capacity_input`)가 싣는다. 라우팅은
  `contracts/envelope.py` 의 `CAPABILITY_ROUTING` 에서 매입 `SUPPLY_CAPACITY_QUERY` 로
  열려 있다. 그래도 재료가 비는 날이 있으므로 이 모듈은 재료가 없는 경우를 정상 경로로
  다룬다 (아래).

`0` 으로 채우지 않는다 (규칙 3). 판매 계약이 셋을 가른다::

    > 0    확보 가능량 확인
    0      확보 가능량 0kg 확인      ← 읽은 값이다
    None   미실행 · 확인 불가        ← 못 읽었다

  마스터도 같은 것을 청했다 — "`rental_cap_kg` 실측이 `0.0` 인데 그건 읽은 값이라
  `0.0` 이 맞다. 못 읽은 것과 반드시 구별되어야 한다."

한쪽만 알아도 확정하지 않는다. 창고만 알고 재무를 모르면 "적어도 이보다
  작다" 까지만 아는데, 판매는 이 수를 확보 가능량으로 읽어 후보를 살린다.
  못 지킬 수를 내느니 `None` 과 사유를 낸다 — 그것이 `risks` 가 필수인 이유다.

회신 설명문 · 근거(`supply_capacity_reasoning` · `supply_capacity_evidences`)도 여기 둔다
— 무엇이 상한을 정했는지 말하는 규칙이라 계산과 같은 자리다. 시세를 읽는 순서는
`service/supply_capacity.py`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from app.contracts.core import Evidence

#: 무엇이 상한을 정했나. 사람이 그 답을 받고 무엇을 할지가 갈린다 — 우리가
#: 회신 계약에 더해 달라고 청한 칸이다 (§4).
#:
#: .. code-block:: text
#:
#:     warehouse   창고를 더 빌리면 풀린다
#:     finance     돈 문제다. 창고를 빌려도 안 풀린다
#:     unknown     못 물어봤다 — 아직 아무것도 모른다
Basis = Literal["warehouse", "finance", "unknown"]

#: 회신에 실리는 사유. 화면과 Critic 이 읽는다 — 내부 단계 이름을 쓰지 않는다.
_NO_QUOTE = "그날 시세를 읽지 못해 단가를 정하지 못했다 — 댈 수 있는 양을 계산할 수 없다"
_NO_WAREHOUSE = "창고 여유를 받지 못했다 — 그쪽이 더 좁으면 이보다 적어진다"
_NO_FINANCE = "매입 가능액을 받지 못했다 — 그쪽이 더 좁으면 이보다 적어진다"
_BOTH_MISSING = "창고 여유와 매입 가능액을 둘 다 받지 못했다 — 댈 수 있는 양을 아직 모른다"
_TIE = "창고 여유와 매입 가능액이 같은 양에서 막는다 — 한쪽만 풀어도 늘지 않는다"


@dataclass(frozen=True)
class SupplyCapacity:
    """판매에 낼 한 품목분 경계.

    ``procurable_quantity_kg`` 가 ``None`` 인데 ``risks`` 가 비면 안 된다 — 그러면
    판매 쪽에서 "확인 안 함" 이 "위험 없음" 이 된다. 그 불변조건을
    ``__post_init__`` 이 잠근다.
    """

    procurable_quantity_kg: int | None
    expected_unit_price_krw: int | None
    unit_price_grade: str | None
    basis: Basis
    risks: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.procurable_quantity_kg is None and not self.risks:
            raise ValueError(
                "가능량이 None 인데 사유가 없다 — 판매가 「위험 없음」으로 읽는다."
            )
        if self.basis == "unknown" and self.procurable_quantity_kg is not None:
            raise ValueError(
                f"무엇이 막았는지 모르는데 수량({self.procurable_quantity_kg})이 있다."
            )


def pick_unit_price(
    quotes: Sequence[Mapping[str, Any]], constraints: Mapping[str, Any]
) -> tuple[int, str] | None:
    """그날 단가와 그 값이 나온 등급 이름. 시세가 없으면 ``None``.

    등급 이름이 아니라 값으로 고른다. 관통 216일 실측에서 「특」이 최고가가
      아닌 날이 331일 중 44일(13.3%)이었다 — 배추는 7일 중 5일이 그랬다. 이름으로
      박으면 그날 최대 97.8% 를 더 부르고, "어떤 등급을 섞어 사도 지켜진다" 는
      약속이 깨진다.

    그래서 등급 이름을 회신에 같이 싣는다. 값 기준으로 골랐다는 사실이
      숫자만 봐서는 안 보이기 때문이다.

    기준은 ``constraints`` 가 갖는다 (규칙 7) — 코드에 안 박는다.
    """
    basis = constraints["supply_capacity"]["unit_price_basis"]
    if basis != "highest_grade":
        raise ValueError(
            f"모르는 단가 기준: {basis!r}. 새 기준을 넣으려면 여기도 같이 연다."
        )
    usable = [q for q in quotes if isinstance(q.get("price"), int) and q["price"] > 0]
    if not usable:
        return None
    top = max(usable, key=lambda q: q["price"])
    return int(top["price"]), str(top["grade"])


def compute_supply_capacity(
    *,
    quotes: Sequence[Mapping[str, Any]],
    warehouse_free_kg: float | None,
    finance_cap_amount_krw: int | None,
    constraints: Mapping[str, Any],
) -> SupplyCapacity:
    """경계 하나를 낸다. 아무것도 안 만들고 안 쓴다 (규칙 2).

    ``warehouse_free_kg`` · ``finance_cap_amount_krw`` 는 마스터가 실어 주는 남의
    값이다. ``None`` 은 "못 읽었다" 이고 ``0`` 은 "자리가 없다" 라 다르게 답한다.
    """
    priced = pick_unit_price(quotes, constraints)
    if priced is None:
        # 단가가 없으면 재무 상한을 수량으로 못 바꾼다 — 창고를 알아도 답이 반쪽이다.
        return SupplyCapacity(None, None, None, "unknown", (_NO_QUOTE,))
    unit_price, grade = priced

    risks: list[str] = []
    if warehouse_free_kg is None and finance_cap_amount_krw is None:
        risks.append(_BOTH_MISSING)
        return SupplyCapacity(None, unit_price, grade, "unknown", tuple(risks))
    if warehouse_free_kg is None:
        risks.append(_NO_WAREHOUSE)
    if finance_cap_amount_krw is None:
        risks.append(_NO_FINANCE)
    if risks:
        # 한쪽만 알면 «적어도 이보다 작다» 까지다. 판매는 이 수를 확보 가능량으로
        # 읽으므로, 못 지킬 수를 내느니 사유를 낸다.
        return SupplyCapacity(None, unit_price, grade, "unknown", tuple(risks))

    assert warehouse_free_kg is not None and finance_cap_amount_krw is not None
    warehouse_kg = int(warehouse_free_kg)
    # 단가는 위에서 한 번 구한 것을 그대로 쓴다. 나눗셈에 쓴 수와 회신에 싣는
    # 수가 갈리면 안 된다 (마스터 조건 — 최대 31.9% 갈린다).
    finance_kg = finance_cap_amount_krw // unit_price

    if warehouse_kg < finance_kg:
        return SupplyCapacity(warehouse_kg, unit_price, grade, "warehouse", ())
    if warehouse_kg == finance_kg:
        # 동률은 «둘 다 막는다» 이다. `finance` 로 적되 한쪽만 풀어도 안 늘어난다는
        # 것을 말해 준다 — 창고만 빌리고 늘기를 기다리는 일이 없게.
        return SupplyCapacity(finance_kg, unit_price, grade, "finance", (_TIE,))
    return SupplyCapacity(finance_kg, unit_price, grade, "finance", ())


def supply_capacity_reasoning(item: str, capacity: SupplyCapacity) -> str:
    """수량·단가를 문장에 안 적는다. 봉투가 설명문의 숫자를 막는다
    (`E-REASONING-NUMERIC` — "숫자가 필요하면 Evidence 를 추가한다"). 값은
    payload 와 Evidence 에 있고, 여기서는 무엇이 상한을 정했는지만 말한다.
    """
    if capacity.procurable_quantity_kg is None:
        return f"{item} 은 지금 받은 것만으로는 댈 수 있는 양을 정할 수 없다."
    what = {"warehouse": "창고 여유", "finance": "매입 가능액", "unknown": "알 수 없음"}
    return f"{item} 은 {what[capacity.basis]}이 정하는 양까지 댈 수 있다."


def supply_capacity_evidences(
    body: Mapping[str, Any], capacity: SupplyCapacity
) -> tuple[Evidence, ...]:
    """봉투가 근거를 요구하는 최상위 값에 하나씩 단다.

    `risks` 에 다는 근거는 개수뿐이다. `Evidence.value` 가 `float` 라 문장에 붙일 수
      있는 것이 개수뿐이고, 그것은 근거가 아니라 세어 본 것이다. `risks` 는 봉투 메타
      (`contracts/envelope.py` 의 `ENVELOPE_META_KEYS`)라 근거 의무가 없다 — 물류
      `soft_warnings` 와 같은 이유다("만족시킬 수 없는 검사는 기준이 아니라 결함이다").
      개수 근거(`SUPPLY-CAP-RISKS`)는 그래도 단다.
    """
    out = [
        Evidence(
            claim="risks",
            source="tool_calc",
            ref_ids=("SUPPLY-CAP-RISKS",),
            value=float(len(body["risks"])),
            unit="건",
            evidence_grade="MEASURED",
        )
    ]
    if capacity.expected_unit_price_krw is not None:
        out.append(
            Evidence(
                claim="expected_unit_price_krw",
                source="tool_calc",
                ref_ids=("AUCTION-QUOTE",),
                value=float(capacity.expected_unit_price_krw),
                unit="원/kg",
                evidence_grade="MEASURED",
                evidence_detail=f"그날 최고가 등급 「{capacity.unit_price_grade}」",
            )
        )
    if capacity.procurable_quantity_kg is not None:
        out.append(
            Evidence(
                claim="procurable_quantity_kg",
                source="tool_calc",
                ref_ids=("SUPPLY-CAP-MIN",),
                value=float(capacity.procurable_quantity_kg),
                unit="kg",
                evidence_grade="MEASURED",
                evidence_detail=f"{capacity.basis} 가 상한을 정했다",
            )
        )
    if "requested_quantity_kg" in body:
        out.append(
            Evidence(
                claim="requested_quantity_kg",
                source="sales",
                ref_ids=("SALES-SHORTFALL",),
                value=float(body["requested_quantity_kg"]),
                unit="kg",
                evidence_grade="MEASURED",
            )
        )
    return tuple(out)
