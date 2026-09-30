# `database/` — 스키마 파일과 실행 순서

> 마지막 갱신 2026-09-30 — 부서 폴더 · 객체(표 · 뷰)별 파일로 다시 나누고 새 DB 적용 목록을 세웠다
> (§0 · §1). 같은 날 보완: 새 DB 목록은 schema 파일만 담고, 각 schema 파일이 새 DB 의 최종 정의다
> (§1). 각 파일의 적용 상태는 §2 에 적힌 날짜 기준이다.

## 0. 폴더 구성 (2026-09-30)

```text
database/
├─ new_database_order.txt    새 DB 적용 순서 — `apply_sql.py --new-database` 가 이 순서로만 돈다
├─ schema/<부서>/<객체>.sql   지금 쓰는 구조 = 새 DB 의 최종 정의. 표(뷰) 하나에 파일 하나 — 칸 · 키 · 제약 · 인덱스 · 주석 · FK
├─ migrations/<부서>/         이미 쓰는 DB 를 옮기는 변경(ALTER 판 · 이관). 새 DB 목록에는 없다. 파일 이름은 이력이라 그대로
└─ seed/<부서>/               행 데이터 — 새 DB 목록에 없다. 사용자가 확인하고 직접 넣는다(seed/README.md)
```

| 부서 폴더 | 객체 |
|---|---|
| `schema/common/` | 스키마 `haetdeul`(`haetdeul_schema.sql`) · `sim_runs` · `company_personas` · `items` · `partners` · `evidences` · `agent_policy_config` · `agent_runs` |
| `schema/purchase/` | `purchases` · `purchase_items` · `market_quotes` · `auction_prices_daily` · `proposals` · `partner_item_demands` · 뷰 `v_current_partner_demand` |
| `schema/sales/` | `sales` · `sale_items` · `sales_agent_runs` |
| `schema/logistics/` | 재고 `inventory_lots` · `inventory_moves` · `item_storage_policies` · `logistics_runtime_fixture` · `logistics_contracts` · `deliveries`, WMS 표 22 · 뷰 2, Agent 표 3(`logistics_exceptions` · `logistics_investigations` · `logistics_action_proposals`), `logistics_agent_runs` |
| `schema/finance/` | `finance_states` · `daily_closings` · `expenses` · `payables` · `receivables` · `finance_payable_closing_events` · `finance_cash_adjustments` · `partner_credit_limits` · `finance_agent_runs` · `finance_agent_runs_v22` · 뷰 `v_current_finance_state` |
| `schema/ml/` | `ml_price_forecasts`(보관 함수 · 트리거 포함) · `ml_price_forecasts_history` · `forecasts` · `ml_calendar_days` · `ml_batch_day_status` · 뷰 `v_ml_price_forecast` · `v_ml_batch_days` |
| `schema/master/` | `orchestrator_agent_runs` · `master_agent_runs` · `master_decisions` · `master_collection_events` · `master_day_openings` · `master_purchase_records` |

- 부서 배정은 2026-09-30 정리안이다(표 주석 · 앱에서 쓰는 곳 기준). 옛 스냅샷의 표에는 정해진 주인이
  없었다(§5). **확인 필요**: `daily_closings`(재무 · 마스터 마감이 쓴다), `partner_item_demands` ·
  `v_current_partner_demand`(매입 · 판매 수요), `forecasts`(ML · 매입), `agent_runs`(옛 Agent 감사로그).
- 🔴 **폴더와 파일 이름은 순서가 아니다.** 새 DB 는 `new_database_order.txt` 순서로만 세운다(§1).
  파일 이름의 숫자 접두어(`00_` · `10_` · `30_` · `40_`, seed 의 `25_` · `27_`)는 뗐다.
- **SQL 문장은 그대로 옮겼다.** 옛 `10_domain_schema.sql`(pg_dump) · `30_logistics_wms_schema.sql` ·
  `40_logistics_agent_schema.sql` · `ml_calendar_days.sql` 은 문장 단위로 갈라 객체별 파일로 옮겼고
  (문장 711개가 원문과 같다), 파일마다 `BEGIN;` · `COMMIT;` 으로 감쌌다. 뺀 것은 pg_dump 의 세션
  설정(`SET` · `set_config`) · 중복 `CREATE SCHEMA` · psql 명령 `\restrict` · `\unrestrict` 뿐이다.
  옛 파일 전체와 머리말은 git `3525c8f3` 에 있다(비교 기준).
- 같은 날 BL-021 보완: 그 뒤 schema 파일 7개(`master_agent_runs` · `master_decisions` · `sales` ·
  `v_current_finance_state` · `finance_states` · `expenses` · `logistics_runtime_fixture`)에 새 DB 목록
  끝에서 돌던 이관판 8개의 효과를 담았다(§1). 이 파일들은 해당 문장이 스냅샷 · 옛 파일과 다르고,
  무엇을 담았는지 파일 머리말에 적었다.
- SQL 파일 안의 옛 경로 · 이름 표기(예: `psql -f database/<파일>`)는 본문을 보존하려고 고치지
  않았다. 예외는 실행되는 명령 한 줄 — `migrations/master/master_agent_runs_migration.sql` 의 `\i`
  를 `database/schema/master/master_agent_runs.sql` 로 고쳤다. 보완 때 이관판 8개 머리에 현재 안내
  주석 몇 줄을 더했다(실행 문장 그대로 · §1).
- 🔴 **파일을 옮겨도 DB 는 바뀌지 않는다.** 이미 적용한 파일을 옮겼다고 다시 돌릴 이유가 없다.
  적용 이력은 여전히 §2 의 문구뿐이다(이력 표 없음).

