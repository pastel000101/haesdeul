"""시세 **판정** — 선언 좌표 확인, 물량가중 단가의 반올림, 관측 품질(뒤처짐 · 규격 · 표기).

```text
validate_coordinates · source_table · spec_for_item · min_trade_volume_kg   선언을 읽고 확인
krw_per_kg · to_price                                                     단가 계산 · 반올림
provenance_problem · stale_quote_reason · quote_block_reason · …          이 시세를 써도 되나
```

★ 노드는 DB 를 모른다 — 관측일 · 규격 · 시장 개장일은 시세 한 줄에 실려
  오고(`readmodel/quotes.py`),
  여기서 그 값으로만 판정한다.

🟢 **자리 (2026-09-29 · 재구성 BL-016).** 전에는 `quotes.py` 한 파일에 SQL · 결과 조립과 함께
  있었다. 몸통은 그대로이고, 조립(readmodel)과 판정이 같이 쓰는 `_iso_or_none` · `_spec_label_on`
  만 공개 이름으로 올렸다. 시리즈 · 좌표에 대한 설명은 `readmodel/quotes.py` 머리말에 있다.
"""

from collections.abc import Mapping
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

#: 이 모듈이 **실제로 구현한** 좌표. 선언과 다르면 조회를 시작하지 않는다.
#:
#: 🔴 선언만 있고 아무도 안 읽는 값은 **단일 소스인 척하는 주석**이다 (Codex 교차검증
#:   2026-08-31). ``weighting`` 을 ``simple`` 로 바꿔도 계산이 그대로면, YAML 은 사실을
#:   말하는 게 아니라 사실처럼 보이는 글자다. 여기서 대조해 선언이 실행에 닿게 한다.
_IMPLEMENTED = {
    # 이 테이블 전체가 경락이다 — 가격종류 컬럼이 없어 필터로 못 쓰고, 선언으로만 남는다.
    # 그래서 더더욱 대조가 필요하다: WHSL/RTL 로 바꿔 적어도 조회가 그대로 돌아버린다.
    "price_kind": "AUC",
    # 거래대금(원) ÷ 거래중량(kg) 이므로 단위는 원/kg 하나뿐이다.
    "unit": "원/kg",
    # ``krw_per_kg`` 이 구현한 유일한 가중 방식.
    "weighting": "volume",
}


def validate_coordinates(cfg: Mapping[str, Any]) -> None:
    """선언된 좌표가 이 모듈이 구현한 것과 같은지 본다. 다르면 **조회하지 않는다.**

    조용히 무시하면 "설정을 바꿨는데 왜 그대로지"가 되고, 최악은 바꾼 줄 알고 쓰는 것이다.
    """
    wrong = {
        key: cfg.get(key) for key, value in _IMPLEMENTED.items() if cfg.get(key) != value
    }
    if wrong:
        raise ValueError(
            f"market_quotes 좌표 선언이 구현과 다르다: {wrong} — 구현값 {_IMPLEMENTED}. "
            f"선언만 바꾼다고 계산이 따라 바뀌지 않으므로 조회를 진행하지 않는다"
        )


def min_trade_volume_kg(cfg: Mapping[str, Any]) -> float:
    """등급 하나가 그날 **시세로 실리기 위한 최소 거래중량** (`#559`).

    선언에서 읽는다 (규칙 7). 없으면 조회하지 않는다 — 기본값을 코드에 두면 *"선언을
    지웠는데 왜 그대로지"* 가 되고, 그 상태는 이 모듈이 ``_IMPLEMENTED`` 로 막으려는
    것과 같은 종류다.

    ⚠️ ``0`` 은 **받는다** — 「하한 없음」이라는 확정된 값이다 (규칙 3). 미결이면 키를
    두지 않고, 그때는 여기서 멈춘다. 음수는 뜻이 없으므로 막는다.
    """
    value = cfg.get("min_trade_volume_kg")
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise KeyError(
            "market_quotes.min_trade_volume_kg 가 없거나 수가 아니다 — 얇은 거래를 "
            f"시세로 실을지는 선언이 정한다 (규칙 7). 받은 값: {value!r}"
        )
    if value < 0:
        raise ValueError(f"min_trade_volume_kg 는 음수일 수 없다: {value!r}")
    return float(value)


