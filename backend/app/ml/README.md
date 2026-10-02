# 마스터 ↔ ML 연결 — ML 포트 안내

ML 은 마스터가 부르는 에이전트 `ml` 로 연결돼 있습니다. 상태 조회(`STATUS_QUERY`)만
받고, 사람이 읽을 답(`answer_markdown`)과 기계용 칸을 함께 돌려줍니다.

---

## 1. 연결된 자리

| 무엇 | 자리 |
|---|---|
| 이름 `ml` · 받는 모드 `STATUS_QUERY` | `app/contracts/envelope.py` 의 `AgentName` · `_AGENT_MODES` |
| 등록 | 마스터 조립 뿌리 `app/master/registry/bootstrap.py` 가 `app.ml.adapter.ml_port` 를 등록한다 |
| `answer_markdown` 그대로 쓰기 | `app/master/domain/answer.py` 의 `_MARKDOWN_AGENTS` · 화면 `MasterConsole.tsx` 가 `Markdownish` 로 그린다 |
| 질문 문장 넘기기 | `app/master/service/status_flow.py` 가 `{"question": 원문, "item": 품목}` 을 싣는다 |

새 모드를 만들지 않았습니다. `STATUS_QUERY` 는 "묻기만 하는 요청" 이고 ML 이 하는 일이
정확히 그것입니다.

`_AGENT_DEPT` 에는 `ml` 이 없습니다. ML 은 조언자가 아니라 답하는 쪽이라 축 조정을
제안하지 않습니다. 넣으면 쓰지 않는 권한이 열립니다.

---

## 2. 돌려주는 것

```text
포트      app/ml/adapter.py::ml_port
서명      (AgentRequest) -> (AgentReply, ExecutionMetadata)
모드      STATUS_QUERY
```

`payload` 는 이렇게 채웁니다.

| 키 | 뜻 |
|---|---|
| `answer_markdown` | 사람에게 그대로 보여줄 글 (표·굵은 글씨 포함) |
| `answer_status` | `ok` · `partial` · `need_clarify` · `out_of_scope` 등 (소문자) |
| `answer_routes` | 무엇을 답했나 — `forecast` · `batch` · `perf` (쉼표로 이음) |
| `item` | 배추·무·양파 (가격 갈래일 때) |
| `as_of` | 예측 기준일 |
| `forecasts[]` | 답한 행 — `target_dt` · `predicted` · `lower` · `upper` · `item` · `kind` |
| `model_version` · `forecast_source` | 어느 모델·어느 표에서 읽었나 |
| `use_recommended` | 답에 든 조합을 전부 써도 되나 — 하나라도 막혔으면 `false` (없으면 «모른다») |
| `out_of_range_note` | 예측 범위 밖이라 못 답한 날 (있을 때만) |
| `batch` | 그날 배치 — `status`(사람 말) · `run_id` · `n_ok` · `n_fail` |
| `report` | 그날 AI 점검 보고서의 `ran_at` |
| `performance[]` | 봉인 개봉 성능 아홉 칸 — `item` · `kind` · `avg_price` · `avg_error` · `pct` |
| `models[]` | 지금 도는 모델 셋 — `kind` · `model_ver` · `created_at` · `train_end` · `last_swapped_at` |

대문자 라벨을 최상위에 두지 않습니다. 봉투가 최상위 숫자·대문자 라벨에 근거를
요구하는데(`required_claims`), `"OK"` · `"AUC"` 같은 값에는 댈 수치가 없습니다.
같은 사실은 `forecasts[]` · `performance[]` 안으로 넣습니다.

근거(`Evidence`)의 주소는 payload 를 그대로 가리킵니다 —
`forecasts[0].predicted` · `batch.n_ok` · `performance[3].pct`.

### 2-1. `answer_markdown` 은 펼치지 않고 그대로 쓴다

마스터의 사실 줄 규칙(payload 의 키마다 한 줄)을 그대로 태우면 마크다운 표가 한 줄로
뭉개집니다. 그래서 마스터는 `ml` 답의 `answer_markdown` 을 본문에 그대로 붙이고
(`_MARKDOWN_AGENTS`), 화면은 `frontend/src/components/console/ml/Markdownish.tsx` 로
그립니다. 마크다운과 별개로 `item` · `forecasts[]` · `model_version` 같은 기계용 칸을
같이 싣는 것은, 마크다운을 못 쓰는 쪽도 답을 쓸 수 있게 하려는 것입니다.

`Markdownish` 는 제목 · 표 · 굵은 글씨 · 코드칸 · 목록 · 인용을 그립니다. ML 답이 쓰는
문법이 정확히 그만큼입니다. `dangerouslySetInnerHTML` 을 쓰지 않습니다 — AI 가 쓴 글이
화면으로 들어오는 통로라, 글자는 전부 React 가 글자로 넣습니다.

---

## 3. 질문 문장