### 초기 구축에서 뺀 것 · 남긴 것 (2026-09-30)

옛 스냅샷에 있었지만 새 DB 목록에서 뺐다 — 앱 · 스크립트 · 테스트 · 다른 SQL 에서 쓰는 곳이 없고,
남는 객체가 FK · 뷰 · 트리거로 기대지도 않는다(저장소 전체 검색과 DB 의존 확인). 이미 있는 DB 에서는
지우지 않는다(DROP 판 없음). 정의는 git `3525c8f3:database/10_domain_schema.sql` 에 있다.

| 뺀 객체 | 무엇이었나 | 확인한 것 |
|---|---|---|
| `agent_policies` | Agent 판단 임계치 설정 | 쓰는 곳 없음 — 지금 쓰는 설정 표는 `agent_policy_config` |
| `constraint_reviews` | T2 병렬 제약 검토 결과 | 쓰는 곳 없음(이 문서 §5 의 옛 언급만) |
| `persona_evidences` | Persona 필드 ↔ 근거 연결 | 쓰는 곳 없음. 재무 정책 seed 의 설명 문구가 출처로 이름만 적는다 — **확인 필요**: 근거 연결을 새 DB 에도 둘지 |
| `v_current_inventory` · `v_current_logistics_capacity` | 현재 재고 · 5PL 여유 계산 뷰 | 쓰는 곳 없음. 코드가 이 뷰를 **안 쓴다**는 검사가 있다(`test_logistics_service_repository`) |
| `v_dashboard_state` | UI Mock 대체 회사 상태 뷰 | 쓰는 곳 없음(재무 뷰 이관 판 머리말의 옛 언급만) |
| `v_ml_forecast_revisions` | 예측 덮어쓰기 이력 요약 뷰 | 저장소 안에서 쓰는 곳 없음 — **확인 필요**: 저장소 밖 ML 배치 · 운영 도구가 읽는지는 확인하지 못했다 |
| `v_seed_validation` | 초기 seed 정합성 검사 뷰 | 쓰는 곳 없음 |

앱이 이름으로 부르지 않지만 **남긴 것** — 남는 객체가 기댄다.

| 남긴 객체 | 이유 |
|---|---|
| `agent_runs` | `proposals` 의 FK 가 가리킨다 |
| `deliveries` | `expenses` 의 FK 가 가리킨다 |
| `ml_price_forecasts_history` | `ml_price_forecasts` 의 트리거(`trg_ml_forecast_archive`)가 예측을 덮어쓸 때 옛 값을 이 표에 쌓는다 |

---

**검증 결과는 시점마다 다릅니다. 섞어 읽지 마십시오.**

| 시점 | 무엇을 세웠나 | 실측 결과 |
|---|---|---|
| 2026-08-30 | 빈 PostgreSQL 17 컨테이너 · 당시 §1 순서(`30_` **없음**) | **38표 · 8뷰** |
| 2026-09-05 | `30_logistics_wms_schema.sql` **하나가 만드는 물류 객체** | **21표 · 2뷰** |
| 2026-09-05 | 현재 §1 순서 전체(`30_` **포함**) | **59표 · 10뷰** |
| 2026-09-30 | 옛 §1 11개 파일(아래 §1 끝) | **64표 · 10뷰** |
| 2026-09-30 | 첫 정리의 `new_database_order.txt` 84개 파일(스키마 76 · 이관 판 8) | **69표 · 6뷰** |
| 2026-09-30 | 보완 뒤 `new_database_order.txt` 76개 파일(스키마만 · §1) | **69표 · 6뷰** — 84개로 세운 DB 와 카탈로그 같음 |

- **38표 · 8뷰는 2026-08-30 의 옛 결과입니다.** `30_` 추가 후의 총수가 아닙니다.
- 59표 · 10뷰는 §1 에 적힌 9개 파일을 임시 스키마에 실제로 세워 센 값입니다
  (2026-09-05 · 방법은 §6). §1 에 없는 파일(`master_agent_runs.sql` ·
  `ml_calendar_days.sql` 등)은 여기 포함되지 않습니다 — 그래서 공유 DB 의
  62표 · 11뷰와 다릅니다.
- `30_logistics_wms_schema.sql` 이 만드는 21표 · 2뷰는 실 DB 카탈로그와
  컬럼·타입·NULL·DEFAULT·제약·인덱스·주석·뷰정의를 1:1 대조해 **완전히 같음**을
  확인했습니다.
- 2026-09-30 세 줄은 임시 PostgreSQL 17.11 컨테이너의 빈 DB 에 세워 센 값입니다(공유 DB 에는 붙지
  않음 · seed 없음). 64표 · 10뷰와 공유 DB 의 수(2026-09-05 62표 · 11뷰)는 세운 파일이 달라 바로
  비교하지 않습니다.

🔴 **옛 `30_logistics_wms_schema.sql`(지금 `schema/logistics/` 의 WMS 파일들)은 공유 외부
DB(`haetdeul`)에 이미 존재하는 WMS 구조를 저장소로 회수한 것이며, 그 DB 에 다시 적용하는 것이
목적이 아닙니다.** 목적은 *저장소만으로 같은 스키마를 세울 수 있게 하는 것*입니다 — 공유 DB 는
이미 이 모양이라 돌릴 이유가 없고, 검증도 임시 스키마에서만 했습니다.

---

## 1. 새 DB 를 세울 때 — `new_database_order.txt` 순서로

```bash
cd backend
.venv/Scripts/python.exe scripts/apply_sql.py --new-database          # 목록만 본다 (DB 에 안 붙는다)
.venv/Scripts/python.exe scripts/apply_sql.py --new-database --apply  # backend/.env 의 DB 에 세운다
```

