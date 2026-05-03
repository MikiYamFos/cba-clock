-- =============================================================================
-- CBA Clock — Postgres Schema
-- =============================================================================
--
-- EMPLOYER SIDE:
--   corporate_group → corporation → property (with successor tracking)
--     └── bargaining_unit
--           └── worker (via worker_bargaining_unit junction)
--                 └── worker_status_history
--                 └── worker_negotiation_role
--           └── manager (with property history)
--
-- CONTRACT SIDE:
--   bargaining_unit
--     └── negotiation
--           └── negotiation_proposal
--                 └── proposed_change (article-level, with side attribution)
--           └── cba (ratified — legally distinct from TA)
--                 └── cba_section
--                       └── timing_rule (Claude extraction, immutable)
--                             └── timing_rule_correction (human correction, separate)
--                 └── cba_holiday
--                 └── side_negotiation (MOU or reopener mini-negotiation)
--                       └── negotiation_proposal
--                       └── cba (document_type=mou or reopener)
--                             └── mou_section
--
-- GRIEVANCE SIDE:
--   grievance (tied to cba)
--     └── grievance_article
--     └── grievance_respondent
--     └── grievance_worker
--     └── grievance_step
--
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Corporate hierarchy
-- ---------------------------------------------------------------------------

CREATE TABLE corporate_group (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    jurisdiction    TEXT,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE corporation (
    id                  SERIAL PRIMARY KEY,
    corporate_group_id  INTEGER REFERENCES corporate_group(id),
    name                TEXT NOT NULL,
    legal_name          TEXT,
    jurisdiction        TEXT,
    notes               TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- Property tracks its own successor chain via self-referential FK.
-- When Taj Mahal closes and Hard Rock opens on the same site,
-- Hard Rock's property record points back to Taj via successor_property_id.
CREATE TABLE property (
    id                      SERIAL PRIMARY KEY,
    corporation_id          INTEGER REFERENCES corporation(id),
    successor_property_id   INTEGER REFERENCES property(id),   -- property this succeeded
    succession_date         DATE,
    succession_type         TEXT,                              -- e.g. 'acquisition', 'closure_reopening', 'subcontracting'
    name                    TEXT NOT NULL,
    address                 TEXT,
    city                    TEXT,
    state                   TEXT,
    country                 TEXT DEFAULT 'US',
    timezone                TEXT NOT NULL DEFAULT 'America/New_York',
    notes                   TEXT,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE bargaining_unit (
    id              SERIAL PRIMARY KEY,
    property_id     INTEGER REFERENCES property(id),
    name            TEXT NOT NULL,
    union_name      TEXT NOT NULL,
    union_local     TEXT,
    craft           TEXT,
    jurisdiction    TEXT,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Workers and managers
-- ---------------------------------------------------------------------------

CREATE TABLE worker (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    employee_id     TEXT,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Junction: worker can belong to multiple bargaining units over time.
-- Includes recall right columns for successor employer situations.
CREATE TABLE worker_bargaining_unit (
    worker_id               INTEGER NOT NULL REFERENCES worker(id),
    bargaining_unit_id      INTEGER NOT NULL REFERENCES bargaining_unit(id),
    start_date              DATE,
    end_date                DATE,                       -- NULL if currently active
    recall_expiration_date  DATE,                       -- when recall right expires if applicable
    recall_exercised        BOOLEAN DEFAULT FALSE,
    recall_exercised_date   DATE,
    PRIMARY KEY (worker_id, bargaining_unit_id, start_date)
);

CREATE TYPE worker_status AS ENUM (
    'active',
    'on_strike',
    'laid_off',
    'recalled',
    'suspended',
    'reinstated',
    'terminated',
    'on_leave'
);

-- Full chronological status history for a worker within a bargaining unit.
-- Each status change is a new row — nothing is overwritten.
CREATE TABLE worker_status_history (
    id                  SERIAL PRIMARY KEY,
    worker_id           INTEGER NOT NULL REFERENCES worker(id),
    bargaining_unit_id  INTEGER NOT NULL REFERENCES bargaining_unit(id),
    status              worker_status NOT NULL,
    effective_date      DATE NOT NULL,
    end_date            DATE,                           -- NULL if currently in this status
    reason              TEXT,                           -- plain English: why did status change
    notes               TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE manager (
    id              SERIAL PRIMARY KEY,
    property_id     INTEGER NOT NULL REFERENCES property(id),
    name            TEXT NOT NULL,
    title           TEXT,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE manager_property_history (
    id              SERIAL PRIMARY KEY,
    manager_id      INTEGER NOT NULL REFERENCES manager(id),
    property_id     INTEGER NOT NULL REFERENCES property(id),
    start_date      DATE NOT NULL,
    end_date        DATE,
    reason          TEXT
);

-- ---------------------------------------------------------------------------
-- Document types and enums
-- ---------------------------------------------------------------------------

CREATE TYPE cba_document_type AS ENUM (
    'ratified',
    'mou',
    'reopener'
);

CREATE TYPE proposal_type AS ENUM (
    'proposal',
    'counter_proposal',
    'tentative_agreement'
);

CREATE TYPE negotiating_side AS ENUM (
    'union',
    'management',
    'joint'
);

CREATE TYPE change_status AS ENUM (
    'proposed',
    'accepted',
    'rejected',
    'modified'
);

-- ---------------------------------------------------------------------------
-- Negotiations
-- ---------------------------------------------------------------------------

-- One per bargaining cycle for a bargaining unit.
CREATE TABLE negotiation (
    id                  SERIAL PRIMARY KEY,
    bargaining_unit_id  INTEGER NOT NULL REFERENCES bargaining_unit(id),
    ratified_cba_id     INTEGER,                        -- FK added after cba table
    start_date          DATE,
    end_date            DATE,                           -- when TA was reached
    ratification_date   DATE,                           -- when membership voted yes
    notes               TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- Mini-negotiation for MOUs and reopeners.
CREATE TABLE side_negotiation (
    id                  SERIAL PRIMARY KEY,
    parent_cba_id       INTEGER NOT NULL,               -- FK added after cba table
    result_cba_id       INTEGER,                        -- FK added after cba table
    negotiation_type    TEXT NOT NULL
                        CHECK (negotiation_type IN ('mou', 'reopener')),
    start_date          DATE,
    end_date            DATE,
    ratification_date   DATE,
    notes               TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- Every submitted document in a negotiation.
CREATE TABLE negotiation_proposal (
    id                      SERIAL PRIMARY KEY,
    negotiation_id          INTEGER REFERENCES negotiation(id),
    side_negotiation_id     INTEGER REFERENCES side_negotiation(id),
    proposal_type           proposal_type NOT NULL,
    submitted_by            negotiating_side NOT NULL,
    submitted_date          DATE,
    source_file             TEXT,
    summary                 TEXT,
    notes                   TEXT,
    created_at              TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT one_negotiation CHECK (
        (negotiation_id IS NOT NULL)::int +
        (side_negotiation_id IS NOT NULL)::int = 1
    )
);

-- Article-level changes within a proposal, with side attribution.
CREATE TABLE proposed_change (
    id                      SERIAL PRIMARY KEY,
    negotiation_proposal_id INTEGER NOT NULL REFERENCES negotiation_proposal(id),
    cba_section_id          INTEGER,                    -- FK added after cba_section table
    article_reference       TEXT,                       -- fallback if section not yet linked
    proposed_by             negotiating_side NOT NULL,
    description             TEXT NOT NULL,
    proposed_language       TEXT,
    status                  change_status NOT NULL DEFAULT 'proposed',
    resolved_in_proposal_id INTEGER REFERENCES negotiation_proposal(id),
    notes                   TEXT,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- CBAs
-- ---------------------------------------------------------------------------

CREATE TABLE cba (
    id                      SERIAL PRIMARY KEY,
    bargaining_unit_id      INTEGER REFERENCES bargaining_unit(id),
    parent_cba_id           INTEGER REFERENCES cba(id),
    successor_cba_id        INTEGER REFERENCES cba(id),
    negotiation_id          INTEGER REFERENCES negotiation(id),
    side_negotiation_id     INTEGER REFERENCES side_negotiation(id),
    document_type           cba_document_type NOT NULL DEFAULT 'ratified',
    source_file             TEXT NOT NULL UNIQUE,
    -- Denormalized for searchability before corporate hierarchy is fully built out
    employer_name           TEXT,
    union_name              TEXT,
    union_local             TEXT,
    location                TEXT,
    version_label           TEXT,
    ratification_date       DATE,
    effective_start_date    DATE,
    effective_end_date      DATE,
    expiration_date         DATE,
    reopener_date           DATE,
    is_current              BOOLEAN NOT NULL DEFAULT TRUE,
    notes                   TEXT,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    last_processed_at       TIMESTAMPTZ,

    CONSTRAINT one_current_ratified_per_unit
        EXCLUDE USING btree (bargaining_unit_id WITH =)
        WHERE (is_current = TRUE AND document_type = 'ratified' AND bargaining_unit_id IS NOT NULL)
);

-- Deferred FKs
ALTER TABLE negotiation ADD CONSTRAINT fk_negotiation_ratified_cba
    FOREIGN KEY (ratified_cba_id) REFERENCES cba(id);

ALTER TABLE side_negotiation ADD CONSTRAINT fk_side_negotiation_parent_cba
    FOREIGN KEY (parent_cba_id) REFERENCES cba(id);

ALTER TABLE side_negotiation ADD CONSTRAINT fk_side_negotiation_result_cba
    FOREIGN KEY (result_cba_id) REFERENCES cba(id);

-- ---------------------------------------------------------------------------
-- CBA sections, timing rules, corrections, holidays
-- ---------------------------------------------------------------------------

CREATE TABLE cba_section (
    id              SERIAL PRIMARY KEY,
    cba_id          INTEGER NOT NULL REFERENCES cba(id),
    article_number  TEXT NOT NULL,
    article_title   TEXT NOT NULL,
    start_char      INTEGER,
    end_char        INTEGER,
    labels          TEXT[],
    created_at      TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE (cba_id, article_number)
);

ALTER TABLE proposed_change ADD CONSTRAINT fk_proposed_change_cba_section
    FOREIGN KEY (cba_section_id) REFERENCES cba_section(id);

-- Claude's extraction — immutable after creation
CREATE TABLE timing_rule (
    id              SERIAL PRIMARY KEY,
    cba_section_id  INTEGER NOT NULL REFERENCES cba_section(id),
    rule_id         TEXT,
    action          TEXT NOT NULL,
    trigger         TEXT NOT NULL,
    offset_days     INTEGER NOT NULL,
    unit            TEXT NOT NULL,
    party           TEXT,
    quote           TEXT,
    is_ambiguous    BOOLEAN DEFAULT FALSE,
    ambiguity_note  TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Human corrections — always separate, never overwrites original
CREATE TABLE timing_rule_correction (
    id                  SERIAL PRIMARY KEY,
    timing_rule_id      INTEGER NOT NULL REFERENCES timing_rule(id),
    action              TEXT,
    trigger             TEXT,
    offset_days         INTEGER,
    unit                TEXT,
    party               TEXT,
    quote               TEXT,
    correction_note     TEXT,
    corrected_by        TEXT,
    corrected_at        TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE (timing_rule_id)
);

-- Which articles an MOU modifies in the parent CBA
CREATE TABLE mou_section (
    id                  SERIAL PRIMARY KEY,
    mou_cba_id          INTEGER NOT NULL REFERENCES cba(id),
    cba_section_id      INTEGER NOT NULL REFERENCES cba_section(id),
    modification_summary TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE (mou_cba_id, cba_section_id)
);

-- Holidays defined in the contract — never assumed
CREATE TABLE cba_holiday (
    id              SERIAL PRIMARY KEY,
    cba_id          INTEGER NOT NULL REFERENCES cba(id),
    name            TEXT NOT NULL,
    month           INTEGER,
    day             INTEGER,
    rule            TEXT,
    source_quote    TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- Worker negotiation roles
-- ---------------------------------------------------------------------------

-- Tracks a worker's participation in a specific negotiation.
-- Separate from employment status — a worker can be active AND on the
-- negotiating committee AND have been on strike in a prior cycle.
CREATE TABLE worker_negotiation_role (
    id                  SERIAL PRIMARY KEY,
    worker_id           INTEGER NOT NULL REFERENCES worker(id),
    negotiation_id      INTEGER REFERENCES negotiation(id),
    side_negotiation_id INTEGER REFERENCES side_negotiation(id),
    role                TEXT NOT NULL,              -- e.g. 'negotiating_committee', 'chief_steward', 'observer', 'ratification_delegate'
    start_date          DATE,
    end_date            DATE,
    notes               TEXT,

    CONSTRAINT one_negotiation_role CHECK (
        (negotiation_id IS NOT NULL)::int +
        (side_negotiation_id IS NOT NULL)::int = 1
    )
);

-- ---------------------------------------------------------------------------
-- Grievances
-- ---------------------------------------------------------------------------

CREATE TYPE grievance_type AS ENUM ('individual', 'group', 'policy');
CREATE TYPE grievance_status AS ENUM (
    'open', 'step_1', 'step_2', 'step_3', 'mediation',
    'arbitration', 'settled', 'withdrawn', 'denied'
);

CREATE TABLE grievance (
    id                      SERIAL PRIMARY KEY,
    cba_id                  INTEGER NOT NULL REFERENCES cba(id),
    grievance_type          grievance_type NOT NULL DEFAULT 'individual',
    status                  grievance_status NOT NULL DEFAULT 'open',
    occurrence_date         DATE NOT NULL,
    filed_date              DATE NOT NULL,
    description             TEXT NOT NULL,
    remedy_sought           TEXT,
    outcome                 TEXT,
    outcome_date            DATE,
    notes                   TEXT,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE grievance_article (
    grievance_id    INTEGER NOT NULL REFERENCES grievance(id),
    cba_section_id  INTEGER NOT NULL REFERENCES cba_section(id),
    PRIMARY KEY (grievance_id, cba_section_id)
);

CREATE TABLE grievance_respondent (
    id              SERIAL PRIMARY KEY,
    grievance_id    INTEGER NOT NULL REFERENCES grievance(id),
    corporation_id  INTEGER REFERENCES corporation(id),
    property_id     INTEGER REFERENCES property(id),
    manager_id      INTEGER REFERENCES manager(id),
    role            TEXT,

    CONSTRAINT one_respondent_type CHECK (
        (corporation_id IS NOT NULL)::int +
        (property_id IS NOT NULL)::int +
        (manager_id IS NOT NULL)::int = 1
    )
);

CREATE TABLE grievance_worker (
    grievance_id    INTEGER NOT NULL REFERENCES grievance(id),
    worker_id       INTEGER NOT NULL REFERENCES worker(id),
    is_lead         BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (grievance_id, worker_id)
);

CREATE TABLE grievance_step (
    id                  SERIAL PRIMARY KEY,
    grievance_id        INTEGER NOT NULL REFERENCES grievance(id),
    step_number         INTEGER NOT NULL,
    step_name           TEXT,
    timing_rule_id      INTEGER REFERENCES timing_rule(id),
    trigger_date        DATE NOT NULL,
    deadline_date       DATE NOT NULL,
    holiday_schedule    TEXT[],
    completed_date      DATE,
    outcome             TEXT,
    notes               TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE (grievance_id, step_number)
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------

-- Corporate hierarchy
CREATE INDEX idx_corporation_group ON corporation(corporate_group_id);
CREATE INDEX idx_property_corporation ON property(corporation_id);
CREATE INDEX idx_property_successor ON property(successor_property_id);
CREATE INDEX idx_bargaining_unit_property ON bargaining_unit(property_id);

-- Workers
CREATE INDEX idx_worker_bargaining_unit ON worker_bargaining_unit(bargaining_unit_id);
CREATE INDEX idx_worker_status_history_worker ON worker_status_history(worker_id);
CREATE INDEX idx_worker_status_history_unit ON worker_status_history(bargaining_unit_id);
CREATE INDEX idx_worker_status_history_status ON worker_status_history(status);
CREATE INDEX idx_worker_negotiation_role_worker ON worker_negotiation_role(worker_id);
CREATE INDEX idx_worker_negotiation_role_negotiation ON worker_negotiation_role(negotiation_id);
CREATE INDEX idx_manager_property ON manager(property_id);

-- Negotiations
CREATE INDEX idx_negotiation_bargaining_unit ON negotiation(bargaining_unit_id);
CREATE INDEX idx_negotiation_ratified_cba ON negotiation(ratified_cba_id);
CREATE INDEX idx_side_negotiation_parent_cba ON side_negotiation(parent_cba_id);
CREATE INDEX idx_negotiation_proposal_negotiation ON negotiation_proposal(negotiation_id);
CREATE INDEX idx_negotiation_proposal_side ON negotiation_proposal(side_negotiation_id);
CREATE INDEX idx_proposed_change_proposal ON proposed_change(negotiation_proposal_id);
CREATE INDEX idx_proposed_change_section ON proposed_change(cba_section_id);
CREATE INDEX idx_proposed_change_status ON proposed_change(status);

-- CBAs
CREATE INDEX idx_cba_bargaining_unit ON cba(bargaining_unit_id);
CREATE INDEX idx_cba_source_file ON cba(source_file);
CREATE INDEX idx_cba_parent ON cba(parent_cba_id);
CREATE INDEX idx_cba_document_type ON cba(document_type);
CREATE INDEX idx_cba_is_current ON cba(is_current);
CREATE INDEX idx_cba_expiration ON cba(expiration_date);
CREATE INDEX idx_cba_employer_name ON cba(employer_name);
CREATE INDEX idx_cba_union_name ON cba(union_name);

-- Sections and rules
CREATE INDEX idx_cba_section_cba ON cba_section(cba_id);
CREATE INDEX idx_timing_rule_section ON timing_rule(cba_section_id);
CREATE INDEX idx_timing_rule_correction ON timing_rule_correction(timing_rule_id);
CREATE INDEX idx_mou_section_mou ON mou_section(mou_cba_id);
CREATE INDEX idx_mou_section_cba_section ON mou_section(cba_section_id);

-- Grievances
CREATE INDEX idx_grievance_cba ON grievance(cba_id);
CREATE INDEX idx_grievance_status ON grievance(status);
CREATE INDEX idx_grievance_step_grievance ON grievance_step(grievance_id);
CREATE INDEX idx_grievance_step_deadline ON grievance_step(deadline_date);
CREATE INDEX idx_grievance_respondent_manager ON grievance_respondent(manager_id);

-- ---------------------------------------------------------------------------
-- Incidents
-- ---------------------------------------------------------------------------
-- Documented employer conduct — always against the company.
-- Stands alone independently of grievances.
-- A grievance can reference incidents that preceded it via grievance_id on incident.
-- An incident can exist for months before a grievance is ever filed.
-- All fields describing what happened, when, who reported it, and who witnessed
-- it are strictly required — no vague or incomplete incident records.

CREATE TYPE incident_category AS ENUM (
    'nlra_violation',       -- illegal regardless of contract (captive meetings, threats, etc.)
    'contract_violation'    -- violates a specific provision of the CBA
);

CREATE TYPE incident_type AS ENUM (
    -- NLRA violations
    'captive_meeting',          -- mandatory meeting to discourage union activity
    'threat',                   -- threatening workers for union activity
    'surveillance',             -- surveilling union activity
    'interrogation',            -- questioning workers about union membership or activity
    'promise_of_benefit',       -- promising benefits to discourage organizing
    'bargaining_refusal',       -- refusing to bargain in good faith
    'recognition_refusal',      -- refusing to recognize the union or bargaining obligation
    -- Contract violations
    'subcontracting',           -- work given to outside contractor
    'temp_staffing',            -- use of temporary agency workers for bargaining unit work
    'worker_misclassification', -- reclassifying workers as contractors or management
    'position_reclassification',-- reclassifying job title to move it outside the unit
    'work_transfer',            -- moving bargaining unit work to another facility
    'scheduling_change',        -- violating scheduling notice or hours requirements
    'layoff',                   -- reduction in force violating contract procedures
    'discipline',               -- disciplinary action violating just cause or procedure
    'safety_violation',         -- unsafe conditions violating contract or OSHA
    'wage_violation',           -- failure to pay correct wages, differentials, or benefits
    'benefits_violation',       -- failure to provide contracted benefits
    'seniority_violation',      -- violating seniority rights in promotion, layoff, or recall
    'discrimination',           -- discriminatory action grievable under the contract
    'contract_interference',    -- interfering with union rights or steward access
    'other'                     -- catchall, requires description
);

CREATE TYPE reporter_type AS ENUM (
    'worker',
    'steward',
    'officer',
    'attorney',
    'investigator',
    'journalist',
    'other'
);

CREATE TABLE incident (
    id                  SERIAL PRIMARY KEY,

    -- Where and who is affected
    bargaining_unit_id  INTEGER NOT NULL REFERENCES bargaining_unit(id),
    property_id         INTEGER NOT NULL REFERENCES property(id),

    -- Which employer entity is responsible
    -- At least one of these must be set
    corporation_id      INTEGER REFERENCES corporation(id),
    manager_id          INTEGER REFERENCES manager(id),

    -- What happened
    incident_category   incident_category NOT NULL,
    incident_type       incident_type NOT NULL,
    description         TEXT NOT NULL,
    location_detail     TEXT,                       -- specific location within property if relevant

    -- When it happened — window of time acceptable
    occurred_at_start   DATE NOT NULL,
    occurred_at_end     DATE,                       -- NULL if single moment

    -- Who reported it — required, no anonymous incident records
    reported_by_name    TEXT NOT NULL,
    reported_by_type    reporter_type NOT NULL,
    reported_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Witnesses — TEXT array, linked to worker records where possible
    witness_names       TEXT[],

    -- Optional link to grievance — NULL until a grievance is filed
    grievance_id        INTEGER REFERENCES grievance(id),

    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Data integrity: occurred_at_end must be after occurred_at_start
    CONSTRAINT valid_occurrence_window CHECK (
        occurred_at_end IS NULL OR occurred_at_end >= occurred_at_start
    ),

    -- Data integrity: reported_at must be on or after occurred_at_start
    CONSTRAINT reported_after_occurred CHECK (
        reported_at::date >= occurred_at_start
    ),

    -- At least one employer entity must be identified
    CONSTRAINT employer_identified CHECK (
        corporation_id IS NOT NULL OR manager_id IS NOT NULL
    )
);

-- Indexes
CREATE INDEX idx_incident_bargaining_unit ON incident(bargaining_unit_id);
CREATE INDEX idx_incident_property ON incident(property_id);
CREATE INDEX idx_incident_category ON incident(incident_category);
CREATE INDEX idx_incident_type ON incident(incident_type);
CREATE INDEX idx_incident_occurred ON incident(occurred_at_start);
CREATE INDEX idx_incident_grievance ON incident(grievance_id);
CREATE INDEX idx_incident_corporation ON incident(corporation_id);
CREATE INDEX idx_incident_manager ON incident(manager_id);