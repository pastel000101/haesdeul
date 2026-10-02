# 화면용 API — 파트별 연결법

운영 콘솔 여섯 탭에 값을 대는 자리입니다.
탭 하나는 `app/api/<파트>/` 의 `presenter.py`(화면 부품 조립) · `schema.py`(응답 모양) ·
`routes.py`(주소) 로 되어 있고, 값은 부서의 `readmodel/` 이 DB 에서 읽어 줍니다.
화면은 안 건드립니다.

---

## 0. 5분 요약

```
① backend/app/api/<내파트>/presenter.py 의 build() 를 연다 — 화면 부품을 조립하는 자리
② 값은 내 부서 readmodel(app/<부서>/readmodel/) 에서 받는다 — SQL 은 부서 repository/ 에
③ 읽기에 실패하면 Source(filled=False) 와 이유를 함께 낸다 (예측 · 매입 탭이 그렇게 한다)
④ pytest tests/api 를 돌린다
```

주소도 화면도 이미 있습니다. 여섯 탭 모두 실제 데이터를 읽습니다.

| 파트 | 조립 파일 | 값을 읽는 곳 | 화면 주소 |
|---|---|---|---|
| 마스터 | `app/api/dashboard/presenter.py` | 다섯 탭의 `presenter` · `master/readmodel/purchase_record.py` | `/console` |
| ML | `app/api/forecast/presenter.py` | `ml/readmodel/` | `/console/forecast` |
| 매입 | `app/api/purchase/presenter.py` | `master/readmodel/purchase_tab.py` · `purchase_record.py` | `/console/purchase` |
| 재무 | `app/api/finance/presenter.py` | `finance/readmodel/dashboard.py` | `/console/finance` |
| 물류 | `app/api/logistics/presenter.py` | `logistics/readmodel/console.py` · `inbound_schedules.py` | `/console/inventory` |
| 판매 | `app/api/sales/presenter.py` | `sales/readmodel/dashboard.py` | `/console/sales` |

---

## 1. 이게 왜 따로 있나

부서 입구는 에이전트를 돌리는 문입니다. 화면이 쓸 수가 없습니다.

```
POST /finance/agent            에이전트를 돌린다
GET  /finance/runs/{run_id}    "그 실행" 의 이력
```

화면은 `run_id` 를 모릅니다. 날짜 하나만 압니다.
"1월 6일 현금 잔액 얼마?" 를 부서 입구에는 물을 자리가 없습니다.

그리고 `/finance/agent` 를 화면이 부르면 화면을 열 때마다 에이전트가 돕니다.
느리고, 비싸고, 볼 때마다 답이 달라집니다.

그래서 주소로 갈랐습니다.

```
/finance/agent     에이전트를 돌린다   (부서 입구)
/api/finance       화면에 값을 준다    (여기)
```

`/api` 로 시작하면 화면용입니다. 규칙은 이 하나뿐입니다.

---

## 2. 폴더 하나 = 파트 하나

```
backend/app/api/
  primitives.py     여섯 탭이 함께 쓰는 부품 — 표 · 그래프 · 요약칸
  calendar.py       공용 날짜축
  router.py         HTTP 주소를 전부 모아 app/main.py 에 넘기는 곳
  <파트>/            화면 탭: dashboard · forecast · purchase · finance · logistics · sales
    schema.py       응답 모양      (바꾸려면 화면 담당과 같이 본다)
    presenter.py    화면 부품 조립 — readmodel 값을 Stat · Table · Chart 로
    routes.py       화면 주소      (거의 안 건드린다)
    <자원>.py       부서 HTTP 입구 — 에이전트 실행 · 이력 · 쓰기 (예: finance/agent.py)
  console/routes.py 실행 목록 (화면이 고를 실행)
  master/ · critic/ · ml/   마스터 · Critic · ML 의 HTTP 입구 (화면 탭 없음)
```

화면용(`/api/...`)과 부서 입구(`/finance/agent` 처럼 `/api` 밖)가 같은 폴더에 있습니다.
주소로 가릅니다(아래 1절). 부서 폴더(`app/finance/` 등)에는 FastAPI 코드가 없습니다.

---

## 3. 예시값은 읽기에 실패했을 때만