def source_table(cfg: Mapping[str, Any]) -> tuple[str, str]:
    """읽을 ``(schema, table)``. **환경변수가 아니라 선언에서 온다** (규칙 7).

    🔴 전에는 스키마를 ``db.get_db_schema()`` 가 ``DB_SCHEMA`` 환경변수에서 가져왔다.
      그러면 **``.env`` 가 어느 테이블을 읽을지 정한다** — 팀원마다 값이 달라 같은 코드가
      다른 시세를 보고, 그 상태는 이 저장소에서 이미 한 번 겪었다 (LLM_PROVIDER 건).
      스키마는 접속 정보가 아니라 **좌표**라서 다른 다섯과 같은 자리에 있어야 한다.

    ``haetdeul`` 사본이 아니라 ``source_raw`` 를 읽는다 — 사본은 ML 소관이 아니고
    2026-08-31 실측으로 **3일** 뒤처져 있었다(08-26 vs 08-29). rise_rate 분모가 ML 예측과
    다른 날의 시장을 보게 된다.

    두 값 다 식별자로 쿼리에 들어가므로 문자열인지 여기서 본다 — 아니면
    ``sql.Identifier`` 가 조립 시점에 죽고, 그때는 무엇이 잘못됐는지 남지 않는다.
    """
    source = cfg.get("source")
    if not isinstance(source, Mapping):
        raise KeyError(
            "market_quotes.source 가 없다 — 스키마·테이블은 선언에서 읽는다 (규칙 7)"
        )
    schema, table = source.get("schema"), source.get("table")
    if not isinstance(schema, str) or not isinstance(table, str) or not schema or not table:
        raise ValueError(
            f"market_quotes.source 는 schema·table 문자열이어야 한다: {dict(source)!r}"
        )
    return schema, table


def spec_for_item(item: str, constraints: Mapping[str, Any]) -> dict[str, Any] | None:
    """그 품목의 조회 규격. **미확정이면 None 이다 — 임의 규격으로 채우지 않는다** (규칙 3).

    ⚠️ **키가 없는 것과 값이 null 인 것을 구분한다.** 셋을 갈라야 한다::

        키가 있고 값이 spec    조회한다
        키가 있고 값이 null    **미결이라 안 읽는다** — 결정이다 (규칙 3)
        키가 없다              🔴 **계약 밖이거나 빠뜨린 것** — 여기서 멈춘다

    셋째를 조용히 넘기면 실수로 지운 품목이 «미결» 과 똑같이 보이고, 그 품목은
    그날부터 영원히 시세 없이 돈다.

    🔴 **지금 둘째(값이 null)에 해당하는 품목이 없다** (2026-09-09). 피마늘이
      그 자리였는데 계약에서 빠져(`#216` · `contracts/core.py ITEMS`) 선언에서도
      걷었다. **분기는 남긴다** — 규격 미결 품목이 다시 생기면 그날 이 길로 온다.

    규격이 반쯤 적힌 상태도 여기서 막는다. ``packages`` 만 있고 ``unit_weight_kg`` 이 없으면
    조회 시점에 ``KeyError`` 로 죽는데, 그때는 사유를 낼 자리가 이미 지나갔다.
    """
    spec_by_item = constraints["market_quotes"]["spec_by_item"]
    if item not in spec_by_item:
        raise KeyError(
            f"market_quotes.spec_by_item 에 {item!r} 항목이 없다 — "
            f"계약 품목이면 규격을 선언하고, 미결이면 키를 지우지 말고 값을 null 로 둔다. "
            f"계약 밖 품목이면 여기까지 오면 안 된다 (문 앞 게이트 #223) "
            f"(규칙 3: 빠뜨린 것과 미결은 다르다)"
        )
    spec = spec_by_item[item]
    if spec is None:
        return None
    if not isinstance(spec, Mapping):
        raise TypeError(f"spec_by_item[{item!r}] must be a mapping or null, got {spec!r}")
    return _checked_spec(item, spec)