- 목록에 적힌 순서대로 한 파일씩 적용하고, **한 파일이라도 실패하면 거기서 멈추고** 실패 종료
  코드를 낸다.
- 🔴 대상 DB 의 `haetdeul` 에 객체가 하나라도 있으면 **아무것도 안 하고 멈춘다** — 이미 쓰는 DB 는
  §2 로.
- **목록에는 `schema/` 파일 76개만 있다.** 각 파일이 새 DB 에서 그 표(뷰)의 최종 정의다 — 표 파일
  하나만 읽으면 새 DB 의 칸 · NULL 허용 · 기본값 · 제약 · 인덱스 · 주석을 알 수 있다. `migrations/` 는
  이미 쓰는 DB 를 갱신할 때만 쓴다(§2).
- 순서는 참조로 정했다 — FK · 뷰 · 함수가 가리키는 객체를 만드는 파일이 먼저 온다. 스키마 파일을
  더하면 목록에도 넣는다. `backend/tests/scripts/test_new_database_order.py` 가 빠진 파일 · 순서
  위반 · seed 포함 · `schema/` 밖 파일 · 숫자 접두어를 잰다.
- **새 DB 를 세우는 데 psql 은 필요 없다.** 목록의 파일에 psql 명령 줄(`\i` 등)이 없어 `apply_sql.py`
  가 전부 psycopg 로 보낸다(2026-09-30 psql 이 없는 Windows 호스트에서 확인). psql 이 필요한 파일은
  기존 DB 용 이관판 하나다(§2).

### 목록 끝에서 돌던 이관판 8개 — 효과를 schema 파일에 담았다 (2026-09-30 BL-021 보완)

같은 날 첫 정리 때는 목록 끝의 이관판 8개가 앞선 schema 파일을 뒤에서 고쳤다. 그러면 표 파일 하나만
읽어서는 새 DB 의 최종 모양을 알 수 없어서, 각 효과를 해당 schema 파일에 담고 목록에서 뺐다. 이관판은
지우지 않았다 — 이미 쓰는 DB 는 여전히 그 파일로 간다(§2).

| 이관판 | 새 DB 에 남는 효과 | 담은 schema 파일 |
|---|---|---|
| `migrations/master/master_agent_runs_sim_run_id.sql` | `sim_run_id TEXT NULL`(맨 뒤 칸 · 기본값 없음) · 부분 인덱스 `idx_master_agent_runs_sim_run_as_of` · 칸 주석 | `schema/master/master_agent_runs.sql` |
| `migrations/master/master_agent_runs_migration.sql` | 결정 → 실행 FK `master_decisions_run_fk` 가 `master_agent_runs(run_id, request_id)` 를 가리킨다(ON DELETE RESTRICT). 행 이관(INSERT)과 행 수 확인은 기존 DB 에만 뜻이 있어 옮기지 않았다 | `schema/master/master_decisions.sql` |
| `migrations/master/master_decision_revalidation.sql` | 부분 인덱스 `idx_master_decisions_revalidation_request_id` · 두 칸 주석. 칸 둘과 CHECK 둘은 본 DDL 에 이미 있었다 | `schema/master/master_decisions.sql` |
| `migrations/sales/sales_source_order_id_nullable.sql` | `sales.source_order_id` NOT NULL 해제 | `schema/sales/sales.sql` |
| `migrations/finance/finance_current_state_view.sql` | `v_current_finance_state` 본문(`sim_runs` 가 정한 축의 가장 늦은 상태)과 뷰 주석 — 스냅샷 판은 `FIN-DAY30-LOAN` 한 행에 묶여 있었다 | `schema/finance/v_current_finance_state.sql` |
| `migrations/finance/finance_state_as_of_index.sql` | 인덱스 `ix_finance_states_as_of` | `schema/finance/finance_states.sql` |
| `migrations/finance/expense_lifecycle.sql` | 인덱스 `expenses_projection_axis_idx` · `expenses_paid_axis_idx`. 칸 셋과 칸 주석은 본 DDL 에 이미 같은 모양으로 있었다 | `schema/finance/expenses.sql` |
| `migrations/logistics/logistics_drop_inbound_json.sql` | 입고 JSON 두 칸(`in_transit_json` · `confirmed_inbound_json`)이 없다 — 만들었다 지우지 않고 처음부터 뺐다. 상태 두 칸 주석 | `schema/logistics/logistics_runtime_fixture.sql` |

- 기존 DB 의 행을 옮기는 문장(INSERT · 행 수 확인)은 새 DB 의 CREATE 로 옮기지 않았다. 새 칸 · 정책 ·
  업무 동작은 더하지 않았다 — 보완 전후 목록으로 세운 DB 의 구조가 같다(아래).
- 이관판 8개 머리에 «새 DB 는 이 파일을 적용하지 않는다 · 최종 모양이 있는 schema 파일 · 아래 머리말의
  신규 구축 안내는 작성 당시 기준» 주석을 붙였다. 실행 문장은 그대로다.

- 스키마 파일을 다시 돌리면: 옛 스냅샷에서 나눈 파일 33개와 `master/master_purchase_records.sql` 은
  `IF NOT EXISTS` 가 없어 «이미 있다» 로 멈춥니다 — 파일마다 `BEGIN;` · `COMMIT;` 이라 멈춘 파일은
  아무것도 바꾸지 않습니다. 나머지 42개는 멱등입니다(2026-09-30 확인 · 보완 뒤 다시 확인).
- 이관판(§2)은 새 DB 목록에 넣지 않습니다. 23개 모두를 새 DB 에 하나씩 얹어 보면 21개는 바뀌는 것이
  없습니다(보완 뒤 확인). 예외 둘 — `logistics_exceptions_detection_history` 는 칸 주석 문구가 본 DDL 과
  조금 다르고, `logistics_inbound_schedules` 는 입고 JSON 칸이 없어 오류로 멈춥니다.