탭마다 응답에 `source=Source(filled=…, owner=…, note=…)` 가 있습니다.
여섯 탭 모두 실제 데이터를 읽으므로 평소에는 `filled=True` 입니다.
DB 를 못 읽었을 때 예측 · 매입 탭은 화면 구성을 보이려고 예시값을 내는데, 그때는
`filled=False` 와 이유(`note`)를 함께 냅니다.

```python
# app/api/forecast/presenter.py — 원본 저장소를 못 읽었을 때
source=Source(
    filled=False, owner="ML",
    note="원본 데이터 저장소에 연결할 수 없어 예시값을 보여줍니다 — ...",
),
```

`filled=False` 면 화면 위에 주황색 「예시값」 띠가 뜹니다.

예시값을 내는 자리에서는 반드시 `filled=False` 로 두세요.
실제 값인데 `False` 면 아무도 안 믿고, 예시값인데 `True` 면
데모 숫자를 실적으로 읽습니다. 뒤쪽이 훨씬 위험합니다.

---

## 4. 값은 어디서 오나

### 4-1. 읽는 길

```
routes.py           주소 · 인자 검사
  → presenter.py    build() — 화면 부품 조립 (SQL 없음)
    → app/<부서>/readmodel/   조회 연결을 빌려 repository 를 부른다
      → app/<부서>/repository/  SQL — 받은 연결로 실행만
```

```python
# app/api/finance/presenter.py (줄임)
from app.finance.readmodel.dashboard import get_finance_cashflow, get_finance_dashboard

def build(as_of: date, state: str) -> FinanceTab:
    dash = get_finance_dashboard(sim_run_id=SHOWN_SIM_RUN_ID, as_of=as_of)
    flow = get_finance_cashflow(sim_run_id=SHOWN_SIM_RUN_ID, as_of=as_of, days=30)
    ...
```

화면층(`app/api`)에는 SQL 이 없고 연결도 직접 빌리지 않습니다 — 구조 검사
(`tests/architecture/test_http_entries.py`)가 막습니다. 연결 모듈을 들이는 예외는 두 자리로
정해 두었습니다(재무 HTTP 의 `Depends` 연결 · 물류 화면의 실패 분류). 새 값이
필요하면 부서 `repository/` 에 SQL 을, `readmodel/` 에 조회 함수를 더하고 presenter 는
그 함수를 부르기만 합니다.

### 4-2. 값이 없을 때

readmodel 이 «그날 행이 없다» 를 `None` 으로 돌려주면 0 이 아니라 «없음» 으로 그립니다
(`—` · `empty_text` · 이유를 적은 `Note`). 둘은 뜻이 다릅니다 (6-② 규칙).

### 4-3. DB 연결과 스키마 이름

연결은 `app/core/db.py` 의 공통 풀이 줍니다. 조회는 `core_db.read_connection()`, 쓰기는
`core_db.connection()` + `core_db.transaction(conn)` 이고, 부서마다 따로 열지 않습니다.
스키마 이름은 하드코딩하지 말고 `app/core/settings.py` 의 `get_db_schema()` 로 받으세요.
부서마다 따로 두는 `db.py` 는 없습니다.

---

## 5. 부품 — 무엇으로 그리나

전부 `app/api/primitives.py` 에 있습니다. 화면은 이것만 그릴 줄 압니다.

### `Stat` — 큰 숫자 한 칸

```python
Stat(label="받을 돈", value="7,305", unit="만원",
     detail="매출채권 15건 · 아직 수금 0원",
     tone="warn",      # neutral · good · warn · bad · info · sim
     raw=7305)         # 계산·정렬에 쓸 수. 글자와 따로 담는다
```

`value` 는 사람이 읽을 글자, `raw` 는 계산용 수입니다.
자릿점·단위(만원)는 파트마다 다르므로 글자를 그대로 받습니다.

### `Table` — 표

```python
Table(
    columns=[
        Column(key="d",    label="날짜",   mono=True),
        Column(key="cash", label="현금", align="right", mono=True),
    ],
    rows=[
        {"d": "2025-12-31", "cash": "-1,328만원"},
        {"d": "2025-12-30", "cash": None},        # ← 공란. 0 이 아니다
    ],
    empty_text="이 기간에 값이 없습니다",           # 행이 0개일 때 대신 적을 말
)
```