def _checked_spec(item: str, spec: Mapping[str, Any]) -> dict[str, Any]:
    """규격 세 값이 조회에 쓸 수 있는 모양인지 확인한다."""
    packages = spec.get("packages")
    weight = spec.get("unit_weight_kg")
    label = spec.get("label")
    # 문자열을 넘기면 ``list("그물망")`` 이 글자 목록이 되어 **조용히 0건**이 된다.
    if not isinstance(packages, list) or not packages:
        raise ValueError(
            f"spec_by_item[{item!r}].packages must be a non-empty list, got {packages!r}"
        )
    if not all(isinstance(name, str) and name for name in packages):
        raise ValueError(f"spec_by_item[{item!r}].packages must hold names, got {packages!r}")
    if isinstance(weight, bool) or not isinstance(weight, int | float) or weight <= 0:
        raise ValueError(f"spec_by_item[{item!r}].unit_weight_kg must be positive, got {weight!r}")
    if not isinstance(label, str) or not label:
        # label 은 사유 문장에 그대로 실린다 — 비면 "그날 무엇을 봤는지"가 사라진다.
        raise ValueError(f"spec_by_item[{item!r}].label must be a non-empty string, got {label!r}")
    _check_before(item, spec.get("before"))
    return dict(spec)


def _check_before(item: str, before: Any) -> None:
    """규격 전환 블록(``before``)도 같은 검사를 받는다.

    ⚠️ 여기서 안 보면 ``date`` 누락은 쿼리 파라미터를 채울 때 **늦게 ``KeyError``** 로 터지고
      (그때는 사유를 낼 자리가 지나갔다), 음수 중량은 조용히 0행 → "거래 기록이 없다"는
      **틀린 사유**로 이어진다 (Codex 2차 지적).
    """
    if before is None:
        return
    if not isinstance(before, Mapping):
        raise TypeError(
            f"spec_by_item[{item!r}].before must be a mapping or absent, got {before!r}"
        )
    if not isinstance(before.get("date"), date):
        raise TypeError(
            f"spec_by_item[{item!r}].before.date must be a date, got {before.get('date')!r}"
        )
    weight = before.get("unit_weight_kg")
    if isinstance(weight, bool) or not isinstance(weight, int | float) or weight <= 0:
        raise ValueError(
            f"spec_by_item[{item!r}].before.unit_weight_kg must be positive, got {weight!r}"
        )
    label = before.get("label")
    if not isinstance(label, str) or not label:
        raise ValueError(
            f"spec_by_item[{item!r}].before.label must be a non-empty string, got {label!r}"
        )


def krw_per_kg(amount_krw: Decimal, volume_kg: Decimal) -> Decimal:
    """물량가중 단가 = 거래대금 합 ÷ 거래중량 합. **단순평균이 아니다.**

    2026-08-03 배추 특(가락, 규격 무필터) 물량가중 938.5 vs 단순평균 2,009.9 — 2.1배다.
    ``grade_unit_price`` 는 사중 일치 금액 축에 직접 걸리므로 식이 틀리면 금액이 통째로
    어긋난다.

    ``Decimal`` 로 받는 이유: 두 컬럼이 ``numeric`` 이라 psycopg 가 Decimal 을 돌려준다.
    float 로 옮기면 합계 자리에서 오차가 생기고, 그 오차가 반올림 경계를 넘길 수 있다.
    """
    if volume_kg <= 0:
        raise ValueError(f"거래중량이 {volume_kg} 이라 물량가중 단가를 낼 수 없다")
    return amount_krw / volume_kg