마스터는 사람 말을 `payload["question"]` 에, 품목이 있으면 `payload["item"]` 에 싣습니다.
이름은 `question` 하나입니다 (마스터 확정). 여러 이름을 열어 두면 어느 것이 정본인지 못
정하고, 두 이름으로 다른 값이 오는 날 조용히 한쪽만 읽힙니다. `item` 의 어휘는 마스터
`ItemName`(배추·무·양파)과 ML `ITEMS` 가 같습니다.

```text
질문이 오면    해석해서 값·구간·오차·출처를 마크다운으로 답한다
안 오면        오늘 예측을 갖고 있는지와 무엇을 물으면 되는지를 답한다
```

빈 요청에 되묻지 않습니다. 그러면 조회할 때마다 "ML 이 답하지 못했다" 가 떠서
진짜 고장과 구분이 안 됩니다.

---

## 4. 답할 수 있는 범위

```text
품목   배추 · 무 · 양파
가격   경락가(AUC) · 중도매가(WHSL) · 소매가(RTL)
날짜   오늘 ~ 18일 뒤
질문   값이 얼마인가 · 얼마나 맞는가 · 믿고 써도 되는가
```

### 4-1. 갈래가 셋입니다

한 질문이 여럿을 물을 수 있습니다. 답은 물어본 차례대로 이어 붙입니다.

| 갈래 | 이런 질문 | 답하는 것 |
|---|---|---|
| `forecast` | 「5일 뒤 배추 경락가?」 | 예측 표 · 예상 구간 |
| `batch` | 「오늘 데이터 처리 잘 됐어?」 | 그날 배치 한 줄(상태·시각·단계 수) + 실패한 단계 + 그날 AI 점검 보고서 본문 그대로 |
| `perf` | 「모델 성능 어때?」 | 현재 모델 셋 + 봉인 개봉 성능 아홉 칸 + 그날 재학습 보고서 전부 |

```text
「오늘 상태 어때? 성능도」       → batch · perf
「5일 뒤 배추 경락가랑 배치 상태」 → forecast · batch
```

품목·가격 종류를 안 말하면 되묻지 않고 전부 답합니다. 품목이 없으면
배추·무·양파, 가격 종류가 없으면 경락가·중도매가·소매가, 둘 다 없으면 아홉 조합입니다.
날짜가 없으면 오늘 하나입니다. 무엇을 채웠는지 답 첫 줄에 한 줄로 밝힙니다.
질문 문장으로 오든 `item`·`kind` 값으로 오든 같은 규칙입니다 — 값으로 부르는 쪽은
프로그램이라 되물어도 다시 답할 수가 없습니다. 되묻기(`need_clarify`)는 해석기가
갈래조차 못 고른 때(`clarify`) 하나뿐입니다. 짝 물음(`asks`)이 오면 채우지 않습니다.
범위 밖 품목(마늘·대파)도 전부로 바꾸지 않고 그대로 거절합니다.

가격 답은 표만 냅니다. 「이 조합은 판단에 쓰지 마세요」 같은 경고 문장은 답에 넣지
않습니다. 값은 그대로 나갑니다 — `use_recommended` · `quality_note` · `is_gated` 가 `meta`
와 payload 에 실리고, 판단은 마스터가 그 값으로 합니다. 그래서 `use_recommended` 의 뜻은
«답에 든 조합을 전부 써도 되나» 입니다 — 하나라도 막혔으면 `false` 입니다. 첫 조합만
보면 「가격 알려줘」의 아홉 조합 가운데 막힌 조합(예: 양파 중도매가)이 조용히 묻힙니다.

해석기는 하나입니다. LLM 호출은 한 번이고, 그 한 번이 `routes` 목록을 돌려줍니다.
갈래를 나눠 읽는 것은 코드가 합니다.

한 갈래가 실패해도 나머지는 나갑니다. 못 읽은 갈래만 «…을 읽지 못했습니다» 한 줄로
말하고, 전체 상태는 `partial` 이 됩니다. 전부 못 읽었을 때만 `source_unavailable` 입니다.

배치·성능은 지나간 날을 묻습니다. 「어제 배치 상태」·「9월 10일 배치」가 그렇습니다 —
해석기 날짜 목록에 지난 30일이 있고, 날짜가 여럿이면 날마다 블록(최대 7일 · 넘으면
최근 7일만 쓰고 그렇게 말합니다)입니다.

기준일보다 뒤인 날은 안 보여줍니다. 「내일 배치 알려줘」에는 «배치 관련 정보는 기준일
또는 기준일보다 과거의 데이터만 조회 가능합니다.» 한 줄만 나갑니다 (재학습은 «재학습
관련 정보는…»). 지난 날이 없다고 오늘로 돌아가면 오늘 배치와 오늘 점검 보고서가 나가
틀린 답이 됩니다. 「어제랑 내일」처럼 섞이면 어제는 표로 답하고 그 한 줄을 맨 끝에 한 번
붙입니다. «아직 안 돌았다» 고 쓰지 않습니다 — 화면 기준일은 진짜 오늘보다 과거일 수
있어서(09-14 로 두면 09-15 기록은 DB 에 있는데도 앞날입니다) 그 말은 거짓이 될 수 있습니다.
날짜를 안 말한 배치는 오늘입니다. 「5일 뒤 배추 경락가랑 배치 상태」의 날짜는 해석기가
짝 물음(`asks`) 안에만 넣고 맨 위 `dates` 는 비웁니다 — 배치·성능이 보는 것은 맨 위
`dates` 뿐이라 여기까지 오지 않습니다.
그날 기록이 없는 것은 «고장» 이 아닙니다 — 답은 «기준일(2026-08-03)에 대한 배치
기록이 없습니다» 로 나가고, 봉투는 `READY` · `skipped` · `missing_data` 는 비어 있습니다.
표 이름은 못 읽었을 때만, 그것도 물어본 갈래의 표만 댑니다.