- `columns[].key` 와 `rows` 의 키 이름이 같아야 합니다. 다르면 그 칸이 통째로 빕니다
- `align="right"` 는 수치 칸에, `mono=True` 는 자릿수를 맞춰 보고 싶을 때
- `empty_text` 를 꼭 쓰세요. "없음" 과 "아직 안 들어옴" 은 다릅니다

### `Chart` — 그래프

```python
Chart(
    label="12월 일별 현금",       # 읽어주는 도구가 쓸 이름
    y_min=-20, y_max=110,
    y_ticks=[0, 50, 100],
    y_unit="M",                  # 눈금 뒤에 붙일 글자
    series=[
        Series(name="실적", data=[58.1, 54.0, None, 49.8], tone="info"),
        Series(name="추정", data=[None, None, 49.8, 45.6], dashed=True),
    ],
    x_labels=["12/02", "", "", "12/31"],   # 공용 날짜축을 안 쓸 때만
)
```

값의 단위와 눈금 글자가 다르면 `y_labels` 를 쓰세요.

```python
# 재고는 kg 로 그리는데 눈금은 톤으로 적어야 한다
y_ticks=[10000, 20000],
y_labels=["10t", "20t"],     # 없으면 "10,000t" 이 되어 틀린다
```

### `Note` — 설명 상자

```python
Note(tone="warn",
     text="둘째 칸은 **0 이 아니라 공란**입니다 — 그날 보고가 없었습니다.")
```

`**굵게**` 만 알아듣습니다. 다른 표시는 글자 그대로 나옵니다.

### `Card` · `Pane` — 카드가 많을 때

재고·물류와 판매가 씁니다. 카드를 늘려도 화면은 안 고칩니다.

```python
Card(
    key="lots",                      # 파트 안에서 안 겹치게
    title="Lot 상태",
    subtitle="Snapshot + turnover 계산 결과",
    source_ref="inventory_lots",     # 오른쪽 위에 작게 — 어느 표를 읽었나
    lead=Note(...),                  # 표보다 먼저 읽을 안내
    flow=["현재고", "예약", "할당"],   # 화살표로 잇는 단계
    stats=[...], table=..., chart=...,
    bullets=["원칙 한 줄", "또 한 줄"],
    footer="표 아래 작은 글",
)
```

채운 것만 그립니다. 다 채울 필요 없습니다.
카드를 하나 더 넣어도 화면은 안 고칩니다 — 목록이라 그냥 늘어납니다.

---

## 6. 지켜야 할 것 다섯

### ① `/api` 아래는 읽기만 합니다

쓰기는 부서 입구가 합니다 (`POST /master/runs/{id}/decision`).
여기에 POST 를 만들면 같은 일을 두 군데서 하게 되고,
«어느 쪽으로 승인했나» 가 기록에서 갈립니다.

테스트가 막습니다 — `tests/api/test_screen_api.py` 의 `test_쓰기는_막혀_있다`.

### ② `None` 은 0 이 아닙니다

```python
data=[21400, None, 19800]     # 가운데는 그날 보고가 없었다
data=[21400, 0,    19800]     # 가운데는 재고가 없었다      ← 뜻이 다르다
```

값이 없으면 `None` 을 넣으세요. 화면이 선을 끊고 표에 `—` 를 그립니다.
0 으로 채우면 «그날 재고가 없었다» 는 거짓말이 됩니다.

그리고 왜 없는지를 `Note` 에 적으세요. 안 적으면 보는 사람이 0 으로 읽습니다.

### ③ 그래프 계열 길이는 날짜축과 같아야 합니다

화면은 칸 번호로만 위치를 잡습니다.
계열이 짧으면 오류 없이 조용히 왼쪽으로 밀립니다.
"그날 값이 그랬구나" 로 읽히는 것이 제일 위험합니다.

날짜축 길이는 응답 안 `axis.days` 로 옵니다.

### ④ 대시보드는 숫자를 만들지 않습니다

```
마스터는 숫자를 만들지 않는다.
부서 값을 날짜 축에 놓고, 없으면 공란으로 둔다.
```