- 🔴 **seed 는 넣지 않습니다.** 정책 · fixture · 데모 데이터는 사용자가 대상 DB 를 확인하고 직접
  넣습니다 — `seed/README.md`.

2026-09-30 보완 확인 (임시 PostgreSQL 17.11 컨테이너 · 공유 DB 에는 붙지 않음 · seed 없음):

- 보완 전 목록 84개와 보완 뒤 목록 76개를 각각 새 빈 DB 에 psql 로 세웠고, 두 DB 의 카탈로그 1,683줄이
  같습니다 — 표 · 칸(논리 순서 · 타입 · NULL 허용 · 기본값 · 생성식) · 기본 키 · 외래 키(참조 대상 ·
  ON DELETE) · CHECK · UNIQUE · 인덱스 · 뷰 정의 · 함수 · 트리거 · 주석.
- 칸의 물리 번호(`attnum`)만 한 표에서 다릅니다 — 보완 전 경로는 `logistics_runtime_fixture` 에 입고
  JSON 두 칸을 만들었다 지워 빈 번호가 남고, 지금은 처음부터 없습니다. 칸 이름 · 순서 · 정의는 같습니다.
- 보완 뒤 목록을 `apply_sql.py --new-database --apply` 로 psql 이 없는 Windows 호스트에서 새 빈 DB 에
  세웠고(76개 모두 psycopg) 카탈로그가 psql 로 세운 DB 와 같습니다. 다시 주면 «빈 DB 가 아니다» 로
  멈춥니다. 목록 3번째에 실패하는 파일을 넣은 스크래치 사본으로 돌리면 그 파일에서 멈추고 뒤 파일은
  적용되지 않습니다.
- 마스터 실행을 가리키는 결정 한 건은 들어가고, `orchestrator_agent_runs` 행을 가리키는 결정은 FK 로
  막힙니다.
- `db` 마커 검사: 보완 전 트리를 보완 전 목록 DB 에, 지금 트리를 보완 뒤 목록 DB 에 붙여 각 438건 — 둘 다
  14 failed · 424 passed 이고 실패 ID · 첫 원인이 같습니다(빈 DB 라 데이터 · 원본 스키마가 없는 12건 등).
  물류 `db` 검사 10개 파일 422건은 두 트리 모두 세운 DB · 빈 DB 에서 통과합니다.

첫 정리 확인 (같은 날 · 보완 전 · 목록 84개 — 기록으로 둔다):

- 목록 84개(스키마 76 · 이관 판 8)를 psql 로 한 번, `apply_sql.py --new-database --apply` 로 한 번
  각각 새 빈 DB 에 세워 둘 다 성공했고 카탈로그가 같습니다.
- 뺀 8개까지 넣어 세운 DB 는 옛 파일 이름 그대로 짠 25개 목록으로 세운 DB 와 카탈로그 1,790줄 ·
  칸 순서까지 같습니다 — 파일 배치로 정의가 바뀌지 않았습니다. 목록으로 세운 DB 와의 차이 108줄은
  전부 뺀 8개입니다.
- 앱이 이름으로 부르는 표 · 뷰 54개가 모두 있습니다. 마스터 실행을 가리키는 결정 한 건을 넣어 보면
  통과합니다(옛 §1 에 `master_agent_runs` 만 더한 DB 는 FK 로 막힘).
- 이미 세운 DB 에 다시 `--new-database --apply` 를 주면 «빈 DB 가 아니다» 로 멈춥니다.
- 물류 `db` 검사 10개 파일(422건)이 새 빈 DB 와 목록으로 세운 DB 에서 모두 통과합니다.

> 옛 §1 (2026-09-08 까지 · 11개): `00_init_schema` → `10_domain_schema` → `30_logistics_wms_schema` →
> `40_logistics_agent_schema` → `orchestrator_agent_runs` · `*_agent_runs` · `master_decisions` →
> `logistics_drop_inbound_json`. 이 순서로 세운 DB 에는 코드가 쓰는 객체 8개(`master_agent_runs` ·
> `master_day_openings` · `master_purchase_records` · `master_collection_events` · `ml_calendar_days` ·
> `v_ml_batch_days` · `finance_cash_adjustments` · `partner_credit_limits`)가 없었습니다(2026-09-30 확인).

---

## 2. 이미 데이터가 있는 DB 를 옮길 때 — 위 파일을 쓰지 않습니다

`CREATE TABLE IF NOT EXISTS` 는 **이미 있는 표를 고치지 않습니다.** 그래서 운영
중인 DB 에는 본 DDL 이 아니라 **ALTER 판**을 씁니다. 한 파일씩, 먼저 보기만 한 뒤 적용합니다.

```bash
cd backend
.venv/Scripts/python.exe scripts/apply_sql.py database/migrations/<부서>/<파일>.sql          # 보기만
.venv/Scripts/python.exe scripts/apply_sql.py database/migrations/<부서>/<파일>.sql --apply  # 적용
```

**새 DB 목록에서 뺀 이관판 8개(§1 표)도 이미 쓰는 DB 에는 그대로 이 파일들로 갑니다.** 효과를 schema
파일에 담은 것은 새 DB 용이고, 기존 DB 를 바꾸지 않았습니다. 아래 표에 적용 기록이 있는 것은
`logistics_drop_inbound_json.sql`(적용 대기) 하나이고 나머지 7개는 이 README 에 적용 기록이 없습니다 —
**확인 필요**: 적용하기 전에 대상 DB 에 그 칸 · 인덱스 · 제약 · 뷰 정의가 이미 있는지 먼저 봅니다.