읽는 표 (전부 원본 창고 · `SELECT` 만): `batch_run` · `batch_run_stage` ·
`agent_report` · `prediction_log` · `model_cutover`. 성능 아홉 칸은 표가 아니라
상수입니다 (`app/ml/config.py` 의 `SEALED_ACCURACY`) — `prediction_log` 로 다시 재지
않습니다 (실험용 모델이 섞여 있습니다).

«현재 모델» 의 출처는 둘입니다 (`readmodel/qa_reads.current_models()`). 이름·만든 날은
`prediction_log` 의 최신 기준일 행(`model_ver` · `model_created_at`), 학습 끝·최근
교체는 `model_cutover` 의 `kind` 별 최신 행(`new_train_end` · `swapped_at` · `note`)
입니다. `model_cutover` 가 없어도 실패하지 않습니다 — 그때 «최근 교체» 칸에
«교체 이력 없음» 이라고 적습니다 (2026-09-16 실측: 두 창고 다 그 표가 없었습니다).

이름으로는 교체를 알 수 없습니다. `ops_auc` · `ops_whsl` · `ops_rtl` 은 모델을
갈아 끼워도 그대로 둡니다 — 매입 파트 필터가 이름 정확히 일치라 바꾸면 에러 없이
0건이 됩니다. 그래서 만든 날을 같이 적습니다.

교체 시각을 모를 수 있습니다. `model_cutover.time_known` 이 `false` 면 «최근 교체»
를 날짜만 + «(시각 미상)» 으로 적습니다. 되짚어 적은 기록은 백업 폴더 이름에서
날짜만 건진 것이 있어 시각이 `00:00` 으로 앉아 있고, 그대로 보이면 «한밤중에 바꿨나» 로
읽힙니다. 칸이 없으면(`None`) 날짜·시각을 적습니다 — «모른다» 와
«안 알려준다» 는 다릅니다. 긴 `note` 는 답에 안 적습니다 (원문은 DB 에 그대로).

재학습 보고서는 그날 것을 전부 읽습니다 (`agent_reports()`). 하루에 판정 3건 ·
검증 2건이 남는 날이 있고, 마지막 하나만 읽으면 경락가 검증의 «후보가 나쁩니다» 가
답에서 통째로 빠집니다. 판정은 한 줄 요약표, 검증은 비교표 전부입니다.
가격 종류는 `payload.kind` → 제목 맨 앞 낱말 → «(가격 종류 미상)» 순으로 가립니다.

시간대가 표마다 다릅니다. `batch_run.started_at` 은 UTC 라
`AT TIME ZONE 'Asia/Seoul'` 로 돌려 날짜를 자르고, `agent_report.ran_at` 은 이미
한국 시간이라 그대로 자릅니다. 한국 09:00 이 UTC 자정이라 안 돌리면 09:00 배치가
전날 것으로 세어집니다.

못 하는 것도 적습니다. 마늘·대파 같은 다른 품목, 19일 뒤 이상, 과거,
평균·합계 같은 집계, 「왜 오르나」, 사라 말라 판단. 범위 밖이면 `qa_status` 에
`OUT_OF_SCOPE` 로 적고 `READY` 로 답합니다 — 소관이 아닌 것은 고장이 아닙니다.

---

## 5. Evidence 등급은 `ASSUMED` 입니다

`HARD_ALLOWED_GRADES` 는 `OFFICIAL · VENDOR · SIM_FIXED` 인데, 예측은 관측이
아닙니다. ML 값으로 하드 제약을 세우면 모델이 틀리면 제약도 틀리는 제약이
됩니다. 그래서 일부러 하드 제약에 못 쓰는 등급으로 내보냅니다.

각 근거의 `ref_ids` 에는 읽은 표와 키를 그대로 적습니다.

```text
ml_price_forecasts:base_dt=2026-09-15,item=배추,kind=AUC,target_dt=2026-09-16
```

---

## 6. HTTP API 는 연결에 쓰지 않습니다

`/ml/qa` (GET·POST)는 시험용 입구입니다(`app/api/ml/qa.py`). 연결은 같은 프로세스 안에서
함수로 부르는 쪽입니다 — 다른 파트와 같고, 그래야 호출 예산·이력·봉투 검증이 한
줄기로 이어집니다.
