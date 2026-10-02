"""화면 부품 — 여섯 탭이 함께 쓰는 응답 조각.

왜 이런 게 따로 있나.

이 아래(`app/api/*/presenter.py`)는 부서마다 다른 사람이 채웁니다. 그런데 화면은
한 사람이 만듭니다. 그래서 "어떤 모양으로 주고받나"를 여기 한 군데에 못박아
둡니다. 부서는 이 모양에 값을 담기만 하면 되고, 화면은 이 모양만 그릴 줄 알면
됩니다.

여기 있는 것은 값의 모양이지 화면의 모양이 아닙니다. 색·굵기·자리는
프론트가 정합니다. 백엔드가 `style` 이나 `className` 을 내려보내면 안 됩니다 —
그러면 화면을 고칠 때마다 백엔드를 고쳐야 합니다.

숫자와 표시를 나눠 담습니다. `value` 는 사람이 읽을 글자(`"-1,328"`)고
`raw` 는 계산에 쓸 수 있는 수입니다. 화면이 정렬·비교를 하려면 수가 필요한데,
자리 표시(만원·소수점)는 부서마다 다르므로 글자도 같이 받습니다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

#: 값의 성격. 색이 아니라 뜻이다 — 색은 프론트가 정한다.
Tone = Literal["neutral", "good", "warn", "bad", "info", "sim"]

#: 표 칸을 어느 쪽으로 붙이나.
Align = Literal["left", "right", "center"]


class Stat(BaseModel):
    """큰 숫자 한 칸. 화면 맨 위 요약 줄에 쓴다."""

    label: str = Field(description="무엇의 값인가")
    value: str = Field(description="사람이 읽을 값. 자릿점까지 넣어서 준다")
    unit: str | None = Field(default=None, description="원 · kg · 만원 같은 단위")
    detail: str | None = Field(default=None, description="아래 작게 붙는 한 줄")
    tone: Tone = "neutral"
    raw: float | None = Field(default=None, description="계산에 쓸 수. 없으면 None")


class Badge(BaseModel):
    """상단 알약. 상태를 한눈에 알리는 짧은 말."""

    text: str
    tone: Tone = "neutral"


class Note(BaseModel):
    """설명 상자. 표나 그래프 밑에 붙는 문장.

    값이 비어 있는 이유를 적는 자리로도 씁니다. 공란의 이유를 화면에 적지 않으면
    보는 사람이 공란을 0 으로 읽습니다.
    """

    text: str
    tone: Tone = "neutral"


class Column(BaseModel):
    """표의 머리 한 칸."""

    key: str = Field(description="행 딕셔너리에서 꺼낼 이름")
    label: str
    align: Align = "left"
    mono: bool = Field(default=False, description="수치라 폭 고정 글꼴로 볼 것인가")
    #: 칸 너비. CSS 값(`12%` · `120px`)이고 표마다 따로 정한다.
    #:
    #: 칸 수가 표마다 다르므로 너비 규칙도 하나일 수 없다 (#812). 칸 넷인 표와
    #:    일곱인 표에 같은 규칙을 걸면 한쪽은 반드시 성기거나 빽빽해진다.
    #:
    #: `None` 이면 균등 배분이다 — 값이 고만고만한 표는 그대로 두면 된다.
    width: str | None = Field(
        default=None, description="칸 너비(CSS). 없으면 균등 배분"
    )


class Table(BaseModel):
    """표 하나.

    행은 `{칸이름: 값}` 입니다. 값이 `None` 이면 화면은 0 이 아니라 공란으로
    그립니다.
    """

    columns: list[Column]
    rows: list[dict[str, str | float | int | None]] = Field(default_factory=list)
    note: Note | None = None
    empty_text: str = Field(
        default="값이 없습니다",
        description=(
            "행이 0개일 때 표 대신 적을 문구. 값이 없는 경우와 아직 들어오지 않은 경우를 "
            "구분해 적는다"
        ),
    )


class Series(BaseModel):
    """그래프 선 하나.

    `data` 의 길이는 그래프의 가로 칸 수와 같아야 합니다. 날짜축을 쓰는 그래프는
    `CalendarAxis.days` 길이, 자기 눈금을 쓰는 그래프는 `Chart.x_labels` 길이입니다.
    값이 없는 날(휴장일, 값이 아직 들어오지 않은 날)은 0 이 아니라 `None` 으로 둡니다.
    0 으로 그리면 없는 값이 0 으로 읽힙니다.
    """

    name: str
    data: list[float | None]
    tone: Tone = "info"
    dashed: bool = Field(default=False, description="추정값이면 점선")
    width: float = 2.0
    opacity: float = 1.0
    end_dot: bool = Field(default=False, description="마지막 점에 동그라미를 찍나")


class Band(BaseModel):
    """예측 구간 — 위아래 두 선 사이를 칠한다."""

    name: str
    hi: list[float | None]
    lo: list[float | None]
    tone: Tone = "info"


class Marker(BaseModel):
    """그래프 위 한 점에 붙이는 표시. 지급·폐기처럼 그날 일어난 일."""

    index: int = Field(description="날짜축에서 몇 번째 칸인가")
    value: float
    label: str
    tone: Tone = "neutral"


class Chart(BaseModel):
    """날짜축 그래프 하나."""

    label: str = Field(description="읽어주는 도구가 쓸 이름")
    y_min: float
    y_max: float
    y_ticks: list[float]
    y_unit: str = Field(default="", description="눈금 뒤에 붙일 글자. 예 'M'")
    y_labels: list[str] = Field(
        default_factory=list,
        description=(
            "눈금 글자를 직접 줄 때 쓴다 (`y_ticks` 와 같은 길이). 비어 있으면 화면이 "
            "눈금 값 뒤에 `y_unit` 을 붙여 적는다. 값의 단위와 눈금에 보일 단위가 다르면 "
            "이 칸을 쓴다 — 예를 들어 kg 값에 't' 만 붙이면 10,000kg 이 '10,000t' 으로 보인다."
        ),
    )
    series: list[Series] = Field(default_factory=list)
    bands: list[Band] = Field(default_factory=list)
    markers: list[Marker] = Field(default_factory=list)
    note: Note | None = None
    shade_label: str = Field(
        default="휴장 · 경매 없음",
        description=(
            "회색으로 칠한 칸(`Day.market_open` 이 거짓인 칸)이 무엇인지 알리는 범례 문구. "
            "탭마다 뜻이 다르다 — 대시보드는 휴장일이고, 가격 예측은 모델 대신 "
            "어제 가격을 낸 칸이다."
        ),
    )
    x_labels: list[str] = Field(
        default_factory=list,
        description=(
            "날짜축 없이 그리는 그래프의 가로 눈금 글자 (예: 재무 일별 현금, 판매 남은 "
            "수금 일정). 화면은 날짜축이 주어지면 날짜축을, 없으면 이 값을 쓴다. "
            "칸이 많으면 일부만 적고 나머지는 빈 문자열로 둔다 — 다 적으면 글자가 겹친다."
        ),
    )


class Day(BaseModel):
    """날짜축 한 칸."""

    date: str = Field(description="YYYY-MM-DD")
    dow: str = Field(description="월 화 수 …")
    market_open: bool = Field(
        description="그날 경매가 서나. 거짓인 칸은 화면이 회색으로 칠한다 (`Chart.shade_label`)"
    )
    survey: bool = Field(description="그날 가격 조사가 있나")


class CalendarAxis(BaseModel):
    """날짜축 하나 — 그래프의 가로 칸과 기준일(as_of) 위치를 정합니다.

    대시보드는 공용 날짜축(기준일 앞 8일 · 뒤 3일, 달력일) 하나 위에 여러 부서의
    그래프를 함께 그립니다. 그래프마다 축을 따로 만들면 한 화면 안에서 칸과 as_of 선의
    자리가 서로 어긋나기 때문입니다. 가격 예측 탭은 예측 대상일로 자기 축을 만듭니다.
    """

    as_of: str
    as_of_index: int = Field(description="days 에서 as_of 가 몇 번째인가")
    days: list[Day]


#: 이 값을 읽어 본 결과가 무엇인가. `filled` 로는 못 가르는 셋이 있습니다.
#:
#: ```text
#: OK              읽었고 값이 있다
#: NO_DATA         읽었는데 그 축에 사실이 없다        0 도 오류도 아니다
#: DATA_NOT_READY  아직 만들어지지 않은 값이다
#: ERROR           읽다가 실패했다                     숫자를 지어내지 않는다
#: DEMO            일부러 켠 예시값                    실패해서 떨어진 것이 아니다
#: ```
#:
#: 주의: 실패를 `DEMO` 로 적지 않습니다. 예외를 잡아 예시 숫자(예: 14,600kg)를 내려보내면
#:    화면에서 실적처럼 읽힙니다.
SourceStatus = Literal["OK", "NO_DATA", "DATA_NOT_READY", "ERROR", "DEMO"]


class Source(BaseModel):
    """이 값이 어디서 왔나.

    `filled` 는 실제 값을 읽어 채웠는지를 나타냅니다. `False` 이면 실제 값을 읽지 못해
    예시값을 담았다는 뜻이고, 화면은 예시값 표시를 붙입니다. 표시가 없으면 보는 사람이
    예시 숫자를 실적으로 읽습니다.

    `status` 는 선택 칸입니다. `filled` 만으로는 '읽었는데 값이 없다', '읽다가
    실패했다', '예시값이다' 를 구분할 수 없어 읽은 결과를 따로 적습니다. 이 칸을 채우지
    않는 탭은 `None` 입니다.
    """

    filled: bool = Field(description="부서가 실제 값으로 채웠나. False 면 예시값")
    owner: str = Field(description="이 값을 채울 파트")
    note: str | None = Field(default=None, description="어디서 읽어온 값인지 한 줄")
    status: SourceStatus | None = Field(
        default=None, description="읽어 본 결과. 안 채운 탭은 None"
    )


class Card(BaseModel):
    """카드 하나 — 제목과 내용 한 덩어리.

    재고·물류 탭과 판매 탭은 카드 목록으로 화면을 만듭니다. 카드마다 따로 칸 이름을
    두지 않고 목록으로 받으므로, 부서가 카드를 더해도 화면 코드를 고치지 않고 그대로
    늘어납니다.

    모든 칸을 채울 필요는 없습니다. 화면은 채운 칸만 그립니다.
    """

    key: str = Field(description="화면이 자리를 기억하는 데 쓴다. 부서 안에서 겹치지 않게")
    title: str
    subtitle: str | None = None
    source_ref: str | None = Field(default=None, description="어느 표를 읽었나. 오른쪽 위에 작게")
    lead: Note | None = Field(default=None, description="표보다 먼저 읽을 안내")
    flow: list[str] = Field(default_factory=list, description="화살표로 잇는 단계")
    stats: list[Stat] = Field(default_factory=list)
    table: Table | None = None
    chart: Chart | None = None
    bullets: list[str] = Field(default_factory=list, description="원칙·주의처럼 줄글 목록")
    footer: str | None = None


class Pane(BaseModel):
    """탭 안의 작은 탭. 재고·물류가 넷으로 나뉜다."""

    key: str
    label: str
    stats: list[Stat] = Field(default_factory=list)
    cards: list[Card] = Field(default_factory=list)


# ── 그래프 칸 값 ─────────────────────────────────────────────────────────────
#
# `Chart` 의 `y_ticks` · `x_labels` 에 넣을 값을 짓는 도우미 둘. 재무 · 판매 탭이 함께 쓴다.


def three_ticks(start: float, end: float) -> list[float]:
    """세로 눈금 셋 — 아래 · 가운데(소수 한 자리 반올림) · 위."""
    middle = round((start + end) / 2, 1)
    return [start, middle, end]


def spread_labels(labels: list[str]) -> list[str]:
    """가로 글자를 걸러 적는다 — 처음 · 끝 · 일곱 칸마다 하나(7번째부터). 나머지는 빈 문자열.

    칸이 둘 이하면 다 적는다. 다 적으면 글자가 겹친다 (`Chart.x_labels` 설명).
    """
    if len(labels) <= 2:
        return labels
    visible = {0, len(labels) - 1}
    visible.update(range(6, len(labels) - 1, 7))
    return [label if index in visible else "" for index, label in enumerate(labels)]