**psql 이 필요한 파일이 하나 있습니다** — `migrations/master/master_agent_runs_migration.sql` 의 `\i`
(psql 명령). `apply_sql.py` 는 이런 파일만 `psql -X -w -v ON_ERROR_STOP=1 -f` 로 저장소 루트에서
돌리고(psql 이 실패하면 스크립트도 그 종료 코드로 실패), 나머지는 psycopg 로 보냅니다. 그래서 그 파일을
적용하는 자리에는 **PATH 에 `psql`** 이 있어야 합니다(`\i` 는 모든 판이 알고, 2026-09-30 확인은
psql 17.11). 비밀번호는 `PGPASSWORD` 로, 파일 인코딩은 `PGCLIENTENCODING=UTF8` 로 넘깁니다. 옛
pg_dump 원본(git `3525c8f3`)을 psql 로 다시 돌리려면 `\restrict` 를 아는 psql 17.6 · 16.10 이상이
필요합니다(PostgreSQL 릴리스 노트 CVE-2025-8714 — 15 이하 판은 확인하지 않았습니다).

| 이관 스크립트 | 무엇을 바꾸나 | 언제 |
|---|---|---|
| `migrations/master/master_runs_migration.sql` | `orchestrator_agent_runs` 에 `agent='master'` · `request_id` · `plan` | 2026-08-27 |
| `migrations/master/master_decisions_run_id.sql` | `master_decisions.run_id` + 복합 FK + 인덱스 | 2026-08-30 · **팀 승인 대기** |
| `migrations/ml/ml_forecast_view_gate_reason.sql` | `v_ml_price_forecast` 의 `daily[]` 에 `gate_reason` 추가 | 2026-09-03 · **실 DB 적용 대기** |
| `migrations/finance/finance_state_daily_unique.sql` | `finance_states` 의 `UNIQUE(sim_run_id, financing_mode, state_date)` · 적용 전 duplicate preflight · 기존 데이터 자동 정리 없음 | 2026-09-05 · **실 DB 적용 대기** |
| `migrations/finance/partner_credit_limits_recorded_by.sql` | `partner_credit_limits` 에 입력자 감사 칸 추가. 기존 금액·기간은 유지하고 과거 행의 복원 불가능한 입력자는 `LEGACY_UNKNOWN` 으로 명시 | 2026-09-16 · **실 DB 적용 대기** |
| `migrations/finance/payable_cancellation.sql` | `payables` 의 `CANCELLED` · 취소금액/취소일 · 지급/취소/미지급 금액 항등식. 기존 행은 취소금액 0, 자동 상태 변경·삭제 없음 | 2026-09-05 · **실 DB 적용 대기** |
| `schema/logistics/` 의 WMS 파일 24개(옛 `30_logistics_wms_schema.sql` · 2026-09-30 표별로 나눔) | 물류 WMS 표 21 · 뷰 2 회수 + `inventory_lots` 컬럼 6·제약 5 · `inventory_moves` UNIQUE 1 | 2026-09-05 · **실 DB 에는 이미 있음**(회수) · 신규 구축 DB 에는 필수 |
| `migrations/logistics/logistics_inventory_lots_nullable.sql` | `inventory_lots.grade` · `derivation_status` NOT NULL 해제. **기존 행 값 변경 없음.** 정상 실입고가 미확정 등급과 비-Burn-in 상태를 NULL 로 표현할 수 있게 함 | 2026-09-05 · **실 DB 적용됨**(2026-09-08 실측 확인) |
| `schema/master/master_collection_events.sql` | 수금 사건 표 `master_collection_events` 신설. **새 표 하나뿐이라 기존 표를 안 건드린다** — 적용 전후 `receivables` 15행 그대로. 표는 **비어서** 나간다 | 2026-09-08 · **실 DB 적용됨** |
| `migrations/logistics/logistics_allocation_basis_fefo_auto.sql` | `inventory_allocations.allocation_basis` 에 `FEFO_AUTO_SELECTED` 추가 (시뮬레이션 자동 FEFO 할당). **어휘를 넓히기만 한다** — 기존 `FEFO_TOOL_CONFIRMED` · `HUMAN_OVERRIDE` 행은 그대로 유효하고 행 변경이 없다. CHECK 를 넓히려면 `DROP` 이 불가피해 한 트랜잭션 안에서 지웠다 다시 건다 | 2026-09-08 · **실 DB 적용됨**(2026-09-08 실측 확인) |
| `migrations/logistics/logistics_drop_inbound_json.sql` | `logistics_runtime_fixture` 에서 `in_transit_json` · `confirmed_inbound_json` 두 칸 DROP. 입고 예정의 정본이 `inbound_schedules` 로 옮겨 가(W3-2 Reader · W3-3 Writer) **두 칸을 읽는 곳도 쓰는 곳도 0** 이 된 뒤의 정리다. 🔴 **status 두 칸은 안 걷는다** — 두 Reader 가 `UNRESOLVED` 를 실제로 읽는다. `confirmed_outbound_*` 도 안 건드린다(WP-3). 적용 전 확인 넷이 파일 머리말에 있다 | 2026-09-09 · **실 DB 적용 대기** |
| `migrations/logistics/logistics_reservation_released_as_of.sql` | `inventory_reservations` 에 `released_as_of DATE` 한 칸 추가 (WP-3 M3). **additive 다** — 기존 칸을 안 바꾸고 안 지운다. Backfill 없음(놓아준 날짜를 되살릴 근거가 없다 · 실측 8행 전부 `ALLOCATED` 라 `NULL` 이 사실이다). CHECK · INDEX 도 안 건다 — 규율은 `outbound.release_reservation` 의 필수 인자가 지킨다. 🔴 이 칸이 없으면 예약 조회 SQL 이 `column does not exist` 로 깨진다 (배포 선행조건) | 2026-09-09 · **실 DB 적용 대기** |
| `schema/logistics/logistics_exceptions.sql` · `logistics_investigations.sql` · `logistics_action_proposals.sql`(옛 `40_logistics_agent_schema.sql`) | 재고·물류 Agent Core 표 **셋** — `logistics_exceptions`(#628 Commit 2) · `logistics_investigations`(Commit 7) · `logistics_action_proposals`(Commit 5 + Commit 6 실행 축). **기존 표를 안 건드린다** — 남의 표에 하는 일은 `sim_runs` 를 FK 로 가리키는 것뿐이고 **DROP 은 0 줄**이다. 표는 **비어서** 나간다. 🔴 `logistics_exceptions` 가 없으면 걷기의 물류 점검 두 칸이 `FAILED` 한 줄을 남기고 나머지 열세 칸은 그대로 돈다 (하루가 안 죽는다) | 2026-09-12 · 2026-09-13 갱신 · **실 DB 적용 대기** |
| `migrations/logistics/logistics_inbound_schedules.sql` | 입고 예정 표 `inbound_schedules` 신설 + 살아 있는 `in_transit_json` 5건 Backfill. **기존 표를 안 건드린다** — `logistics_runtime_fixture` 에 UPDATE 도 DELETE 도 없고 적용 전후 254행·in_transit 5건 그대로. `purchase_item_id` FK 는 `purchases` CASCADE 삭제 정책이 정해질 때까지 보류. 두 번 돌려도 5건 (`WHERE NOT EXISTS`) | 2026-09-09 · **실 DB 적용됨**(2026-09-09 실측 확인) |

### ⚠️ 옛 `30_logistics_wms_schema.sql` 은 판을 나누지 않았습니다 (2026-09-05 기록)

이 파일 하나가 **신규 구축과 기존 DB 양쪽**을 겸합니다. 나눌 수 없어서입니다 —
두 방향의 의존이 얽혀 있습니다.

```text
uq_inventory_moves_id_lot (기존 표 ALTER)  →  inventory_move_lines (신규) 가 참조
inbound_receipts (신규)                    →  inventory_lots FK (기존 표 ALTER) 가 참조
```

그래서 **의존 순서대로 한 파일에 담고, 모든 문장을 멱등·가산으로만** 썼습니다
(`CREATE TABLE IF NOT EXISTS` · `ADD COLUMN IF NOT EXISTS` ·
`CREATE INDEX IF NOT EXISTS` · 제약은 `pg_constraint` 조회로 감쌈). `DROP` 이
한 줄도 없습니다. 위 §2 규칙이 막으려던 것(두 판이 조용히 갈리는 것)은 **판이
하나라 성립하지 않습니다.**

🔴 **`10_domain_schema.sql` 을 고치지 않았습니다.** `inventory_lots` ·
`inventory_moves` 는 물류 소유지만 그 파일은 여러 파트의 표가 한 덩어리인 pg_dump
스냅샷이라, 물류 변경을 거기 섞으면 같은 변경이 두 곳으로 갈립니다. 대신 30\_ 이
`10_domain` 뒤에서 ALTER 로 더합니다 — 신규 구축도 이 경로를 지납니다.

**같은 변경이 두 곳에 있습니다** — 본 DDL(신규 구축용)과 ALTER 판(이관용).
어느 하나만 고치면 갈립니다. **둘 다 고칩니다.**

🔴 **적어 두는 것만으로는 안 지켜집니다.** 갈려도 어느 쪽도 에러를 안 내고
**서로 다른 스키마가 조용히 생깁니다.** 그래서 검사로 겁니다 —
`backend/tests/master/test_schema_files_agree.py` 가 두 파일의 뷰 본문을 대조합니다.

> **2026-09-30 표별로 나눈 뒤.** WMS · Agent 표 파일은 전부 멱등이라 이미 쓰는 DB 에도 그대로
> 돌릴 수 있습니다. 🔴 단 옛 `30_` 의 `inventory_lots` · `inventory_moves` 보강 문장은 이제 그 표
> 파일(`schema/logistics/inventory_lots.sql` · `inventory_moves.sql`) 안에서 스냅샷의
> `CREATE TABLE`(`IF NOT EXISTS` 없음) 뒤에 있어, 파일을 통째로 기존 DB 에 돌리면 첫 문장에서
> 멈춥니다(`BEGIN` · `COMMIT` 이라 바뀌는 것 없음). 그 보강만 기존 DB 에 필요하면 옛 `30_`
> (git `3525c8f3`)을 씁니다 — 공유 DB 에는 이미 있습니다(회수).

---

## 3. 파일별 소유

| 파일 | 소유 | 비고 |
|---|---|---|
| `schema/common/haetdeul_schema.sql` | 공통 | 스키마 생성만 |
| 옛 `10_domain_schema.sql` 에서 나눈 `schema/<부서>/` 파일 33개 | ⚠️ 2026-09-30 부서 배정(§0 · 확인 필요) | 옛 스냅샷에는 주인이 없었다 — 아래 §5 |
| `schema/master/orchestrator_agent_runs.sql` | 마스터 | 오케·Critic·마스터가 **공유**하는 실행이력 |
| `schema/master/master_decisions.sql` | 마스터 | 사람의 결정. append-only |
| `migrations/master/master_decisions_run_id.sql` | 마스터 | 위의 ALTER 판 |
| `migrations/master/master_runs_migration.sql` | 마스터 | 2026-08-27 ALTER 판 |
| `schema/master/master_collection_events.sql` | 마스터 | 수금 사건. **자리만 만들고 시드가 없다** — 무엇을 사실로 둘지는 팀 결정이고, 재무가 "due_date 경과를 수금으로 읽지 않는다" 로 그은 선이 그 이유다 |
| `schema/finance/finance_agent_runs*.sql` · `migrations/finance/finance_agent_runs_v22_sales_validation.sql` | 재무 | |
| `schema/logistics/logistics_agent_runs.sql` | 물류 | |
| `schema/logistics/` 의 WMS 파일 24개(옛 `30_logistics_wms_schema.sql`) | **물류** | WMS 표 21 · 뷰 2 회수 (2026-09-04 cutover 분). 다른 파트 표는 FK 로 가리키기만 한다 |
| `migrations/logistics/logistics_inventory_lots_nullable.sql` | **물류** | 재고·물류 동작을 바꾸는 변경이라 물류가 낸다. 대상 표(`inventory_lots`)의 본 DDL 은 `10_domain_schema.sql` 안에 있고 그 파일은 여전히 주인이 없다(§5) |
| `migrations/logistics/logistics_allocation_basis_fefo_auto.sql` | **물류** | 위 `30_` 의 ALTER 판. 값 이름은 Master ↔ Logistics 합의 어휘이고, 표와 제약의 주인은 물류다 |
| `migrations/logistics/logistics_drop_inbound_json.sql` | **물류** | 죽은 입고 JSON 두 칸 정리 (W3-4). `10_domain_schema.sql` 이 만든 칸을 걷는 판이라 그 파일을 안 고친다(§5) — `30_` 이 같은 자리에서 ALTER 로 더하는 것과 같은 규율. `30_` 에 안 넣은 이유는 그 파일이 *"DROP 이 한 줄도 없다"* 를 계약으로 적었기 때문이다. 2026-09-30 BL-021 보완부터 새 DB 는 `schema/logistics/logistics_runtime_fixture.sql` 이 두 칸을 처음부터 만들지 않는다 — 이 판은 이미 쓰는 DB 용이다 |
| `migrations/logistics/logistics_reservation_released_as_of.sql` | **물류** | `30_` §6 의 이관 판. *"이 예약을 언제 놓아줬나"* 를 적을 칸 하나 — 종전에는 `updated_at`(벽시각)으로 과거 예약 상태를 추정할 수밖에 없었고, 그러면 같은 데이터가 내일 다른 과거를 낸다 (WP-3 M3) |
| `migrations/logistics/logistics_inbound_schedules.sql` | **물류** | `30_` §3-0 의 이관 판 + Backfill. 입고 예정을 날짜별 fixture JSON 에서 꺼내 **날짜에 안 묶인 업무 Entity** 로 세운다 (W3-1). 🔴 적용 당시(W3-1)에는 **정본을 안 바꿨다** — Reader 는 JSON, Writer 만 양쪽. 그 뒤 W3-2 가 Reader 를, W3-3 이 Writer 를 옮겼다. **신규 구축에서는 안 돌린다**(§1) |
| `schema/logistics/logistics_exceptions.sql` · `logistics_investigations.sql` · `logistics_action_proposals.sql`(옛 `40_logistics_agent_schema.sql`) | **물류** | 재고·물류 Agent Core 의 표 셋 — 운영 Exception · 조사 실행 기록 · 대응안. 결정론 탐지기와 조사·제안 서비스가 쓰고 (`app/logistics/agent/`), 마스터는 점검 트랜잭션만 진다 (`app/master/inspection.py`). ⚠️ `logistics_agent_runs`(Logistics API Request/Response 이력)와 **다른 표다** — 조사 기록은 `logistics_investigations` 다 |
| `seed/logistics/logistics_runtime_fixture_20260102.sql` | **물류** | 물류가 만들고 물류가 채운다. 런타임 fixture 씨앗 행. 다른 파트는 읽기만 |
| `seed/logistics/logistics_runtime_fixture_20260105_20260106.sql` | **물류** | 물류가 만들고 물류가 채운다. 런타임 fixture 씨앗 행 · 관통 실행일 쌍. 다른 파트는 읽기만 |
| `schema/sales/sales_agent_runs.sql` | 판매 | |
| `schema/ml/ml_calendar_days.sql` · `ml_batch_day_status.sql` · `v_ml_batch_days.sql` | **ML** | ML 이 만들고 ML 이 채운다. 조사일·경매일·공휴일 달력 + `v_ml_batch_days`. 다른 파트는 읽기만 |
| `seed/demo/mvp_demo_remove_dried_pepper.sql` | 데모 | 스키마가 아니라 데이터 정리 |
| `seed/demo/mvp_demo_remove_pimanul.sql` | 데모 | 스키마가 아니라 데이터 정리 · #216 피마늘 제외 · 실 DB 적용 대기 |

---

## 4. `orchestrator_agent_runs` 를 왜 안 나누나

**오케스트레이터가 마스터 에이전트가 됐지만 표는 그대로 둡니다.** 2026-08-27
DDL 에 이미 적힌 판단이고, 지금도 유효합니다.

```text
agent=master        121건    ← 지금 쓰는 것
agent=orchestrator   21건    ← 8/25 까지의 과거 실행
agent=critic          7건
```

**나누면 안 되는 이유 셋.**

**① 모양이 같습니다.** 마스터의 한 실행도 *"API 한 번 · 요청/응답 원문"* 입니다.
표를 나누면 *"그날 무슨 일이 있었나"* 를 두 곳에서 합쳐 봐야 합니다.

**② 과거를 다시 쓰게 됩니다.** `agent='orchestrator'` 21행은 **그때 실제로 있었던
일**입니다. 표를 옮기거나 이름을 바꾸면 과거 행의 판정을 나중에 바꾸는 셈입니다.

**③ 축이 이미 있습니다.** `agent` 컬럼이 셋을 구분합니다. 나눌 이유가 컬럼 하나로
이미 해결돼 있습니다.

### 이름은 바꾸지 않기를 권합니다

`orchestrator_agent_runs` 라는 이름이 이제 안 맞아 보이지만, 실제로는 **"에이전트
실행 공용 로그"** 입니다. 이름을 바꾸면 145행·모듈 4개·이 파일들이 전부 따라오고,
얻는 것은 이름뿐입니다. 대신 표 COMMENT 로 무엇인지 밝혀 두는 쪽이 쌉니다.

**남는 것은 코드 위치 하나입니다** — `app/master/persistence.py` 가
`app/orchestrator/run_repository.py` 를 임포트합니다. 이건 **표 문제가 아니라
모듈 배치 문제**라, 옮기고 싶으면 DB 를 건드리지 않고 옮길 수 있습니다.

---

## 5. 🔴 `10_domain_schema.sql` 은 사람이 쓴 것이 아닙니다

> 2026-09-30: 이 스냅샷을 객체별 파일로 나눴습니다(§0 — 문장은 그대로). 아래는 스냅샷을 뜰 때의
> 기록이고, 나눈 파일에도 그대로 해당합니다 — 설계 의도가 주석에 없습니다. 쓰는 곳도 기대는
> 객체도 없는 8개는 새 DB 목록에서 뺐습니다(§0). 같은 날 보완으로 나눈 파일 중 5개(`sales` ·
> `v_current_finance_state` · `finance_states` · `expenses` · `logistics_runtime_fixture`)는 이관판
> 효과를 담아 스냅샷과 다릅니다(§1).

2026-08-30 이전까지 `database/` 가 덮는 것은 **6개뿐**이었습니다. `items` ·
`partners` · `sales` · `inventory_lots` · `item_storage_policies` ·
`ml_price_forecasts` … **32표와 뷰 8개는 저장소에 없었습니다.** 본 DB 를 이
저장소만으로 세울 수 없는 상태였습니다.

그 빈자리를 메우려고 **살아 있는 test DB 에서 `pg_dump` 로 떴습니다.**

**본 DB 를 세우기 전에 각 파트가 자기 표를 봐야 합니다.**

- 지금 안 쓰는 표가 섞여 있을 수 있습니다 — `agent_runs` · `sim_runs` ·
  `constraint_reviews` · `finance_agent_runs_v22` 는 이름이 겹치거나 옛것으로
  보입니다. **판단은 각 파트 몫입니다.**
- test DB 에서 손으로 바꾼 `CHECK` · `DEFAULT` 가 있으면 그대로 따라옵니다.
- 설계 의도가 주석에 없습니다. 다른 파일들과 달리 **왜 그런지가 안 적혀 있습니다.**

---

## 6. 검증하는 법

빈 컨테이너에 세워 보는 것이 가장 확실합니다. 공용 DB 를 건드리지 않습니다.

```bash
# 저장소 루트에서 — 저장소를 읽기 전용으로 붙인다(`\i` 가 저장소 루트 기준 경로를 읽는다)
docker run -d --name ddl-check -e POSTGRES_PASSWORD=x -v "$PWD":/repo:ro postgres:17
```

```bash
grep -v -e '^#' -e '^$' database/new_database_order.txt | while read -r f; do
  docker exec -w /repo ddl-check psql -U postgres -X -q -v ON_ERROR_STOP=1 -f "$f" > /dev/null \
    || { echo "실패: $f"; break; }
done
docker exec ddl-check psql -U postgres -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='haetdeul'"
```

```bash
docker rm -f ddl-check
```

### 컨테이너를 못 쓸 때 — 임시 스키마에 세워 보고 되돌립니다

옛 `30_logistics_wms_schema.sql` 은 이 방법으로 검증했습니다(2026-09-05 · 아래 파일 이름은 그때 것 —
지금은 `schema/logistics/` 의 표 파일들이고, 물류 `db` 검사가 같은 방식으로 임시 스키마에 세운다:
`backend/tests/logistics/logistics_schema_files.py`). 스크립트의
`haetdeul.` 을 임시 스키마 이름으로 바꿔 한 트랜잭션 안에서 세우고, 실 DB 카탈로그와
대조한 뒤 `ROLLBACK` 합니다.

```text
① 임시 스키마 + 다른 도메인 PK-only stub (items · partners · sim_runs ·
   purchase_items · sales · sale_items)
② inventory_lots · inventory_moves 는 **저장소 10_domain 원문**으로 세운다
   → 30_ 의 ALTER 가 실제로 실행되고 "신규 구축 = 실 DB" 가 증명된다
③ 대상 스크립트 실행 → 실 DB 와 컬럼·타입·NULL·DEFAULT·제약·인덱스·주석·뷰 대조
④ 한 번 더 실행해 멱등 확인
⑤ ROLLBACK
```

🔴 **`haetdeul` 을 건드리지 않습니다** — 이름을 바꿔 돌리므로 FK 가 `haetdeul` 표를
가리키지 않고, 따라서 잠금도 걸리지 않습니다. `haetdeul` 은 **세는 데만** 읽습니다.

**총수(59표 · 10뷰)도 같은 방법으로 쟀습니다** — §1 의 9개 파일을 순서대로 임시
스키마에 세우고 `pg_class` 를 센 뒤 `ROLLBACK` 했습니다. 상단 표의 숫자는 전부
이렇게 실측한 값이며, 계산으로 더한 값이 아닙니다.