def to_price(unit_price: Decimal) -> int:
    """계약이 요구하는 정수 원/kg (``schemas.SourcingLine.grade_unit_price: int``).

    ⚠️ **내장 ``round()`` 를 쓰지 않는다.** 파이썬은 은행가 반올림이라 ``round(938.5)`` 가
      **938** 이다. DoD 재현치가 하필 ``.5`` 로 끝나는 값이라(938.5) 이 차이가 그대로
      드러나고, 그런 값은 실데이터에서 드물지 않다. 통상적인 금액 반올림(사사오입)으로
      고정한다.
    """
    return int(unit_price.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def iso_or_none(value: Any) -> str | None:
    """``date`` 든 문자열이든 ISO 문자열로. 없으면 None."""
    if value is None:
        return None
    return value.isoformat() if isinstance(value, date) else str(value)


def spec_label_on(spec: Mapping[str, Any], observed: str | None) -> str:
    """그날 유효했던 규격 이름. 전환일 이전이면 옛 규격 이름을 쓴다.

    라벨은 사유 문장에 그대로 실린다 — 2017년 값을 보면서 "상자·파렛트 20kg"이라고 적으면
    **무엇을 봤는지가 거짓**이 된다.
    """
    before = spec.get("before")
    if before is None or observed is None:
        return str(spec["label"])
    return str(before["label"] if observed < str(before["date"]) else spec["label"])


#: 실측 시세임을 나타내는 표시 두 개. **한 묶음이다** — 반쪽만 있으면 계약 위반이다.
#: mock 은 둘 다 없고, DB 공급자는 둘 다 싣는다.
PROVENANCE_KEYS = ("spec", "observed_at")


def provenance_problem(
    quotes: list[dict[str, Any]], as_of: str, constraints: Mapping[str, Any]
) -> str | None:
    """관측 표기가 계약대로인가. 어긋나면 **사유 문장**을 돌려준다 (예외가 아니다).

    🔴 이 검사는 **주입 경로에도 걸려야 한다.** 전에는 ``_materialize`` 안에만 있어서
      DB 경로만 지켰고, 주입 시세는 관측일이 미래여도·섞여도·아예 없어도 그대로 통과했다
      (Codex 2차 지적, 전부 재현됨).

    ⚠️ **왜 ``ports`` 가 아니라 여기인가.** 포트는 T0(``build_initial_state``)에서 도는데,
      거기서 예외를 던지면 그래프가 시작조차 못 하고 **어느 노드도 사유를 쓸 자리가 없다**
      — 결정 d 가 막으려던 그것이다. 그리고 mock 은 관측 표기가 정당하게 없어서 포트에서
      일률적으로 요구할 수도 없다. 그래서 판정은 ③·⑤ 가 공유하는 이 함수가 하고, 포트는
      구조 계약(market·grade·price)만 본다.

    검사 다섯. 앞의 것이 걸리면 뒤는 보지 않는다 — 원인을 하나로 말해야 읽는 사람이 그 자리를 본다.
    """
    marked = [q for q in quotes if any(q.get(key) for key in PROVENANCE_KEYS)]
    if not marked:
        return None  # mock — 표기가 없는 것이 정상이다

    half = [q for q in quotes if not all(q.get(key) for key in PROVENANCE_KEYS)]
    if half:
        return (
            f"시세 {len(half)}건에 관측 표기가 반쪽만 있다 (규격·관측일은 한 묶음이다) "
            f"— 관측일 없이 실측으로 분류하면 as_of 를 관측일인 것처럼 적게 된다"
        )

    dates = sorted({str(q["observed_at"]) for q in quotes})
    if len(dates) > 1:
        return (
            f"관측일이 하루가 아니다: {dates} — 등급마다 다른 날의 가격을 쓰면 "
            f"스프레드가 서로 다른 시점의 두 가격을 비교하게 된다 (규칙 4)"
        )

    try:
        observed = date.fromisoformat(dates[0])
        today = date.fromisoformat(as_of)
    except ValueError:
        # 죽지 않는다 (결정 d) — 파싱 실패도 "오늘 시세를 모른다"는 상태의 하나다.
        return f"관측일 {dates[0]!r} 을 날짜로 읽을 수 없어 언제 값인지 알 수 없다"

    if observed >= today:
        return (
            f"관측일 {dates[0]} 이 as_of 이후다 (as_of {as_of}) — 우리는 아침에 돌아서 "
            f"그 시각엔 그 값이 존재하지 않는다. look-ahead 라 쓰지 않았다"
        )

    return _lag_problem(quotes, dates[0], today, constraints["market_quotes"])


def _lag_problem(
    quotes: list[dict[str, Any]], observed: str, today: date, cfg: Mapping[str, Any]
) -> str | None:
    """뒤처짐 둘을 **갈라서** 본다 — 사유가 원인을 잘못 말하면 엉뚱한 자리를 보게 된다.

    1. **적재가 멈췄다** — 시장 전체의 최신 기록이 너무 오래됐다. 규격과 무관하다.
    2. **이 규격이 안 팔렸다** — 시장은 그 뒤로도 열렸는데 우리 규격에 거래가 없다.

    순서가 1→2 인 것은 1이 더 근본이기 때문이다. 적재가 멈춘 상태에서는 2가 **0으로 읽힌다**
    (우리 관측일과 시장 최신일이 같이 뒤로 밀린다) — 그때 2만 보면 "정상"이 되어버린다.

    ⚠️ **연휴는 어느 쪽도 아니다.** 시장이 쉰 날은 개장일이 아니라 2에서 세지 않고,
      1의 임계는 최장 연휴(2025년 6일)를 넘겨 잡아 통과시킨다. 연휴 직후 첫 아침에
      0안이 나오지 않는 것이 이 설계의 목적이다.

    시장 쪽 표기가 없으면(mock·주입) 두 검사 다 건너뛴다 — 없는 값으로 판정하지 않는다.
    """
    last_open = iso_or_none(_single_market_value(quotes, "market_last_open"))
    if last_open is None:
        return None

    stall_limit = cfg["max_calendar_days_behind"]
    stall_gap = (today - date.fromisoformat(last_open)).days
    if stall_gap > stall_limit:
        return (
            f"시장 전체의 최신 경락 기록이 {last_open}로 {stall_gap}일 전이다 "
            f"(허용 {stall_limit}일). 규격 문제가 아니라 적재가 멈췄거나 시장이 그만큼 "
            f"오래 쉰 것이라, 오늘 시세로 쓰지 않고 안을 만들지 않았다"
        )

    behind = _single_market_value(quotes, "trading_days_behind")
    if behind is None:
        return None
    behind_limit = cfg["max_trading_days_behind"]
    if behind > behind_limit:
        return (
            f"시장은 {last_open}까지 열렸는데 이 규격에서는 {observed} 이후 낙찰이 없다 "
            f"— 거래일 기준 {behind}일 (허용 {behind_limit}일). 적재가 밀린 게 아니라 "
            f"그동안 이 규격의 가격이 형성되지 않았다는 뜻이라, 오늘 시세로 쓰지 않고 "
            f"안을 만들지 않았다"
        )
    return None


def _single_market_value(quotes: list[dict[str, Any]], key: str) -> Any:
    """행마다 같아야 하는 시장 쪽 값. 갈라져 있으면 **쓰지 않는다**.

    ``0`` 이 정상값이라 ``or`` 로 접을 수 없다 (규칙 3) — ``trading_days_behind`` 의
    0은 *"안 밀렸다"* 는 확정된 사실이지 미결이 아니다.
    """
    values = {q.get(key) for q in quotes}
    if len(values) != 1:
        return None
    value = values.pop()
    return None if value is None else value


def observed_at(quotes: list[dict[str, Any]]) -> str | None:
    """받은 시세의 관측일. mock 처럼 표기가 없으면 None."""
    dates = {str(q["observed_at"]) for q in quotes if q.get("observed_at")}
    return max(dates) if dates else None


def staleness_days(quotes: list[dict[str, Any]], as_of: str) -> int | None:
    """관측일이 as_of 로부터 며칠 전인가. 관측일 표기가 없으면 None (= 재지 않는다).

    mock 은 표기가 없어 항상 None 이다 — 회귀 경로가 이 검사를 만나지 않는다.
    """
    observed = observed_at(quotes)
    if observed is None:
        return None
    return (date.fromisoformat(as_of) - date.fromisoformat(observed)).days


def stale_quote_reason(
    quotes: list[dict[str, Any]], as_of: str, constraints: Mapping[str, Any]
) -> str | None:
    """너무 오래된 시세면 사유를 돌려준다 — ``provenance_problem`` 의 staleness 부분.

    🔴 **오래된 값을 당일인 척 쓰지 않는다** (규칙 3). 다만 *"오래됐다"* 를 달력일로 세지
      않는다 — 주말·연휴가 그대로 지연으로 잡혀 **연휴 직후 첫 아침**이 반드시 0안이 되고,
      그날이 하필 가장 판단이 필요한 아침이다.

    거래일 기준으로 세면 원인이 갈린다 (``_lag_problem``). 임계 둘 다 규칙 7이다 —
    ``max_trading_days_behind`` · ``max_calendar_days_behind``.
    """
    return provenance_problem(quotes, as_of, constraints)


def observed_spec(quotes: list[dict[str, Any]]) -> str | None:
    """받은 시세가 어느 규격에서 온 것인지. mock 처럼 규격 표기가 없으면 None.

    **데이터가 말하게 한다** — 사유 문장이 여기서 규격을 가져간다.
    """
    labels = sorted({str(q["spec"]) for q in quotes if q.get("spec")})
    return " · ".join(labels) if labels else None


def quote_block_reason(
    quotes: list[dict[str, Any]],
    item: str,
    as_of: str,
    constraints: Mapping[str, Any],
) -> str | None:
    """오늘 시세를 쓸 수 없는 사유. 쓸 수 있으면 None.

    막히는 길이 둘이라 한 함수로 모은다 — ③과 ⑤가 **같은 판정**을 봐야 하기 때문이다.
    각자 판단하면 한쪽만 바뀌고, 그러면 ③은 안을 안 만들었는데 ⑤는 배분을 만드는(또는
    그 반대) 상태가 조용히 생긴다.

    1. 한 건도 못 받았다 — 휴장 · 그 규격 미거래 · 기록 판독 불가 · 규격 미확정
    2. 받았는데 뒤처졌다 — 거래일 기준 초과이거나, 시장 전체 적재가 멈췄다
    """
    market = constraints["market_quotes"]["market_category"]
    usable = [quote for quote in quotes if quote["market"] == market]
    if not usable:
        return missing_quote_reason(item, as_of, constraints)
    return provenance_problem(usable, as_of, constraints)


def missing_quote_reason(item: str, as_of: str, constraints: Mapping[str, Any]) -> str:
    """시세를 한 건도 못 받은 날의 사유. **오케스트레이터가 원인을 알 수 있어야 한다.**

    ⚠️ 이 문장은 **DB 경로에서만 나온다.** mock 은 어느 앵커·품목에서도 빈 목록을 돌려주지
      않는다(모르는 품목이면 KeyError 로 멈춘다). 그래서 여기서 규격을 이름으로 말해도
      "안 쓴 규격을 썼다고 적는" 일이 생기지 않는다 — 계약 테스트가 그 전제를 잠근다.

    🔴 **"그날 휴장"이라고 말하지 않는다.** 쿼리가 ``auction_date < as_of`` 로 **전 기간**을
      훑어 최신일을 고르므로, 빈 결과는 그날 하루의 사정이 아니라 *"as_of 이전 어느 날에도
      그 좌표로 쓸 수 있는 기록이 없다"* 는 뜻이다. 쿼리를 바꾸면서 사유를 안 바꿔
      한동안 틀린 말을 하고 있었다 (Codex 2차 지적) — 이번 수정이 없애려던 바로 그 종류다.
    """
    market = constraints["market_quotes"]["market_category"]
    spec = spec_for_item(item, constraints)
    if spec is None:
        # 조사를 붙이지 않는다 — "피마늘는"이 실제로 나갔다 (2026-08-31 관통). 품목명이
        # 받침으로 끝나는지에 따라 은/는이 갈리는데, 그걸 코드가 판정하게 만들 이유가 없다.
        return (
            f"{item} 조회 규격이 아직 정해지지 않아 등급별 경락가를 받지 못했다 "
            f"— 시세 없이 매입 수량을 정할 수 없어 안을 만들지 않았다"
        )
    # 규격 이름도 **as_of 에 맞는 것**을 고른다. 2017년 무를 18kg 로 조회해 놓고
    # "20kg 규격에서"라고 적으면 무엇을 봤는지가 거짓이 된다 (Codex 2차 지적).
    label = spec_label_on(spec, as_of)
    return (
        f"{as_of} 이전 기간에 {market} {label} 규격으로 쓸 수 있는 낙찰 기록이 없다 "
        f"— 그 좌표의 거래 자체가 없었거나, 있던 기록의 금액·중량을 읽을 수 없었다는 뜻이고, "
        f"보유 재고와는 무관하다. "
        f"시세 없이 매입 수량을 정할 수 없어 안을 만들지 않았다"
    )
