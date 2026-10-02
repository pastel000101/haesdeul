-- inbound_inspection_checks — 물류
--
-- 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE IF NOT EXISTS haetdeul.inbound_inspection_checks (
    check_id      BIGSERIAL NOT NULL,
    inspection_id TEXT NOT NULL,
    check_code    TEXT NOT NULL,
    observed      BOOLEAN NOT NULL,
    severity      TEXT,
    note          TEXT,
    CONSTRAINT inbound_inspection_checks_pkey PRIMARY KEY (check_id),
    CONSTRAINT uq_inbound_inspection_checks UNIQUE (inspection_id, check_code),
    CONSTRAINT inbound_inspection_checks_inspection_id_fkey
        FOREIGN KEY (inspection_id) REFERENCES haetdeul.inbound_inspections(inspection_id),
    CONSTRAINT ck_inbound_inspection_checks_code
        CHECK (check_code IN ('MOLD', 'ROT', 'ODOR', 'APPEARANCE_DAMAGE',
                              'CONTAMINATION', 'PACKAGING_DAMAGE')),
    CONSTRAINT ck_inbound_inspection_checks_severity
        CHECK (severity IS NULL OR severity IN ('LOW', 'MEDIUM', 'HIGH'))
);

COMMENT ON TABLE haetdeul.inbound_inspection_checks IS
    '검수 항목별 기록 — 곰팡이·무름/부패·이상냄새·외관/압상/파손·오염·포장손상 (03 §4). 사람이 웹 Form 으로 넣는다.';

COMMIT;
