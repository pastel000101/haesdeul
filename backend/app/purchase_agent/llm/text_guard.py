"""LLM 문장에 숫자·제어문자가 있나 — 여러 역할이 같이 쓰는 public 검사.

⑤ 등급 조합(``runtime``) · ④ 회차 배분 · ⑧ 근거 자기 검토가 같은 판정을 쓴다. 한 역할이
다른 모듈의 밑줄 이름을 가져다 쓰면 private 을 빌려 쓰는 의존이 생기고, 그 모듈을 고칠 때
누가 그 안을 들여다보고 있는지 알 수 없게 된다. 그래서 공개 이름으로 두고 모두 같은
함수를 부르게 한다.

거는 자리를 가린다. 이 검사는 자연어 필드에만 건다 — ``reason`` 처럼 사람이 읽는
문장이다. ``candidate_id``·``ref_id``·enum 값은 숫자가 들어 있어도 정상이라 여기에 걸면
멀쩡한 응답을 거부하게 된다. ``runtime`` 이 그 경계를 지키고 있고
("chosen_candidate_id 는 검사 대상이 아니다 — 후보 id 에 숫자가 들어갈 수 있다"),
다른 역할도 같은 경계를 따른다.
"""

import re
import unicodedata

#: 출력에 숫자가 있으면 거부한다. 팀 4벌이 전부 쓰는 규칙이고, 정의서 §1.2-3("LLM은
#: 가격·수량 숫자를 생성하지 않는다")을 프롬프트가 아니라 검증기로 강제하는 장치다.
#: ``\d``는 ASCII와 전각(１２３)을 잡지만 ``½``·``²``·``Ⅻ`` 같은 유니코드 수치 문자는
#: 놓친다 — ``str.isnumeric()``이 그쪽을 덮는다.
#: 한계: 한글 수사("백삼십원")는 정규식으로 못 막는다. 그건 판단 영역이라 프롬프트가
#: 맡고, 여기서 잡는 건 기계적으로 판별 가능한 것뿐이다 — 이 한계를 알고 쓴다.
NUMERIC_PATTERN = re.compile(r"\d")


def contains_number(text: str) -> bool:
    return bool(NUMERIC_PATTERN.search(text)) or any(ch.isnumeric() for ch in text)


def contains_control_chars(text: str) -> bool:
    """제어문자·zero-width·bidi 문자. rationale에 그대로 실리므로 표시 안전성 문제다."""
    return any(
        unicodedata.category(ch) in {"Cc", "Cf"} and ch not in "\n\t" for ch in text
    )


#: 숫자·날짜·비율·금액을 가릴 표시. 표시 자체에 숫자가 없다.
#:
#: ``<N1>`` 처럼 번호를 붙이면 그 번호가 다시 숫자라, 정제 결과에 숫자가 남았는지
#:   보는 검사가 자기가 만든 표시에 걸린다.
PLACEHOLDERS = ("<NUM>", "<DATE>", "<PCT>", "<AMT>")

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_PCT = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?\s*%")
_AMT = re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:원|kg|KRW)")
_NUM = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


def sanitize_numerals(text: str) -> str:
    """자연어에서 숫자·날짜를 표시로 바꾼다. 문장의 뜻은 남기고 값만 가린다.

    왜 값을 가리나. 근거 자기 검토에 문장을 넣어야 "결론이 근거보다 센가" 를
    판정할 수 있는데, 원본 숫자를 같이 넣으면 판단자가 그 숫자를 사유에 베껴 쓴다 —
    ⑤ 가 라벨만 넘기기로 한 이유와 같다 (규칙 6).

    순서가 있다 — 날짜·비율·금액을 먼저 바꾸고 남은 숫자를 마지막에 바꾼다. 반대로 하면
    ``2026-01-05`` 가 ``<NUM>-<NUM>-<NUM>`` 이 되어 무엇이었는지 알 수 없게 된다.

    자연어에만 쓴다. ``ref_id``·후보 id·enum 에 쓰면 식별자가 뭉개진다 —
    거기 든 숫자는 지어낸 값이 아니라 이름의 일부다.
    """
    text = _DATE.sub("<DATE>", text)
    text = _PCT.sub("<PCT>", text)
    text = _AMT.sub("<AMT>", text)
    return _NUM.sub("<NUM>", text)