`app/api/dashboard/presenter.py` 는 다른 파트의 `build()` 를 불러 골라 담기만 합니다.
대시보드에 자기 파트 값을 얹고 싶으면 자기 `presenter.py` 에 함수를 만들고
대시보드가 그걸 부르게 하세요. 재무·물류가 이렇게 하고 있습니다.

```python
# app/api/logistics/presenter.py
def dashboard_stock(n: int, at: int, as_of: date) -> Chart: ...

# app/api/dashboard/presenter.py
f_stock = 맡긴다(logistics_presenter.dashboard_stock, n, at, as_of)
```

안 그러면 갈라집니다 — 재고 요약은 4,550kg 인데 그래프 끝은 14,600kg 인 일이
있었습니다.

### ⑤ 백엔드를 고치면 서버를 다시 올리세요

`--reload` 없이 띄우면 코드를 고쳐도 안 바뀝니다.
새 칸을 더하고 서버를 다시 올리지 않으면 화면과 응답 모양이 어긋나 화면이 통째로
안 뜰 수 있습니다.

---

## 7. 확인법

### 값이 나오나

```bash
curl "http://127.0.0.1:8000/api/finance?as_of=2025-12-31&state=base"
```

브라우저로 보려면 `http://127.0.0.1:8000/docs` 에서 눌러 보면 됩니다.

### 모양이 맞나

```bash
cd backend
uv run pytest tests/api -q
```

검사는 대부분 모양을 봅니다 — 실제 데이터의 숫자는 날마다 바뀌므로 숫자를 잡으면
테스트를 지우게 되고 그러면 검사가 사라집니다.

무엇을 잡는가:

```
탭이 열리나 (200)
쓰기가 막혀 있나 (405)
없는 값에 400 을 내나 · 무엇이 가능한지 문장으로 알려주나
Source.filled 가 있나
그래프 계열 길이가 날짜축과 같나
y_labels 가 y_ticks 와 짝인가
대시보드 값이 부서 값과 같은가
표 머리에 있는 칸이 행에도 있나
```

### 화면으로 보나

```bash
# 백엔드
cd backend && uv run uvicorn app.main:app --port 8000

# 프론트 (다른 창)
cd frontend && npm run dev
```

`localhost` 로 여세요. `127.0.0.1` 은 흰 화면이 뜹니다.
Next 개발 서버가 `127.0.0.1` 을 다른 사이트로 보고 막습니다.

---

## 8. 파트별 — 어디에 무엇이 들어가나

### 마스터 · 대시보드

`app/api/dashboard/presenter.py` → `/console`

| 자리 | 무엇 |
|---|---|
| `badges` | 상단 알약 — 장 열림 · 승인 대기 |
| `stats` | 요약 다섯 칸 — 다섯 파트에서 하나씩 |
| `forecast_cards` | 세 품목 내일 예측 (ML 것을 그대로) |
| `purchase` | 승인 기다리는 안 |
| `cash_chart` / `stock_chart` | 재무 · 물류가 만들어 준 것 |
| `sources` | 다섯 파트의 채움 여부 |

여기서 계산하지 마세요. 6-④ 규칙이 여기 걸립니다.

### ML · 가격 예측

`app/api/forecast/presenter.py` → `/console/forecast`

| 자리 | 무엇 |
|---|---|
| `cards` | 세 품목 내일 예측 · 구간 · 폭 |
| `chart` | 실측 · 전일 예측 · 내일 예측 구간 |
| `accuracy` | 우리가 얼마나 틀리나 |
| `quality` | 어느 조합을 써도 되나 |
| `caveat` | 가운데 값만 보고 사면 안 된다는 경고 |

`accuracy` 와 `caveat` 를 지우지 마세요. 예측선만 그리면 정답처럼 보입니다.
1,000원짜리를 배추는 197원 틀립니다.

원본 예측 저장소를 못 읽으면 예시값으로 떨어집니다(`filled=False`) — 화면이 통째로
안 뜨는 것보다 낫습니다.

### 매입

`app/api/purchase/presenter.py` → `/console/purchase`

| 자리 | 무엇 |
|---|---|
| `stats` | 오늘 제안 · 승인 대기 · 확정 매입액 · 입고 예정 |
| `plans[]` | 안 하나 — 수량 · 금액 · 상한가 · 회차 · 지급 · 근거 · 걸리는 것 |
| `plans_note` | 안이 왜 이 개수인가 |
| `committed` | 승인을 거친 뒤에 생기는 확정 매입 |

