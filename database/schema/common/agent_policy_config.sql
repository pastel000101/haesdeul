-- agent_policy_config — 공통
--
-- 적용 순서는 `database/new_database_order.txt`.

BEGIN;

CREATE TABLE haetdeul.agent_policy_config (
    policy_id bigint NOT NULL,
    domain character varying(30) NOT NULL,
    policy_key character varying(100) NOT NULL,
    value_kind character varying(20) NOT NULL,
    value_numeric numeric,
    value_text text,
    value_json jsonb,
    unit character varying(50),
    evidence_grade character varying(20) NOT NULL,
    source_ref character varying(200) NOT NULL,
    approved_by character varying(100) NOT NULL,
    policy_version character varying(40) NOT NULL,
    persona_version character varying(40),
    usage_scope character varying(40) DEFAULT 'AGENT_MVP_DEMO'::character varying NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    note text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT agent_policy_config_evidence_grade_check CHECK (((evidence_grade)::text = ANY ((ARRAY['OFFICIAL'::character varying, 'VENDOR'::character varying, 'SIM_FIXED'::character varying, 'ASSUMED'::character varying])::text[]))),
    CONSTRAINT agent_policy_config_value_kind_check CHECK (((value_kind)::text = ANY ((ARRAY['NUMERIC'::character varying, 'TEXT'::character varying, 'JSON'::character varying])::text[]))),
    CONSTRAINT ck_agent_policy_single_value CHECK ((((((value_numeric IS NOT NULL))::integer + ((value_text IS NOT NULL))::integer) + ((value_json IS NOT NULL))::integer) = 1))
);

CREATE SEQUENCE haetdeul.agent_policy_config_policy_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE haetdeul.agent_policy_config_policy_id_seq OWNED BY haetdeul.agent_policy_config.policy_id;

ALTER TABLE ONLY haetdeul.agent_policy_config ALTER COLUMN policy_id SET DEFAULT nextval('haetdeul.agent_policy_config_policy_id_seq'::regclass);

ALTER TABLE ONLY haetdeul.agent_policy_config
    ADD CONSTRAINT agent_policy_config_pkey PRIMARY KEY (policy_id);

ALTER TABLE ONLY haetdeul.agent_policy_config
    ADD CONSTRAINT uq_agent_policy_version UNIQUE (policy_version, domain, policy_key);

COMMIT;
