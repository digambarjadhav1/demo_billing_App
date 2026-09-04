-- Gross Billing App schema — SQLite dialect (local test target)
-- Structurally identical to schema_fabric.sql (T-SQL). Keep both in sync by hand;
-- there is no ORM abstraction layer in this build (kept dependency-free on purpose).

CREATE TABLE IF NOT EXISTS projects (
    project_id               TEXT PRIMARY KEY,
    project_name             TEXT NOT NULL,
    contract_value           REAL NOT NULL,
    project_duration_months  INTEGER NOT NULL,
    construction_work_type   TEXT NOT NULL,
    award_date               TEXT NOT NULL,
    status                   TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at               TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS curve_templates (
    template_id   TEXT PRIMARY KEY,
    market_type   TEXT NOT NULL,
    description   TEXT,
    is_custom     INTEGER NOT NULL DEFAULT 0,
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_by    TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS curve_template_points (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id           TEXT NOT NULL REFERENCES curve_templates(template_id),
    duration_pct_offset   REAL NOT NULL,   -- 0.0-1.0, position within normalized duration
    pct_allocation        REAL NOT NULL    -- must sum to 1.0 across all points for a template
);

CREATE TABLE IF NOT EXISTS tickets (
    ticket_id         TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL REFERENCES projects(project_id),
    ticket_type       TEXT NOT NULL,   -- STANDARD_CURVE_REQUEST | GROSS_BILLING_CURVE_UPLOAD
    submission_type   TEXT,            -- NEW | REVISED (only for GROSS_BILLING_CURVE_UPLOAD)
    status            TEXT NOT NULL DEFAULT 'SUBMITTED',
    requested_by      TEXT NOT NULL,
    requested_at      TEXT NOT NULL,
    closed_at         TEXT
);

CREATE TABLE IF NOT EXISTS standard_curve_versions (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id                TEXT NOT NULL REFERENCES projects(project_id),
    ticket_id                 TEXT NOT NULL REFERENCES tickets(ticket_id),
    template_id               TEXT NOT NULL REFERENCES curve_templates(template_id),
    contract_value_snapshot   REAL NOT NULL,
    generated_by              TEXT NOT NULL,
    generated_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS standard_curve_lines (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    standard_curve_version_id  INTEGER NOT NULL REFERENCES standard_curve_versions(id),
    project_month_number        INTEGER NOT NULL,
    pct_allocation               REAL NOT NULL,
    reference_amount             REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS budget_lines (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id               TEXT NOT NULL REFERENCES projects(project_id),
    wbs_code                 TEXT NOT NULL,
    cost_element             TEXT NOT NULL,
    fiscal_year              INTEGER NOT NULL,
    revised_budget_amount    REAL NOT NULL,
    extracted_at             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS actual_billing_lines (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id          TEXT NOT NULL REFERENCES projects(project_id),
    gl_account          TEXT NOT NULL,   -- '400000' | '410000'
    fiscal_period       TEXT NOT NULL,   -- 'YYYY-MM-01'
    month_end_balance   REAL NOT NULL,
    extracted_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gross_billing_versions (
    version_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id             TEXT NOT NULL REFERENCES projects(project_id),
    ticket_id              TEXT NOT NULL REFERENCES tickets(ticket_id),
    version_number         INTEGER NOT NULL,
    version_type           TEXT NOT NULL,   -- NEW | REVISED
    parent_version_id      INTEGER REFERENCES gross_billing_versions(version_id),
    status                 TEXT NOT NULL DEFAULT 'DRAFT',
    justification          TEXT,
    created_by             TEXT NOT NULL,
    created_at             TEXT NOT NULL,
    submitted_at           TEXT,
    approved_by            TEXT,
    approved_at            TEXT,
    approval_comments      TEXT,
    uploaded_at            TEXT,
    upload_confirmation_id TEXT
);

CREATE TABLE IF NOT EXISTS gross_billing_lines (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id            INTEGER NOT NULL REFERENCES gross_billing_versions(version_id),
    wbs_code              TEXT NOT NULL,
    cost_element          TEXT NOT NULL,
    period_month          TEXT NOT NULL,   -- 'YYYY-MM-01'
    team_projected_pct    REAL NOT NULL,
    planned_amount        REAL NOT NULL,
    is_locked             INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS upload_packages (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id            INTEGER NOT NULL REFERENCES gross_billing_versions(version_id),
    package_type          TEXT NOT NULL,   -- LOAD | REMOVAL
    file_name             TEXT NOT NULL,
    generated_at          TEXT NOT NULL,
    storage_path          TEXT NOT NULL,
    confirmation_status   TEXT NOT NULL DEFAULT 'PENDING',
    confirmed_by          TEXT,
    confirmed_at          TEXT
);

CREATE TABLE IF NOT EXISTS approval_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id    INTEGER NOT NULL REFERENCES gross_billing_versions(version_id),
    action        TEXT NOT NULL,   -- SUBMITTED | APPROVED | REJECTED | CHANGES_REQUESTED
    actor         TEXT NOT NULL,
    comments      TEXT,
    action_at     TEXT NOT NULL
);