`Reason.ref` 를 꼭 채우세요. 근거에 꼬리표(`FC-2026-01-06`)가 없으면
나중에 되짚을 수 없습니다.

승인 버튼은 이 화면에 없습니다. 승인은 아래 서랍(마스터)에서
`POST /master/runs/{request_id}/decision` 으로 합니다.
이 화면은 무엇을 고를지 판단할 근거를 보이는 자리입니다.

### 재무

`app/api/finance/presenter.py` → `/console/finance`

| 자리 | 무엇 |
|---|---|
| `states` / `selected` | 저장된 기준 상태 (BASE_NO_LOAN · LOAN_BASELINE) |
| `stats` | 현금 · 최소 운영자금 · 받을 돈 · 부채 |
| `cash_chart` | 한 달 일별 현금 |
| `flows` | 돈이 어디로 나가고 들어왔나 (원장 용어 대신 사람 말로) |
| `balances` | 받을 돈 · 줄 돈 |
| `closings` | 최근 일별 마감 |
| `tables_read` | 어느 표를 읽었나 — 화면 아래에 적힙니다 |

조회 전용입니다. 상태 전이는 승인 트랜잭션(`master/service/transition.py` 의
`apply_approval`) 안에서만 일어납니다.

`StateOption` 은 «대출 승인 결과» 가 아닙니다. DB 에 저장된 기준 상태입니다.
데모에도 그렇게 적혀 있습니다.

### 물류 · 재고

`app/api/logistics/presenter.py` → `/console/inventory`

안에서 넷으로 나뉩니다.

| 작은 탭 | `key` |
|---|---|
| 한눈에 보기 | `summary` |
| 재고 · 신선도 | `stock` |
| 입고 · 검수 | `inbound` |
| 예약 · 출고 | `outbound` |

각 `Pane` 은 `stats` 와 `cards[]` 를 가집니다.
카드를 더해도 화면은 안 고칩니다.

대시보드에 얹는 재고 그래프는 `dashboard_stock(n, at, as_of)` 가 만듭니다. 요약 숫자와
같은 곳(물류 readmodel `app/logistics/readmodel/console.py`)에서 나와야 둘이 안 갈라집니다.

### 판매

`app/api/sales/presenter.py` → `/console/sales`

| 자리 | 무엇 |
|---|---|
| `stats` | 총 판매금액 · 판매량 · 공헌이익 · 아직 받을 돈 |
| `cards[]` | 한눈에 · 최근 내역 · 언제 돈이 들어오나 · 알기 쉬운 해석 |

조회 전용입니다. 시나리오를 돌리지 않고 저장된 결과만 봅니다.

---

## 9. 모양을 바꾸고 싶으면

`schema.py` 를 고치면 됩니다. 다만 화면도 같이 고쳐야 합니다.

```
backend/app/api/<파트>/schema.py     ←→     frontend/src/lib/screen.ts
```

한쪽만 고치면 그 칸이 조용히 빕니다. 오류가 안 납니다.
화면 담당(ML 파트)에게 말해 주세요.

칸을 더하는 것은 안전합니다. 이름을 바꾸거나 지우는 것이 위험합니다.

---

## 10. 막히면

| 증상 | 볼 곳 |
|---|---|
| 화면이 하얗다 | `127.0.0.1` 말고 `localhost` 로 열었나 |
| "백엔드에 닿지 못했습니다" | 백엔드가 떠 있나 · 포트가 맞나 |
| 값이 안 바뀐다 | 백엔드를 다시 올렸나 (`--reload` 없이 띄웠으면 안 바뀝니다) |
| 표의 한 칸이 통째로 비었다 | `columns[].key` 와 `rows` 키 이름이 같나 |
| 선이 엉뚱한 날짜에 있다 | 계열 길이가 `axis.days` 와 같나 |
| 숫자가 두 군데서 다르다 | 대시보드가 값을 직접 만들고 있지 않나 |
| 「예시값」 띠가 떴다 | DB 를 읽었나 — 예측 · 매입 탭은 읽기에 실패하면 예시값과 이유(`Source.note`)를 낸다 |
