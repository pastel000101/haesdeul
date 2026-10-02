# `database/` — 스키마 파일과 실행 순서

새 DB 는 §1 의 적용 목록으로 세우고, 이미 데이터가 있는 DB 는 §2 의 이관판을 한 파일씩 쓴다.
행 데이터(seed)는 어느 경로에도 자동으로 들어가지 않는다 — `seed/README.md`.

## 0. 폴더 구성

```text
database/
├─ new_database_order.txt    새 DB 적용 순서 — `apply_sql.py --new-database` 가 이 순서로만 돈다
├─ schema/<부서>/<객체>.sql   지금 쓰는 구조 = 새 DB 의 최종 정의. 표(뷰) 하나에 파일 하나 — 칸 · 키 · 제약 · 인덱스 · 주석 · FK
├─ migrations/<부서>/         이미 쓰는 DB 를 바꾸는 변경(ALTER 판 · 이관). 새 DB 목록에는 없다. 파일 이름은 이력이라 그대로
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

- 부서 배정은 표 주석과 앱에서 쓰는 곳을 기준으로 정했다. pg_dump 스냅샷에서 온 표에는 정해진
  주인이 없었다(§5). 확인 필요: `daily_closings`(재무 · 마스터 마감이 쓴다), `partner_item_demands` ·
  `v_current_partner_demand`(매입 · 판매 수요), `forecasts`(ML · 매입), `agent_runs`(옛 Agent 감사로그).
- 폴더와 파일 이름은 순서가 아니다. 새 DB 는 `new_database_order.txt` 순서로만 세운다(§1).
- 스냅샷에서 나눈 schema 파일은 파일마다 `BEGIN;` · `COMMIT;` 으로 감싸 있다. pg_dump 의 세션
  설정(`SET` · `set_config`) · 중복 `CREATE SCHEMA` · psql 명령 `\restrict` · `\unrestrict` 는 없다.
  나누기 전 원본 스냅샷과 머리말은 git `3525c8f3` 에 있다.
- `migrations/` 파일 안의 옛 경로 · 이름 표기(예: `psql -f database/<파일>`)는 그 파일이 쓰인 때의
  기록이라 고치지 않는다. 예외는 실행되는 `\i` 한 줄 —
  `migrations/master/master_agent_runs_migration.sql` 은 `database/schema/master/master_agent_runs.sql`
  을 읽는다.

### 새 DB 에 없는 객체 · 남긴 객체

공유 DB 의 스냅샷에는 있지만 새 DB 목록에는 없는 객체다 — 앱 · 스크립트 · 테스트 · 다른 SQL 에서
쓰는 곳이 없고, 남는 객체가 FK · 뷰 · 트리거로 기대지도 않는다. 이미 있는 DB 에서는 지우지 않는다
(DROP 판 없음). 정의는 git `3525c8f3:database/10_domain_schema.sql` 에 있다.

| 없는 객체 | 무엇이었나 | 확인한 것 |
|---|---|---|
| `agent_policies` | Agent 판단 임계치 설정 | 쓰는 곳 없음 — 지금 쓰는 설정 표는 `agent_policy_config` |
| `constraint_reviews` | T2 병렬 제약 검토 결과 | 쓰는 곳 없음 |
| `persona_evidences` | Persona 필드 ↔ 근거 연결 | 쓰는 곳 없음. 재무 정책 seed 의 설명 문구가 출처로 이름만 적는다 — 확인 필요: 근거 연결을 새 DB 에도 둘지 |
| `v_current_inventory` · `v_current_logistics_capacity` | 현재 재고 · 5PL 여유 계산 뷰 | 쓰는 곳 없음. 코드가 이 뷰를 안 쓴다는 검사가 있다(`test_logistics_service_repository`) |
| `v_dashboard_state` | UI Mock 대체 회사 상태 뷰 | 쓰는 곳 없음(재무 뷰 이관 판 머리말의 옛 언급만) |
| `v_ml_forecast_revisions` | 예측 덮어쓰기 이력 요약 뷰 | 저장소 안에서 쓰는 곳 없음 — 확인 필요: 저장소 밖 ML 배치 · 운영 도구가 읽는지는 확인하지 못했다 |
| `v_seed_validation` | 초기 seed 정합성 검사 뷰 | 쓰는 곳 없음 |

앱이 이름으로 부르지 않지만 남긴 것 — 남는 객체가 기댄다.

| 남긴 객체 | 이유 |
|---|---|
| `agent_runs` | `proposals` 의 FK 가 가리킨다 |
| `deliveries` | `expenses` 의 FK 가 가리킨다 |
| `ml_price_forecasts_history` | `ml_price_forecasts` 의 트리거(`trg_ml_forecast_archive`)가 예측을 덮어쓸 때 옛 값을 이 표에 쌓는다 |

### WMS 파일과 공유 DB

`schema/logistics/` 의 WMS 파일들은 공유 외부 DB(`haetdeul`)에 이미 존재하는 WMS 구조를 저장소로
회수한 것이다. 공유 DB 에 다시 적용하는 것이 목적이 아니라, 저장소만으로 같은 스키마를 세울 수 있게
하는 것이 목적이다. 공유 DB 는 이미 이 모양이라 돌릴 이유가 없다.

## 1. 새 DB 를 세울 때 — `new_database_order.txt` 순서로

```bash
cd backend
.venv/Scripts/python.exe scripts/apply_sql.py --new-database          # 목록만 본다 (DB 에 안 붙는다)
.venv/Scripts/python.exe scripts/apply_sql.py --new-database --apply  # backend/.env 의 DB 에 세운다
```

- 목록에 적힌 순서대로 한 파일씩 적용하고, 한 파일이라도 실패하면 거기서 멈추고 실패 종료 코드를
  낸다.
- 대상 DB 의 `haetdeul` 에 객체가 하나라도 있으면 아무것도 안 하고 멈춘다 — 이미 쓰는 DB 는 §2 로.
- 목록에는 `schema/` 파일 76개만 있다. 각 파일이 새 DB 에서 그 표(뷰)의 최종 정의다 — 표 파일
  하나만 읽으면 새 DB 의 칸 · NULL 허용 · 기본값 · 제약 · 인덱스 · 주석을 알 수 있다. `migrations/` 는
  이미 쓰는 DB 를 갱신할 때만 쓴다(§2).
- 순서는 참조로 정했다 — FK · 뷰 · 함수가 가리키는 객체를 만드는 파일이 먼저 온다. 스키마 파일을
  더하면 목록에도 넣는다. `backend/tests/scripts/test_new_database_order.py` 가 빠진 파일 · 순서
  위반 · seed 포함 · `schema/` 밖 파일 · 숫자 접두어를 잰다.
- 새 DB 를 세우는 데 psql 은 필요 없다. 목록의 파일에 psql 명령 줄(`\i` 등)이 없어 `apply_sql.py`
  가 전부 psycopg 로 보낸다. psql 이 필요한 파일은 기존 DB 용 이관판 하나다(§2).
- 목록 76개로 빈 PostgreSQL 17 DB 에 세우면 69표 · 6뷰가 선다(2026-09-30 실측 · seed 없음).

### schema 파일이 담은 이관판 효과

아래 이관판 8개의 최종 효과는 해당 schema 파일에 들어 있다. 새 DB 에는 이 이관판을 적용하지
않는다. 이미 쓰는 DB 는 여전히 이 이관판으로 간다(§2).

| 이관판 | 새 DB 에 남는 효과 | 담은 schema 파일 |
|---|---|---|
| `migrations/master/master_agent_runs_sim_run_id.sql` | `sim_run_id TEXT NULL`(맨 뒤 칸 · 기본값 없음) · 부분 인덱스 `idx_master_agent_runs_sim_run_as_of` · 칸 주석 | `schema/master/master_agent_runs.sql` |
| `migrations/master/master_agent_runs_migration.sql` | 결정 → 실행 FK `master_decisions_run_fk` 가 `master_agent_runs(run_id, request_id)` 를 가리킨다(ON DELETE RESTRICT). 행 이관(INSERT)과 행 수 확인은 기존 DB 에만 뜻이 있어 schema 파일에 없다 | `schema/master/master_decisions.sql` |
| `migrations/master/master_decision_revalidation.sql` | 부분 인덱스 `idx_master_decisions_revalidation_request_id` · 두 칸 주석. 칸 둘과 CHECK 둘도 본 DDL 에 있다 | `schema/master/master_decisions.sql` |
| `migrations/sales/sales_source_order_id_nullable.sql` | `sales.source_order_id` NOT NULL 해제 | `schema/sales/sales.sql` |
| `migrations/finance/finance_current_state_view.sql` | `v_current_finance_state` 본문(`sim_runs` 가 정한 축의 가장 늦은 상태)과 뷰 주석 | `schema/finance/v_current_finance_state.sql` |
| `migrations/finance/finance_state_as_of_index.sql` | 인덱스 `ix_finance_states_as_of` | `schema/finance/finance_states.sql` |
| `migrations/finance/expense_lifecycle.sql` | 인덱스 `expenses_projection_axis_idx` · `expenses_paid_axis_idx`. 칸 셋과 칸 주석도 본 DDL 에 같은 모양으로 있다 | `schema/finance/expenses.sql` |
| `migrations/logistics/logistics_drop_inbound_json.sql` | 입고 JSON 두 칸(`in_transit_json` · `confirmed_inbound_json`)이 없다 — 처음부터 만들지 않는다. 상태 두 칸 주석 | `schema/logistics/logistics_runtime_fixture.sql` |

- 기존 DB 의 행을 옮기는 문장(INSERT · 행 수 확인)은 새 DB 의 CREATE 에 없다.
- 이관판 8개 머리에는 «새 DB 는 이 파일을 적용하지 않는다 · 최종 모양이 있는 schema 파일 · 아래
  머리말의 신규 구축 안내는 작성 당시 기준» 주석이 있다. 실행 문장은 그대로다.

### 다시 돌릴 때

- 스키마 파일을 다시 돌리면: 스냅샷에서 나눈 파일 33개와 `master/master_purchase_records.sql` 은
  `IF NOT EXISTS` 가 없어 «이미 있다» 로 멈춘다 — 파일마다 `BEGIN;` · `COMMIT;` 이라 멈춘 파일은
  아무것도 바꾸지 않는다. 나머지 42개는 멱등이다.
- 이관판(§2)은 새 DB 목록에 넣지 않는다. 23개 모두를 새 DB 에 하나씩 얹어 보면 21개는 바뀌는 것이
  없다. 예외 둘 — `logistics_exceptions_detection_history` 는 칸 주석 문구가 본 DDL 과 조금 다르고,
  `logistics_inbound_schedules` 는 입고 JSON 칸이 없어 오류로 멈춘다.
- seed 는 넣지 않는다. 정책 · fixture · 데모 데이터는 사용자가 대상 DB 를 확인하고 직접 넣는다 —
  `seed/README.md`.

## 2. 이미 데이터가 있는 DB 를 옮길 때 — 위 파일을 쓰지 않는다

`CREATE TABLE IF NOT EXISTS` 는 이미 있는 표를 고치지 않는다. 그래서 운영 중인 DB 에는 본 DDL 이
아니라 ALTER 판을 쓴다. 한 파일씩, 먼저 보기만 한 뒤 적용한다.

```bash
cd backend
.venv/Scripts/python.exe scripts/apply_sql.py database/migrations/<부서>/<파일>.sql          # 보기만
.venv/Scripts/python.exe scripts/apply_sql.py database/migrations/<부서>/<파일>.sql --apply  # 적용
```

§1 표의 이관판 8개도 이미 쓰는 DB 에는 이 파일들로 간다. 효과를 schema 파일에 담은 것은 새 DB
용이고, 기존 DB 를 바꾸지 않는다. 아래 표에 적용 기록이 있는 것은 `logistics_drop_inbound_json.sql`
(적용 대기) 하나이고 나머지 7개는 이 README 에 적용 기록이 없다 — 확인 필요: 적용하기 전에 대상
DB 에 그 칸 · 인덱스 · 제약 · 뷰 정의가 이미 있는지 먼저 본다. 적용 기록은 아래 표의 «언제» 칸이
전부다(별도 이력 표 없음).

psql 이 필요한 파일이 하나 있다 — `migrations/master/master_agent_runs_migration.sql` 의 `\i`
(psql 명령). `apply_sql.py` 는 이런 파일만 `psql -X -w -v ON_ERROR_STOP=1 -f` 로 저장소 루트에서
돌리고(psql 이 실패하면 스크립트도 그 종료 코드로 실패), 나머지는 psycopg 로 보낸다. 그래서 그 파일을
적용하는 자리에는 PATH 에 `psql` 이 있어야 한다(`\i` 는 모든 판이 알고, psql 17.11 에서 확인했다).
비밀번호는 `PGPASSWORD` 로, 파일 인코딩은 `PGCLIENTENCODING=UTF8` 로 넘긴다. 옛 pg_dump 원본(git
`3525c8f3`)을 psql 로 다시 돌리려면 `\restrict` 를 아는 psql 17.6 · 16.10 이상이 필요하다
(PostgreSQL 릴리스 노트 CVE-2025-8714 — 15 이하 판은 확인하지 않았다).

| 이관 스크립트 | 무엇을 바꾸나 | 언제 |
|---|---|---|
| `migrations/master/master_runs_migration.sql` | `orchestrator_agent_runs` 에 `agent='master'` · `request_id` · `plan` | 2026-08-27 |
| `migrations/master/master_decisions_run_id.sql` | `master_decisions.run_id` + 복합 FK + 인덱스 | 2026-08-30 · 팀 승인 대기 |
| `migrations/ml/ml_forecast_view_gate_reason.sql` | `v_ml_price_forecast` 의 `daily[]` 에 `gate_reason` 추가 | 2026-09-03 · 실 DB 적용 대기 |
| `migrations/finance/finance_state_daily_unique.sql` | `finance_states` 의 `UNIQUE(sim_run_id, financing_mode, state_date)` · 적용 전 duplicate preflight · 기존 데이터 자동 정리 없음 | 2026-09-05 · 실 DB 적용 대기 |
| `migrations/finance/partner_credit_limits_recorded_by.sql` | `partner_credit_limits` 에 입력자 감사 칸 추가. 기존 금액·기간은 유지하고 과거 행의 복원 불가능한 입력자는 `LEGACY_UNKNOWN` 으로 명시 | 2026-09-16 · 실 DB 적용 대기 |
| `migrations/finance/payable_cancellation.sql` | `payables` 의 `CANCELLED` · 취소금액/취소일 · 지급/취소/미지급 금액 항등식. 기존 행은 취소금액 0, 자동 상태 변경·삭제 없음 | 2026-09-05 · 실 DB 적용 대기 |
| `schema/logistics/` 의 WMS 파일 24개 | 물류 WMS 표 21 · 뷰 2 회수 + `inventory_lots` 컬럼 6·제약 5 · `inventory_moves` UNIQUE 1 | 2026-09-05 · 실 DB 에는 이미 있음(회수) · 신규 구축 DB 에는 필수 |
| `migrations/logistics/logistics_inventory_lots_nullable.sql` | `inventory_lots.grade` · `derivation_status` NOT NULL 해제. 기존 행 값 변경 없음. 정상 실입고가 미확정 등급과 비-Burn-in 상태를 NULL 로 표현할 수 있게 함 | 2026-09-05 · 실 DB 적용됨(2026-09-08 실측 확인) |
| `schema/master/master_collection_events.sql` | 수금 사건 표 `master_collection_events` 신설. 새 표 하나뿐이라 기존 표를 안 건드린다 — 적용 전후 `receivables` 15행 그대로. 표는 비어서 만들어진다 | 2026-09-08 · 실 DB 적용됨 |
| `migrations/logistics/logistics_allocation_basis_fefo_auto.sql` | `inventory_allocations.allocation_basis` 에 `FEFO_AUTO_SELECTED` 추가 (시뮬레이션 자동 FEFO 할당). 어휘를 넓히기만 한다 — 기존 `FEFO_TOOL_CONFIRMED` · `HUMAN_OVERRIDE` 행은 그대로 유효하고 행 변경이 없다. CHECK 를 넓히려면 `DROP` 이 불가피해 한 트랜잭션 안에서 지웠다 다시 건다 | 2026-09-08 · 실 DB 적용됨(2026-09-08 실측 확인) |
| `migrations/logistics/logistics_drop_inbound_json.sql` | `logistics_runtime_fixture` 에서 `in_transit_json` · `confirmed_inbound_json` 두 칸 DROP. 입고 예정의 정본은 `inbound_schedules` 이고 두 칸을 읽는 곳도 쓰는 곳도 없다. status 두 칸은 안 걷는다 — Reader 가 `UNRESOLVED` 를 실제로 읽는다. `confirmed_outbound_*` 도 안 건드린다(WP-3). 적용 전 확인 넷이 파일 머리말에 있다 | 2026-09-09 · 실 DB 적용 대기 |
| `migrations/logistics/logistics_reservation_released_as_of.sql` | `inventory_reservations` 에 `released_as_of DATE` 한 칸 추가 (WP-3 M3). additive 다 — 기존 칸을 안 바꾸고 안 지운다. Backfill 없음(놓아준 날짜를 되살릴 근거가 없다 · 실측 8행 전부 `ALLOCATED` 라 `NULL` 이 사실이다). CHECK · INDEX 도 안 건다 — 규율은 `outbound.release_reservation` 의 필수 인자가 지킨다. 주의: 이 칸이 없으면 예약 조회 SQL 이 `column does not exist` 로 깨진다 (배포 선행조건) | 2026-09-09 · 실 DB 적용 대기 |
| `schema/logistics/logistics_exceptions.sql` · `logistics_investigations.sql` · `logistics_action_proposals.sql` | 재고·물류 Agent Core 표 셋 — `logistics_exceptions`(#628 Commit 2) · `logistics_investigations`(Commit 7) · `logistics_action_proposals`(Commit 5 + Commit 6 실행 축). 기존 표를 안 건드린다 — 남의 표에 하는 일은 `sim_runs` 를 FK 로 가리키는 것뿐이고 DROP 은 없다. 표는 비어서 만들어진다. `logistics_exceptions` 가 없으면 걷기의 물류 점검 두 칸이 `FAILED` 한 줄을 남기고 나머지 열세 칸은 그대로 돈다 (하루가 안 죽는다) | 2026-09-12 · 2026-09-13 갱신 · 실 DB 적용 대기 |
| `migrations/logistics/logistics_inbound_schedules.sql` | 입고 예정 표 `inbound_schedules` 신설 + 살아 있는 `in_transit_json` 5건 Backfill. 기존 표를 안 건드린다 — `logistics_runtime_fixture` 에 UPDATE 도 DELETE 도 없고 적용 전후 254행·in_transit 5건 그대로. `purchase_item_id` FK 는 `purchases` CASCADE 삭제 정책이 정해질 때까지 보류. 두 번 돌려도 5건 (`WHERE NOT EXISTS`) | 2026-09-09 · 실 DB 적용됨(2026-09-09 실측 확인) |

### 본 DDL 과 ALTER 판은 함께 고친다

같은 변경이 두 곳에 있다 — 본 DDL(`schema/`, 신규 구축용)과 ALTER 판(`migrations/`, 이관용).
어느 하나만 고치면 갈린다. 둘 다 고친다.

적어 두는 것만으로는 안 지켜진다. 갈려도 어느 쪽도 에러를 안 내고 서로 다른 스키마가 조용히
생긴다. 그래서 검사로 건다 — `backend/tests/master/test_schema_files_agree.py` 가 두 파일의 뷰 본문 ·
어휘 CHECK · 제약 이름을 대조한다.

### 표 파일을 기존 DB 에 돌릴 때

WMS · Agent 표 파일은 전부 멱등이라 이미 쓰는 DB 에도 그대로 돌릴 수 있다 — 모든 문장이
`CREATE TABLE IF NOT EXISTS` · `ADD COLUMN IF NOT EXISTS` · `CREATE INDEX IF NOT EXISTS` 이거나
`pg_constraint` 조회로 감싼 제약이고, `DROP` 이 없다.

주의: `schema/logistics/inventory_lots.sql` · `inventory_moves.sql` 은 스냅샷의
`CREATE TABLE`(`IF NOT EXISTS` 없음) 뒤에 공유 DB 에서 자란 칸 · 제약을 멱등 ALTER 로 더한다.
파일을 통째로 기존 DB 에 돌리면 첫 문장에서 멈춘다(`BEGIN` · `COMMIT` 이라 바뀌는 것 없음). 그
보강만 기존 DB 에 필요하면 git `3525c8f3` 의 `30_logistics_wms_schema.sql` 을 쓴다 — 공유 DB 에는
이미 있다(회수).

## 3. 파일별 소유

| 파일 | 소유 | 비고 |
|---|---|---|
| `schema/common/haetdeul_schema.sql` | 공통 | 스키마 생성만 |
| pg_dump 스냅샷에서 나눈 `schema/<부서>/` 파일 33개 | 부서 배정(§0) · 확인 필요 | 스냅샷에는 주인이 없었다 — §5 |
| `schema/master/orchestrator_agent_runs.sql` | 마스터 | 오케 · Critic 실행이력. 마스터 실행은 `master_agent_runs` 에 적는다(§4) |
| `schema/master/master_agent_runs.sql` | 마스터 | 마스터 실행이력 |
| `schema/master/master_decisions.sql` | 마스터 | 사람의 결정. append-only |
| `migrations/master/master_decisions_run_id.sql` | 마스터 | 위의 ALTER 판 |
| `migrations/master/master_runs_migration.sql` | 마스터 | `orchestrator_agent_runs` 의 ALTER 판 |
| `schema/master/master_collection_events.sql` | 마스터 | 수금 사건. DDL 은 표를 비워서 만든다 — 무엇을 사실로 둘지는 팀 결정이고, 재무가 "due_date 경과를 수금으로 읽지 않는다" 로 그은 선이 그 이유다. 행은 사용자 수금 기록과 하루 개장의 `SIM_FIXED` 시드가 쓴다(파일 머리말) |
| `schema/finance/finance_agent_runs*.sql` · `migrations/finance/finance_agent_runs_v22_sales_validation.sql` | 재무 | |
| `schema/logistics/logistics_agent_runs.sql` | 물류 | |
| `schema/logistics/` 의 WMS 파일 24개 | 물류 | WMS 표 21 · 뷰 2 회수 (2026-09-04 cutover 분). 다른 파트 표는 FK 로 가리키기만 한다 |
| `migrations/logistics/logistics_inventory_lots_nullable.sql` | 물류 | 재고·물류 동작을 바꾸는 변경이라 물류가 낸다. 대상 표(`inventory_lots`)의 본 DDL 은 `schema/logistics/inventory_lots.sql` |
| `migrations/logistics/logistics_allocation_basis_fefo_auto.sql` | **물류** | WMS 표의 ALTER 판. 값 이름은 Master ↔ Logistics 합의 어휘이고, 표와 제약의 주인은 물류다 |
| `migrations/logistics/logistics_drop_inbound_json.sql` | 물류 | 쓰지 않는 입고 JSON 두 칸 정리 (W3-4). 이미 쓰는 DB 용이다 — 새 DB 는 `schema/logistics/logistics_runtime_fixture.sql` 이 두 칸을 처음부터 만들지 않는다. WMS 표 파일에 넣지 않은 이유는 그 파일들이 «DROP 이 없다» 를 계약으로 두기 때문이다 |
| `migrations/logistics/logistics_reservation_released_as_of.sql` | 물류 | 예약 표의 이관 판. "이 예약을 언제 놓아줬나" 를 적을 칸 하나 — 이 칸이 없으면 `updated_at`(벽시각)으로 과거 예약 상태를 추정할 수밖에 없고, 그러면 같은 데이터가 내일 다른 과거를 낸다 (WP-3 M3) |
| `migrations/logistics/logistics_inbound_schedules.sql` | 물류 | 입고 예정 표의 이관 판 + Backfill. 입고 예정을 날짜별 fixture JSON 에서 꺼내 날짜에 안 묶인 업무 Entity 로 세운다 (W3-1). 신규 구축에서는 안 돌린다(§1) |
| `schema/logistics/logistics_exceptions.sql` · `logistics_investigations.sql` · `logistics_action_proposals.sql` | 물류 | 재고·물류 Agent Core 의 표 셋 — 운영 Exception · 조사 실행 기록 · 대응안. Exception 은 결정론 탐지기가 쓰고(`app/logistics/repository/exceptions.py`), 마스터는 점검 트랜잭션만 진다(`app/master/service/inspection.py`). 조사 · 대응안 표는 지금 `backend/app` 에서 읽거나 쓰는 코드가 없다. `logistics_agent_runs`(Logistics API Request/Response 이력)와 다른 표다 |
| `seed/logistics/logistics_runtime_fixture_20260102.sql` | 물류 | 물류가 만들고 물류가 채운다. 런타임 fixture 씨앗 행. 다른 파트는 읽기만 |
| `seed/logistics/logistics_runtime_fixture_20260105_20260106.sql` | 물류 | 물류가 만들고 물류가 채운다. 런타임 fixture 씨앗 행 · 관통 실행일 쌍. 다른 파트는 읽기만 |
| `schema/sales/sales_agent_runs.sql` | 판매 | |
| `schema/ml/ml_calendar_days.sql` · `ml_batch_day_status.sql` · `v_ml_batch_days.sql` | ML | ML 이 만들고 ML 이 채운다. 조사일·경매일·공휴일 달력 + `v_ml_batch_days`. 다른 파트는 읽기만 |
| `seed/demo/mvp_demo_remove_dried_pepper.sql` | 데모 | 스키마가 아니라 데이터 정리 |
| `seed/demo/mvp_demo_remove_pimanul.sql` | 데모 | 스키마가 아니라 데이터 정리 · #216 피마늘 제외 · 실 DB 적용 대기 |

## 4. `orchestrator_agent_runs` 와 `master_agent_runs`

`orchestrator_agent_runs` 는 오케 · Critic 실행이력이다. Critic 이 `agent='critic'` 으로 쓰고 읽는다
(`app/master/repository/cycle_runs.py` · `app/master/readmodel/cycle_runs.py` ·
`app/master/service/cycle_persistence.py`). 마스터 실행은 마스터 소유 표 `master_agent_runs` 에
적는다(`app/master/repository/runs.py` · `app/master/service/persistence.py`) — 따로 두는 이유는
`schema/master/master_agent_runs.sql` 머리말에 있다.

`orchestrator_agent_runs` 에 남은 `agent='master'` · `agent='orchestrator'` 행은 그때 실제로 있었던
실행이다. 표를 옮기거나 이름을 바꾸면 과거 행의 판정을 나중에 바꾸는 셈이라 그대로 둔다. 기존
DB 에서 마스터 행을 새 표로 옮기는 이관은 `migrations/master/master_agent_runs_migration.sql` 이다.

## 5. 스냅샷에서 온 schema 파일은 사람이 쓴 것이 아니다

`schema/<부서>/` 파일 중 33개는 살아 있는 test DB 에서 `pg_dump` 로 뜬 스냅샷을 객체별로 나눈
것이다(문장은 스냅샷 그대로). 그 가운데 5개(`sales` · `v_current_finance_state` · `finance_states` ·
`expenses` · `logistics_runtime_fixture`)는 이관판 효과를 담아 스냅샷과 다르다(§1).

본 DB 를 세우기 전에 각 파트가 자기 표를 봐야 한다.

- 지금 안 쓰는 표가 섞여 있을 수 있다 — `agent_runs` · `sim_runs` · `finance_agent_runs_v22` 는
  이름이 겹치거나 옛것으로 보인다. 판단은 각 파트 몫이다.
- test DB 에서 손으로 바꾼 `CHECK` · `DEFAULT` 가 있으면 그대로 따라온다.
- 설계 의도가 주석에 없다. 다른 파일들과 달리 왜 그런지가 안 적혀 있다.

## 6. 검증하는 법

빈 컨테이너에 세워 보는 것이 가장 확실하다. 공용 DB 를 건드리지 않는다.

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

### 컨테이너를 못 쓸 때 — 임시 스키마에 세워 보고 되돌린다

물류 `db` 검사가 이 방식으로 `schema/logistics/` 의 표 파일들을 임시 스키마에 세운다
(`backend/tests/logistics/logistics_schema_files.py`). 스크립트의 `haetdeul.` 을 임시 스키마 이름으로
바꿔 한 트랜잭션 안에서 세우고, 실 DB 카탈로그와 대조한 뒤 `ROLLBACK` 한다.

```text
① 임시 스키마 + 다른 도메인 PK-only stub (items · partners · sim_runs ·
   purchase_items · sales · sale_items)
② inventory_lots · inventory_moves 는 저장소의 스냅샷 문장으로 세운다
   → 뒤따르는 ALTER 가 실제로 실행되고 "신규 구축 = 실 DB" 가 증명된다
③ 대상 스크립트 실행 → 실 DB 와 컬럼·타입·NULL·DEFAULT·제약·인덱스·주석·뷰 대조
④ 한 번 더 실행해 멱등 확인
⑤ ROLLBACK
```

`haetdeul` 을 건드리지 않는다 — 이름을 바꿔 돌리므로 FK 가 `haetdeul` 표를 가리키지 않고, 따라서
잠금도 걸리지 않는다. `haetdeul` 은 세는 데만 읽는다.
