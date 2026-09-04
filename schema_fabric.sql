-- Gross Billing App schema — T-SQL dialect (Fabric SQL DB target)
-- Structurally identical to schema_sqlite.sql. Run once via load_to_fabric_sql.py
-- or a SQL client connected with the ODBC/JDBC string from the app's config.

IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'projects')
CREATE TABLE projects (
    project_id               NVARCHAR(20)   NOT NULL PRIMARY KEY,
    project_name             NVARCHAR(200)  NOT NULL,
    contract_value           DECIMAL(18,2)  NOT NULL,
    project_duration_months  INT            NOT NULL,
    construction_work_type   NVARCHAR(60)   NOT NULL,
    award_date               DATE           NOT NULL,
    status                   NVARCHAR(20)   NOT NULL DEFAULT 'ACTIVE',
    created_at               DATETIME2      NOT NULL
);

IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'curve_templates')
CREATE TABLE curve_templates (
    template_id   NVARCHAR(40)  NOT NULL PRIMARY KEY,
    market_type   NVARCHAR(60)  NOT NULL,
    description   NVARCHAR(400),
    is_custom     BIT           NOT NULL DEFAULT 0,
    is_active     BIT           NOT NULL DEFAULT 1,
    created_by    NVARCHAR(100) NOT NULL,
    created_at    DATETIME2     NOT NULL
);

IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'curve_template_points')
CREATE TABLE curve_template_points (
    id                    INT IDENTITY(1,1) PRIMARY KEY,
    template_id           NVARCHAR(40) NOT NULL FOREIGN KEY REFERENCES curve_templates(template_id),
    duration_pct_offset   DECIMAL(9,6) NOT NULL,
    pct_allocation        DECIMAL(9,6) NOT NULL
);

IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'tickets')
CREATE TABLE tickets (
    ticket_id         NVARCHAR(40)  NOT NULL PRIMARY KEY,
    project_id        NVARCHAR(20)  NOT NULL FOREIGN KEY REFERENCES projects(project_id),
    ticket_type       NVARCHAR(40)  NOT NULL,
    submission_type   NVARCHAR(20),
    status            NVARCHAR(20)  NOT NULL DEFAULT 'SUBMITTED',
    requested_by      NVARCHAR(100) NOT NULL,
    requested_at      DATETIME2     NOT NULL,
    closed_at         DATETIME2
);

IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'standard_curve_versions')
CREATE TABLE standard_curve_versions (
    id                        INT IDENTITY(1,1) PRIMARY KEY,
    project_id                NVARCHAR(20)  NOT NULL FOREIGN KEY REFERENCES projects(project_id),
    ticket_id                 NVARCHAR(40)  NOT NULL FOREIGN KEY REFERENCES tickets(ticket_id),
    template_id               NVARCHAR(40)  NOT NULL FOREIGN KEY REFERENCES curve_templates(template_id),
    contract_value_snapshot   DECIMAL(18,2) NOT NULL,
    generated_by              NVARCHAR(100) NOT NULL,
    generated_at              DATETIME2     NOT NULL
);

IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'standard_curve_lines')
CREATE TABLE standard_curve_lines (
    id                          INT IDENTITY(1,1) PRIMARY KEY,
    standard_curve_version_id  INT NOT NULL FOREIGN KEY REFERENCES standard_curve_versions(id),
    project_month_number         INT NOT NULL,
    pct_allocation                DECIMAL(9,6) NOT NULL,
    reference_amount              DECIMAL(18,2) NOT NULL
);

IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'budget_lines')
CREATE TABLE budget_lines (
    id                       INT IDENTITY(1,1) PRIMARY KEY,
    project_id               NVARCHAR(20) NOT NULL FOREIGN KEY REFERENCES projects(project_id),
    wbs_code                 NVARCHAR(30) NOT NULL,
    cost_element             NVARCHAR(30) NOT NULL,
    fiscal_year              INT NOT NULL,
    revised_budget_amount    DECIMAL(18,2) NOT NULL,
    extracted_at             DATETIME2 NOT NULL
);

IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'actual_billing_lines')
CREATE TABLE actual_billing_lines (
    id                  INT IDENTITY(1,1) PRIMARY KEY,
    project_id          NVARCHAR(20) NOT NULL FOREIGN KEY REFERENCES projects(project_id),
    gl_account          NVARCHAR(10) NOT NULL,
    fiscal_period       DATE NOT NULL,
    month_end_balance   DECIMAL(18,2) NOT NULL,
    extracted_at        DATETIME2 NOT NULL
);

IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'gross_billing_versions')
CREATE TABLE gross_billing_versions (
    version_id             INT IDENTITY(1,1) PRIMARY KEY,
    project_id             NVARCHAR(20) NOT NULL FOREIGN KEY REFERENCES projects(project_id),
    ticket_id              NVARCHAR(40) NOT NULL FOREIGN KEY REFERENCES tickets(ticket_id),
    version_number         INT NOT NULL,
    version_type           NVARCHAR(20) NOT NULL,
    parent_version_id      INT FOREIGN KEY REFERENCES gross_billing_versions(version_id),
    status                 NVARCHAR(20) NOT NULL DEFAULT 'DRAFT',
    justification          NVARCHAR(1000),
    created_by             NVARCHAR(100) NOT NULL,
    created_at             DATETIME2 NOT NULL,
    submitted_at           DATETIME2,
    approved_by            NVARCHAR(100),
    approved_at            DATETIME2,
    approval_comments      NVARCHAR(1000),
    uploaded_at            DATETIME2,
    upload_confirmation_id NVARCHAR(60)
);

IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'gross_billing_lines')
CREATE TABLE gross_billing_lines (
    id                    INT IDENTITY(1,1) PRIMARY KEY,
    version_id            INT NOT NULL FOREIGN KEY REFERENCES gross_billing_versions(version_id),
    wbs_code              NVARCHAR(30) NOT NULL,
    cost_element          NVARCHAR(30) NOT NULL,
    period_month          DATE NOT NULL,
    team_projected_pct    DECIMAL(9,6) NOT NULL,
    planned_amount        DECIMAL(18,2) NOT NULL,
    is_locked             BIT NOT NULL DEFAULT 0
);

IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'upload_packages')
CREATE TABLE upload_packages (
    id                    INT IDENTITY(1,1) PRIMARY KEY,
    version_id            INT NOT NULL FOREIGN KEY REFERENCES gross_billing_versions(version_id),
    package_type          NVARCHAR(10) NOT NULL,
    file_name             NVARCHAR(200) NOT NULL,
    generated_at          DATETIME2 NOT NULL,
    storage_path          NVARCHAR(400) NOT NULL,
    confirmation_status   NVARCHAR(20) NOT NULL DEFAULT 'PENDING',
    confirmed_by          NVARCHAR(100),
    confirmed_at          DATETIME2
);

IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'approval_log')
CREATE TABLE approval_log (
    id            INT IDENTITY(1,1) PRIMARY KEY,
    version_id    INT NOT NULL FOREIGN KEY REFERENCES gross_billing_versions(version_id),
    action        NVARCHAR(30) NOT NULL,
    actor         NVARCHAR(100) NOT NULL,
    comments      NVARCHAR(1000),
    action_at     DATETIME2 NOT NULL
);
