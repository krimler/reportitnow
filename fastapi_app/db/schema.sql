-- ReportItNow POC schema — see design.md §4
-- SQLite syntax. Production: same tables on PostgreSQL via SQLAlchemy.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    jurisdiction TEXT NOT NULL,
    gender_scope TEXT NOT NULL DEFAULT 'inclusive', -- 'statutory' | 'inclusive'
    workforce_size INTEGER,
    employer_type TEXT,
    config_json TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL REFERENCES entities(id),
    subject_id TEXT NOT NULL,
    email TEXT NOT NULL,
    display_name TEXT,
    password_hash TEXT,                              -- Argon2id; NULL if IdP-managed
    date_of_birth DATE,
    is_minor INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_id, subject_id)
);

CREATE TABLE IF NOT EXISTS role_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    role TEXT NOT NULL,
    case_id INTEGER,                                  -- NULL for entity-wide roles
    valid_from DATE NOT NULL,
    valid_to DATE,
    revoked_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS icc_committees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL REFERENCES entities(id),
    constituted_on DATE NOT NULL,
    tenure_expires_on DATE NOT NULL,
    reconstitution_alert_sent_on DATE,
    defective_flag INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS icc_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    committee_id INTEGER NOT NULL REFERENCES icc_committees(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    member_type TEXT NOT NULL,
    nominated_on DATE NOT NULL,
    removed_on DATE,
    removal_reason TEXT
);

CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL REFERENCES entities(id),
    committee_id INTEGER REFERENCES icc_committees(id),
    state TEXT NOT NULL DEFAULT 'draft',
    routed_to TEXT NOT NULL DEFAULT 'icc',           -- 'icc' | 'lcc' | 'pocso_police'
    complainant_user_id INTEGER REFERENCES users(id),
    respondent_user_id INTEGER REFERENCES users(id),
    respondent_is_employer INTEGER NOT NULL DEFAULT 0,
    cross_organisational INTEGER NOT NULL DEFAULT 0,
    minor_complainant INTEGER NOT NULL DEFAULT 0,
    incident_date DATE NOT NULL,
    incident_continuing INTEGER NOT NULL DEFAULT 0,
    filed_at TIMESTAMP,
    inquiry_started_at TIMESTAMP,
    report_due_by DATE,
    employer_action_due_by DATE,
    appeal_due_by DATE,
    closed_at TIMESTAMP,
    closure_reason TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS case_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    event_type TEXT NOT NULL,
    event_payload_json TEXT NOT NULL,
    actor_user_id INTEGER NOT NULL REFERENCES users(id),
    occurred_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    audit_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS case_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    doc_type TEXT NOT NULL,
    content_blob BLOB,
    content_hash TEXT NOT NULL,
    ai_component TEXT,
    is_draft INTEGER NOT NULL DEFAULT 1,
    authorised_by_user_id INTEGER REFERENCES users(id),
    authorised_at TIMESTAMP,
    served_to_respondent_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS hearings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    scheduled_for TIMESTAMP NOT NULL,
    held_on TIMESTAMP,
    quorum_met INTEGER,
    complainant_present INTEGER,
    respondent_present INTEGER,
    notice_issued_on DATE,
    notice_period_days INTEGER NOT NULL DEFAULT 15,
    is_ex_parte INTEGER NOT NULL DEFAULT 0,
    consecutive_no_shows_complainant INTEGER NOT NULL DEFAULT 0,
    consecutive_no_shows_respondent INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS hearing_attendance (
    hearing_id INTEGER NOT NULL REFERENCES hearings(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    role_at_hearing TEXT NOT NULL,
    present INTEGER NOT NULL,
    PRIMARY KEY (hearing_id, user_id)
);

-- Append-only audit chain. The application database role has SELECT + INSERT
-- only; UPDATE / DELETE are not granted. (Enforced at the SQLAlchemy layer in
-- this POC; in production, separate DB role.)
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seq INTEGER NOT NULL,
    entity_id INTEGER NOT NULL REFERENCES entities(id),
    case_id INTEGER REFERENCES cases(id),
    component_id TEXT NOT NULL,
    actor_user_id INTEGER NOT NULL REFERENCES users(id),
    input_hash TEXT NOT NULL,
    output_hash TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL,
    UNIQUE(entity_id, seq)
);

CREATE TABLE IF NOT EXISTS holiday_calendar (
    entity_id INTEGER NOT NULL REFERENCES entities(id),
    holiday_date DATE NOT NULL,
    description TEXT,
    PRIMARY KEY (entity_id, holiday_date)
);

CREATE TABLE IF NOT EXISTS consent_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    purpose TEXT NOT NULL,
    granted INTEGER NOT NULL,
    granted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    audit_hash TEXT
);

-- DP query budget cache (§7.2 — budget consumed at most once per FY per entity)
CREATE TABLE IF NOT EXISTS dp_release_cache (
    entity_id INTEGER NOT NULL REFERENCES entities(id),
    fy TEXT NOT NULL,
    dataset_version TEXT NOT NULL,
    output_json TEXT NOT NULL,
    epsilon_spent REAL NOT NULL,
    released_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (entity_id, fy, dataset_version)
);

-- Cross-session chat history. Persisted server-side so a user re-opening
-- Chainlit in a new tab / browser sees their prior conversation. Keyed by
-- (user_id, role) because one person can hold multiple roles (e.g. PO who is
-- also an ICC member runs separate IA_CHAT and CPA_CHAT threads).
CREATE TABLE IF NOT EXISTS chat_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    role TEXT NOT NULL,                    -- 'complainant' | 'respondent' | ...
    case_id INTEGER REFERENCES cases(id),  -- NULL when chat is entity-scoped
    turn_role TEXT NOT NULL,               -- 'user' | 'assistant'
    content TEXT NOT NULL,
    component_id TEXT,                     -- e.g. 'CA_CHAT'
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_chat_history_user_role
    ON chat_history(user_id, role, created_at);

-- Sessions table (Chainlit-issued tokens map to (user_id, role hints))
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_role_assignments_user ON role_assignments(user_id);
CREATE INDEX IF NOT EXISTS idx_role_assignments_case ON role_assignments(case_id);
CREATE INDEX IF NOT EXISTS idx_case_events_case ON case_events(case_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_entity_seq ON audit_log(entity_id, seq);
CREATE INDEX IF NOT EXISTS idx_cases_entity_state ON cases(entity_id, state);
