# `database/seed/` — 행 데이터는 사용자가 직접 넣는다

새 DB 적용 목록(`../new_database_order.txt`)에는 seed 가 하나도 없다. 스키마를 세우는 일과
데이터를 넣는 일을 나눴다 — 데이터는 넣을 DB 와 파일을 사람이 확인하고 한 파일씩 넣는다.

## 넣는 법

1. 대상 DB 를 확인한다. `apply_sql.py` 는 `backend/.env` 의 `DB_HOST` · `DB_PORT` · `DB_NAME` 에
   붙는다. 공유 DB 인지 새로 세운 DB 인지 먼저 본다.
2. 파일 머리말을 읽는다. 무엇을 전제로 하는지(먼저 있어야 하는 행), 다시 넣으면 어떻게 되는지가
   파일마다 적혀 있다. 아래 표는 그 요약이다.
3. 먼저 보기만 한다 — DB 에 붙지 않는다.

   ```bash
   cd backend
   .venv/Scripts/python.exe scripts/apply_sql.py database/seed/<부서>/<파일>.sql
   ```

4. 한 파일씩 넣는다.

   ```bash
   .venv/Scripts/python.exe scripts/apply_sql.py database/seed/<부서>/<파일>.sql --apply
   ```

주의: `--new-database` 는 seed 를 넣지 않는다. 폴더째 한꺼번에 돌리는 명령도 두지 않는다.

## 파일

아래는 파일 머리말과 SQL 을 읽어 적은 것이다. DB 에 넣어 확인한 것이 아니다.

| 파일 | 넣는 것 | 먼저 있어야 하는 것 | 다시 넣으면 |
|---|---|---|---|
| `finance/finance_policy_seed.sql` | 재무 정책 21행(`agent_policy_config`) | 표 | 있는 행의 값을 덮어쓴다(`ON CONFLICT DO UPDATE`). 머리말: 운영 DB 행 반영은 별도 절차 |
| `finance/finance_transition_proof_day5_seed.sql` | 2026-01-05 재무 상태 fixture 1행 | `sim_runs` 의 `SIM-BURNIN-202512` 행(FK) | 이미 있으면 건너뛴다 |
| `finance/partner_credit_limit_seed.sql` | 거래처 여신한도 1행(MVP 고정값 · 실제 계약 한도 아님) | `partners` 의 `KIMCHI_FACTORY_001` — 없으면 아무것도 안 넣는다 | 이미 있으면 건너뛴다 |
| `logistics/logistics_llm_policy_seed.sql` | 물류 선택 정책 2행 | 표 | 이미 있으면 건너뛴다 |
| `logistics/logistics_outbound_prep_lead_seed.sql` | 출고 준비일 정책 1행 | 표 | 이미 있으면 건너뛴다 |
| `logistics/logistics_runtime_fixture_20260102.sql` | 물류 runtime fixture 1행 | 2025-12-31 fixture 행 1건 — 없으면 예외로 멈춘다. 그 행을 만드는 SQL 은 저장소에 없다(머리말) | 이미 있으면 건너뛴다 |
| `logistics/logistics_runtime_fixture_20260105_20260106.sql` | 물류 runtime fixture 2행 | 위와 같음 | 이미 있으면 건너뛴다 |
| `demo/mvp_demo_remove_dried_pepper.sql` | 데모 재고 보정(이동 1 · 로트 상태) | 대상 로트 — 없으면 예외로 멈춘다 | 이미 보정됐으면 그대로 끝난다 |
| `demo/mvp_demo_remove_pimanul.sql` | 데모 재고 보정(#216) | 대상 로트 — 없으면 예외로 멈춘다 | 이미 보정됐으면 그대로 끝난다 |

- 파일 이름의 날짜는 fixture 가 채우는 날이다.
- 적용 여부(적용됨 · 대기)는 `../README.md` §2 · §3 과 각 파일 머리말의 기록뿐이다.
