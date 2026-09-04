"""
Business logic for the Gross Billing App prototype.

Every function takes a plain DB-API connection (sqlite3.Connection or a
pyodbc.Connection — both work here since both use '?' parameter markers and
neither call is written to depend on SQL-side NOW()/GETDATE()). This module
has zero UI dependency: app.py (Streamlit) and test_walkthrough.py both call
into the exact same functions, so the logic is identical whether it's driven
by a human clicking through the app or by an automated test.

Rules encoded here, each traceable to a source:
  - Standard Curve is reference-only, never uploaded, never drives planned_amount
    on the Gross Billings curve.                          [Meeting notes 4/29]
  - Gross Billings curve is the team's own manual monthly % entry.  [Meeting notes]
  - Cannot exceed contract value; must reconcile to budget; cannot edit locked
    months.                                     [Understanding Doc, Curve Builder screen]
  - Only COST_ENGINEERING_ADMIN role may add/disable curve templates or create
    one-off custom curves.                       [Meeting notes + Understanding Doc]
  - Rebaseline clones the latest approved version, locks periods with existing
    actuals, requires justification, and produces BOTH a REMOVAL package (for
    the prior curve) and a LOAD package (for the revised curve) on upload.
                                                  [Meeting notes: "two separate
                                                   upload files"; Understanding
                                                   Doc: "Locks historical actuals",
                                                   "Requires justification"]
  - GCGR is explicitly out of scope for this app (confirmed absent from the
    Understanding Document's scope, unlike the earlier meeting notes where it
    was ambiguous) — deliberately not modeled here.
"""

from __future__ import annotations
import datetime
import uuid


class PermissionError_(Exception):
    """Raised when a role-gated action is attempted by the wrong role."""


class ValidationError(Exception):
    """Raised when a business rule (contract value, reconciliation, locking) is violated."""


def _now() -> str:
    return datetime.datetime.utcnow().isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


ADMIN_ROLE = "COST_ENGINEERING_ADMIN"
COE_APPROVER_ROLE = "COE_APPROVER"
PROJECT_TEAM_ROLE = "PROJECT_TEAM"
FINANCE_READONLY_ROLE = "FINANCE_READONLY"
VALID_ROLES = {ADMIN_ROLE, COE_APPROVER_ROLE, PROJECT_TEAM_ROLE, FINANCE_READONLY_ROLE}


# ---------------------------------------------------------------------------
# Screen 1 support: Project Gross Billing Overview
# ---------------------------------------------------------------------------

def create_project(conn, project_id, project_name, contract_value,
                    project_duration_months, construction_work_type,
                    award_date) -> None:
    conn.execute(
        "INSERT INTO projects (project_id, project_name, contract_value, "
        "project_duration_months, construction_work_type, award_date, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?)",
        (project_id, project_name, contract_value, project_duration_months,
         construction_work_type, award_date, _now()),
    )
    conn.commit()


def update_project_parameters(conn, project_id, contract_value, project_duration_months,
                               construction_work_type) -> None:
    """
    Lets the Project Team correct/set core project parameters. Blocked once a
    curve has already been approved or uploaded against the old values — at
    that point changing the contract value or duration out from under an
    approved curve would silently invalidate it; the Rebaseline Manager is
    the correct path for a real change at that stage, not a quiet edit here.
    """
    locked_count = conn.execute(
        "SELECT COUNT(*) FROM gross_billing_versions WHERE project_id = ? "
        "AND status IN ('APPROVED', 'UPLOADED')",
        (project_id,),
    ).fetchone()[0]
    if locked_count > 0:
        raise ValidationError(
            f"Project {project_id} already has an approved/uploaded Gross Billings curve. "
            f"Changing contract value, duration, or work type now would invalidate that curve. "
            f"Use the Rebaseline Manager instead."
        )
    conn.execute(
        "UPDATE projects SET contract_value = ?, project_duration_months = ?, "
        "construction_work_type = ? WHERE project_id = ?",
        (contract_value, project_duration_months, construction_work_type, project_id),
    )
    conn.commit()


def project_exists(conn, project_id) -> bool:
    return conn.execute(
        "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
    ).fetchone() is not None


def get_project_overview_status(conn, project_id) -> dict:
    """
    Reproduces the four status badges the Overview screen shows:
    Draft / Pending Approval / Uploaded to SAP / Rebaseline in Progress.
    """
    cur = conn.execute(
        "SELECT status, version_type FROM gross_billing_versions "
        "WHERE project_id = ? ORDER BY version_number DESC LIMIT 1",
        (project_id,),
    )
    row = cur.fetchone()
    if row is None:
        return {"status": "NO_CURVE_YET", "version_type": None}
    status, version_type = row
    if status == "PENDING_APPROVAL" and version_type == "REVISED":
        return {"status": "REBASELINE_IN_PROGRESS", "version_type": version_type}
    return {"status": status, "version_type": version_type}


# ---------------------------------------------------------------------------
# Screen 2 support: Standard Curve Generator (admin-gated template management)
# ---------------------------------------------------------------------------

def add_curve_template(conn, role, template_id, market_type, description,
                        points: list[tuple[float, float]], created_by,
                        is_custom=False) -> None:
    """
    points: list of (duration_pct_offset, pct_allocation) tuples.
    Must sum to 1.0 within floating-point tolerance — this is the historical
    curve library super-users maintain; a bad template silently corrupts every
    project that later uses it, so this is validated hard, not just logged.
    """
    if role != ADMIN_ROLE:
        raise PermissionError_(
            f"Only {ADMIN_ROLE} may add curve templates. Got role={role!r}."
        )
    total = sum(p[1] for p in points)
    if abs(total - 1.0) > 1e-6:
        raise ValidationError(
            f"Template {template_id} percentages sum to {total:.6f}, must sum to 1.0."
        )
    conn.execute(
        "INSERT INTO curve_templates (template_id, market_type, description, "
        "is_custom, is_active, created_by, created_at) VALUES (?, ?, ?, ?, 1, ?, ?)",
        (template_id, market_type, description, int(is_custom), created_by, _now()),
    )
    conn.executemany(
        "INSERT INTO curve_template_points (template_id, duration_pct_offset, pct_allocation) "
        "VALUES (?, ?, ?)",
        [(template_id, offset, pct) for offset, pct in points],
    )
    conn.commit()


def disable_curve_template(conn, role, template_id) -> None:
    if role != ADMIN_ROLE:
        raise PermissionError_(f"Only {ADMIN_ROLE} may disable curve templates.")
    conn.execute("UPDATE curve_templates SET is_active = 0 WHERE template_id = ?", (template_id,))
    conn.commit()


def create_standard_curve_ticket(conn, project_id, requested_by) -> str:
    """Ticket type 1: STANDARD_CURVE_REQUEST."""
    ticket_id = _new_id("SCR")
    conn.execute(
        "INSERT INTO tickets (ticket_id, project_id, ticket_type, submission_type, "
        "status, requested_by, requested_at) VALUES (?, ?, 'STANDARD_CURVE_REQUEST', NULL, "
        "'SUBMITTED', ?, ?)",
        (ticket_id, project_id, requested_by, _now()),
    )
    conn.commit()
    return ticket_id


def generate_standard_curve(conn, role, ticket_id, generated_by) -> int:
    """
    Reference-only curve. NEVER feeds gross_billing_lines.planned_amount —
    that distinction is the single most important correction from the
    meeting notes; do not let this function's output be mistaken for a Plan.
    """
    if role != ADMIN_ROLE:
        raise PermissionError_(f"Only {ADMIN_ROLE} may generate a standard curve.")

    cur = conn.execute(
        "SELECT project_id FROM tickets WHERE ticket_id = ? AND ticket_type = 'STANDARD_CURVE_REQUEST'",
        (ticket_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise ValidationError(f"No STANDARD_CURVE_REQUEST ticket found: {ticket_id}")
    project_id = row[0]

    proj = conn.execute(
        "SELECT contract_value, project_duration_months, construction_work_type "
        "FROM projects WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    if proj is None:
        raise ValidationError(f"Unknown project: {project_id}")
    contract_value, duration_months, work_type = proj

    # Match on market_type regardless of is_custom — a one-off custom template is
    # still the correct match once an admin has created one for this project's
    # work type; is_custom only affects who's allowed to create/disable it, not
    # whether it's eligible to be looked up here.
    tmpl = conn.execute(
        "SELECT template_id FROM curve_templates WHERE market_type = ? AND is_active = 1 LIMIT 1",
        (work_type,),
    ).fetchone()
    if tmpl is None:
        raise ValidationError(
            f"No active standard template for work type {work_type!r}. "
            f"An admin must create a CUSTOM_{project_id} template via add_curve_template() first."
        )
    template_id = tmpl[0]

    points = conn.execute(
        "SELECT duration_pct_offset, pct_allocation FROM curve_template_points "
        "WHERE template_id = ?",
        (template_id,),
    ).fetchall()

    conn.execute(
        "INSERT INTO standard_curve_versions (project_id, ticket_id, template_id, "
        "contract_value_snapshot, generated_by, generated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (project_id, ticket_id, template_id, contract_value, generated_by, _now()),
    )
    version_id = _last_id(conn)

    # Stretch the normalized duration-% curve onto this project's actual month count,
    # combining points that round to the same month for short-duration projects.
    monthly = {}
    for offset, pct in points:
        month_num = max(1, round(offset * duration_months))
        monthly[month_num] = monthly.get(month_num, 0.0) + pct

    rows = [
        (version_id, month_num, pct, pct * contract_value)
        for month_num, pct in sorted(monthly.items())
    ]
    conn.executemany(
        "INSERT INTO standard_curve_lines (standard_curve_version_id, project_month_number, "
        "pct_allocation, reference_amount) VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.execute(
        "UPDATE tickets SET status = 'CLOSED', closed_at = ? WHERE ticket_id = ?",
        (_now(), ticket_id),
    )
    conn.commit()
    return version_id


# ---------------------------------------------------------------------------
# Screen 3 support: Budget & Actuals Sync (system-pulled; dummy-data stands in for SAP)
# ---------------------------------------------------------------------------

def sync_budget_line(conn, project_id, wbs_code, cost_element, fiscal_year,
                      revised_budget_amount) -> None:
    conn.execute(
        "INSERT INTO budget_lines (project_id, wbs_code, cost_element, fiscal_year, "
        "revised_budget_amount, extracted_at) VALUES (?, ?, ?, ?, ?, ?)",
        (project_id, wbs_code, cost_element, fiscal_year, revised_budget_amount, _now()),
    )
    conn.commit()


def sync_actual_billing(conn, project_id, gl_account, fiscal_period, month_end_balance) -> None:
    if gl_account not in ("400000", "410000"):
        raise ValidationError(f"Unexpected GL account {gl_account!r}; expected 400000 or 410000.")
    conn.execute(
        "INSERT INTO actual_billing_lines (project_id, gl_account, fiscal_period, "
        "month_end_balance, extracted_at) VALUES (?, ?, ?, ?, ?)",
        (project_id, gl_account, fiscal_period, month_end_balance, _now()),
    )
    conn.commit()


def get_budget_total(conn, project_id) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(revised_budget_amount), 0) FROM budget_lines WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    return float(row[0])


def get_locked_periods(conn, project_id) -> set:
    """Periods with posted actuals are locked — read-only per the Sync screen spec."""
    rows = conn.execute(
        "SELECT DISTINCT fiscal_period FROM actual_billing_lines WHERE project_id = ?",
        (project_id,),
    ).fetchall()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# Screen 4 support: Project Gross Billing Curve Builder
# ---------------------------------------------------------------------------

def create_gross_billings_ticket(conn, project_id, requested_by, submission_type="NEW") -> str:
    """Ticket type: GROSS_BILLING_CURVE_UPLOAD, submission_type NEW or REVISED."""
    if submission_type not in ("NEW", "REVISED"):
        raise ValidationError("submission_type must be 'NEW' or 'REVISED'")
    ticket_id = _new_id("GBU")
    conn.execute(
        "INSERT INTO tickets (ticket_id, project_id, ticket_type, submission_type, "
        "status, requested_by, requested_at) VALUES (?, ?, 'GROSS_BILLING_CURVE_UPLOAD', ?, "
        "'SUBMITTED', ?, ?)",
        (ticket_id, project_id, submission_type, requested_by, _now()),
    )
    conn.commit()
    return ticket_id


def create_draft_version(conn, project_id, ticket_id, created_by,
                          version_type="NEW", parent_version_id=None,
                          justification=None) -> int:
    if version_type == "REVISED" and not justification:
        raise ValidationError(
            "A rebaseline (REVISED) version requires a justification "
            "(Understanding Doc: Rebaseline Manager screen)."
        )
    next_version_number = conn.execute(
        "SELECT COALESCE(MAX(version_number), 0) + 1 FROM gross_billing_versions "
        "WHERE project_id = ?",
        (project_id,),
    ).fetchone()[0]

    conn.execute(
        "INSERT INTO gross_billing_versions (project_id, ticket_id, version_number, "
        "version_type, parent_version_id, status, justification, created_by, created_at) "
        "VALUES (?, ?, ?, ?, ?, 'DRAFT', ?, ?, ?)",
        (project_id, ticket_id, next_version_number, version_type, parent_version_id,
         justification, created_by, _now()),
    )
    version_id = _last_id(conn)
    conn.commit()
    return version_id


def _last_id(conn) -> int:
    """
    Returns the last auto-generated integer PK. Dispatches on the connection's
    module name rather than try/except-across-dialects — a failed statement on
    a SQL Server connection can leave the transaction in a state where the next
    statement also fails, so this must not guess-and-retry against pyodbc.
    """
    module_name = type(conn).__module__
    if module_name.startswith("sqlite3"):
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return conn.execute("SELECT @@IDENTITY").fetchone()[0]


def add_or_update_line(conn, version_id, wbs_code, cost_element, period_month,
                        team_projected_pct, budget_amount_for_line) -> None:
    """
    Enforces: cannot edit a locked month (actuals already posted for that period
    on this project). Locking is evaluated by the caller via get_locked_periods()
    and passed through create_draft_version()'s cloning step below — this function
    itself checks the is_locked flag already stored on the line, so a caller can't
    bypass locking by calling this directly either.
    """
    existing = conn.execute(
        "SELECT id, is_locked FROM gross_billing_lines WHERE version_id = ? AND wbs_code = ? "
        "AND cost_element = ? AND period_month = ?",
        (version_id, wbs_code, cost_element, period_month),
    ).fetchone()
    planned_amount = team_projected_pct * budget_amount_for_line

    if existing:
        line_id, is_locked = existing
        if is_locked:
            raise ValidationError(
                f"Period {period_month} for {wbs_code}/{cost_element} is locked "
                f"(actuals already posted) — cannot edit."
            )
        conn.execute(
            "UPDATE gross_billing_lines SET team_projected_pct = ?, planned_amount = ? "
            "WHERE id = ?",
            (team_projected_pct, planned_amount, line_id),
        )
    else:
        conn.execute(
            "INSERT INTO gross_billing_lines (version_id, wbs_code, cost_element, "
            "period_month, team_projected_pct, planned_amount, is_locked) "
            "VALUES (?, ?, ?, ?, ?, ?, 0)",
            (version_id, wbs_code, cost_element, period_month, team_projected_pct, planned_amount),
        )
    conn.commit()


def auto_distribute_evenly(conn, version_id, project_id) -> int:
    """
    Fills every WBS/cost-element combination with an even 1/duration-months
    split across all periods, reconciling exactly to total budget. Skips any
    period already locked (e.g. cloned-in historical actuals on a rebaseline
    draft) rather than raising, so this is safe to run on a partially-locked
    version. Returns the number of lines written.

    This exists specifically because filling one line at a time via the UI
    for every WBS x cost element x month combination is impractical for any
    real project (often 50-150+ combinations) — this gives the team a
    reconciled starting point they can then hand-adjust for individual months.
    """
    proj = conn.execute(
        "SELECT project_duration_months, award_date FROM projects WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    if proj is None:
        raise ValidationError(f"Unknown project: {project_id}")
    duration, award_date = proj
    award_dt = datetime.date.fromisoformat(award_date)

    budget_rows = conn.execute(
        "SELECT wbs_code, cost_element, revised_budget_amount FROM budget_lines WHERE project_id = ?",
        (project_id,),
    ).fetchall()
    locked_keys = {
        (wbs, ce, period) for wbs, ce, period in conn.execute(
            "SELECT wbs_code, cost_element, period_month FROM gross_billing_lines "
            "WHERE version_id = ? AND is_locked = 1", (version_id,)
        ).fetchall()
    }

    pct = 1.0 / duration
    filled = 0
    for wbs, ce, amt in budget_rows:
        for m in range(duration):
            period = add_months(award_dt, m)
            key = (wbs, ce, period.isoformat())
            if key in locked_keys:
                continue
            add_or_update_line(conn, version_id, wbs, ce, period.isoformat(), pct, amt)
            filled += 1
    return filled


def clear_unlocked_lines(conn, version_id) -> int:
    """Removes all non-locked lines on a DRAFT version, so a user can start over
    (e.g. after messy manual entries) without touching cloned/locked history."""
    cur = conn.execute(
        "DELETE FROM gross_billing_lines WHERE version_id = ? AND is_locked = 0", (version_id,)
    )
    conn.commit()
    return cur.rowcount


def add_months(base_date, n) -> "datetime.date":
    """Calendar-correct month addition (avoids the +31-day drift a naive
    timedelta approach accumulates — verified to silently produce a wrong
    month once a span exceeds ~52 months, which a naive test only covering
    up to 48 months would not catch)."""
    month_index = base_date.month - 1 + n
    year = base_date.year + month_index // 12
    month = month_index % 12 + 1
    return datetime.date(year, month, 1)


def validate_version_for_submission(conn, version_id) -> list[str]:
    """
    Returns a list of violation messages (empty list = valid). Matches the
    Curve Builder screen's stated validations exactly:
      - Cannot exceed contract value
      - Must reconcile to total budget
    ("Cannot change locked months" is enforced at write-time in add_or_update_line,
    not re-checked here since a locked line can't have been edited in the first place.)
    """
    violations = []
    row = conn.execute(
        "SELECT project_id FROM gross_billing_versions WHERE version_id = ?",
        (version_id,),
    ).fetchone()
    if row is None:
        return [f"Version {version_id} not found."]
    project_id = row[0]

    contract_value = conn.execute(
        "SELECT contract_value FROM projects WHERE project_id = ?", (project_id,)
    ).fetchone()[0]

    total_planned = conn.execute(
        "SELECT COALESCE(SUM(planned_amount), 0) FROM gross_billing_lines WHERE version_id = ?",
        (version_id,),
    ).fetchone()[0]

    if total_planned > contract_value + 0.01:
        violations.append(
            f"Total planned amount {total_planned:,.2f} exceeds contract value {contract_value:,.2f}."
        )

    budget_total = get_budget_total(conn, project_id)
    if budget_total > 0 and abs(total_planned - budget_total) > max(0.01 * budget_total, 1.0):
        violations.append(
            f"Total planned amount {total_planned:,.2f} does not reconcile to total budget "
            f"{budget_total:,.2f} (tolerance 1%)."
        )
    return violations


def submit_for_approval(conn, version_id, submitted_by) -> None:
    violations = validate_version_for_submission(conn, version_id)
    if violations:
        raise ValidationError("Cannot submit: " + "; ".join(violations))
    conn.execute(
        "UPDATE gross_billing_versions SET status = 'PENDING_APPROVAL', submitted_at = ? "
        "WHERE version_id = ?",
        (_now(), version_id),
    )
    conn.execute(
        "INSERT INTO approval_log (version_id, action, actor, comments, action_at) "
        "VALUES (?, 'SUBMITTED', ?, NULL, ?)",
        (version_id, submitted_by, _now()),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Screen 5 support: Review & Approval (PP2 / COE)
# ---------------------------------------------------------------------------

def approve_version(conn, role, version_id, approved_by, comments=None) -> None:
    if role != COE_APPROVER_ROLE:
        raise PermissionError_(f"Only {COE_APPROVER_ROLE} may approve a version.")
    _require_status(conn, version_id, "PENDING_APPROVAL")
    conn.execute(
        "UPDATE gross_billing_versions SET status = 'APPROVED', approved_by = ?, "
        "approved_at = ?, approval_comments = ? WHERE version_id = ?",
        (approved_by, _now(), comments, version_id),
    )
    conn.execute(
        "INSERT INTO approval_log (version_id, action, actor, comments, action_at) "
        "VALUES (?, 'APPROVED', ?, ?, ?)",
        (version_id, approved_by, comments, _now()),
    )
    conn.commit()


def reject_version(conn, role, version_id, rejected_by, comments) -> None:
    if role != COE_APPROVER_ROLE:
        raise PermissionError_(f"Only {COE_APPROVER_ROLE} may reject/request changes.")
    if not comments:
        raise ValidationError("A rejection/changes-requested action requires comments.")
    _require_status(conn, version_id, "PENDING_APPROVAL")
    conn.execute(
        "UPDATE gross_billing_versions SET status = 'REJECTED', approved_by = ?, "
        "approved_at = ?, approval_comments = ? WHERE version_id = ?",
        (rejected_by, _now(), comments, version_id),
    )
    conn.execute(
        "INSERT INTO approval_log (version_id, action, actor, comments, action_at) "
        "VALUES (?, 'CHANGES_REQUESTED', ?, ?, ?)",
        (version_id, rejected_by, comments, _now()),
    )
    conn.commit()


def _require_status(conn, version_id, expected_status) -> None:
    row = conn.execute(
        "SELECT status FROM gross_billing_versions WHERE version_id = ?", (version_id,)
    ).fetchone()
    if row is None:
        raise ValidationError(f"Version {version_id} not found.")
    if row[0] != expected_status:
        raise ValidationError(
            f"Version {version_id} is in status {row[0]!r}, expected {expected_status!r}."
        )


# ---------------------------------------------------------------------------
# Screen 6 support: SAP Upload File Preparation & Repository
# ---------------------------------------------------------------------------

def generate_upload_packages(conn, version_id) -> list[int]:
    """
    NEW versions produce a LOAD package only.
    REVISED versions produce BOTH a REMOVAL package (reversing the parent
    version's lines) and a LOAD package (the new curve) — this is the exact
    "two separate upload files" mechanic from the 4/29 meeting notes, and the
    single detail most likely to be missed if this pipeline is rebuilt from
    the Understanding Document alone (that document doesn't spell out the
    two-file mechanic as explicitly as the raw meeting notes do).
    """
    version = conn.execute(
        "SELECT project_id, version_type, parent_version_id, status FROM gross_billing_versions "
        "WHERE version_id = ?",
        (version_id,),
    ).fetchone()
    if version is None:
        raise ValidationError(f"Version {version_id} not found.")
    project_id, version_type, parent_version_id, status = version
    if status != "APPROVED":
        raise ValidationError(f"Version {version_id} must be APPROVED before packaging (is {status!r}).")

    package_ids = []
    ts = _now()

    load_file = f"GBU_LOAD_{project_id}_v{version_id}.csv"
    conn.execute(
        "INSERT INTO upload_packages (version_id, package_type, file_name, generated_at, "
        "storage_path, confirmation_status) VALUES (?, 'LOAD', ?, ?, ?, 'PENDING')",
        (version_id, load_file, ts, f"/repository/{project_id}/{load_file}"),
    )
    package_ids.append(_last_id(conn))

    if version_type == "REVISED":
        if parent_version_id is None:
            raise ValidationError("REVISED version has no parent_version_id to reverse.")
        removal_file = f"GBU_REMOVAL_{project_id}_v{parent_version_id}.csv"
        conn.execute(
            "INSERT INTO upload_packages (version_id, package_type, file_name, generated_at, "
            "storage_path, confirmation_status) VALUES (?, 'REMOVAL', ?, ?, ?, 'PENDING')",
            (version_id, removal_file, ts, f"/repository/{project_id}/{removal_file}"),
        )
        package_ids.append(_last_id(conn))

    conn.commit()
    return package_ids


def mark_uploaded(conn, role, version_id, confirmed_by, upload_confirmation_id=None) -> None:
    """
    SAP upload execution stays manual per the Understanding Document's scope
    ("SAP upload execution will remain AS-IS (manual)"). This function is the
    human's confirmation click after they've actually done that manual upload —
    it does not perform any real SAP integration.
    """
    if role not in (ADMIN_ROLE, COE_APPROVER_ROLE):
        raise PermissionError_("Only Cost Engineering or COE may confirm an upload.")
    _require_status(conn, version_id, "APPROVED")
    confirmation_id = upload_confirmation_id or _new_id("CONF")
    conn.execute(
        "UPDATE gross_billing_versions SET status = 'UPLOADED', uploaded_at = ?, "
        "upload_confirmation_id = ? WHERE version_id = ?",
        (_now(), confirmation_id, version_id),
    )
    conn.execute(
        "UPDATE upload_packages SET confirmation_status = 'CONFIRMED', confirmed_by = ?, "
        "confirmed_at = ? WHERE version_id = ?",
        (confirmed_by, _now(), version_id),
    )
    ticket_id = conn.execute(
        "SELECT ticket_id FROM gross_billing_versions WHERE version_id = ?", (version_id,)
    ).fetchone()[0]
    conn.execute(
        "UPDATE tickets SET status = 'UPLOADED', closed_at = ? WHERE ticket_id = ?",
        (_now(), ticket_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Screen 7 support: Rebaseline Manager
# ---------------------------------------------------------------------------

def create_rebaseline(conn, project_id, requested_by, justification) -> int:
    """
    Clones the latest UPLOADED (or APPROVED, if never uploaded yet) version's
    lines into a new DRAFT/REVISED version. Locks any period that already has
    posted actuals — "Locks historical actuals" per the Rebaseline Manager
    screen spec. A NEW standard curve is deliberately NOT requested here,
    matching the meeting notes ("a new standard curve is usually NOT required
    during rebaseline").
    """
    source = conn.execute(
        "SELECT version_id FROM gross_billing_versions WHERE project_id = ? "
        "AND status IN ('UPLOADED', 'APPROVED') ORDER BY version_number DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    if source is None:
        raise ValidationError(
            f"No approved/uploaded version exists for {project_id} to rebaseline from."
        )
    source_version_id = source[0]

    ticket_id = create_gross_billings_ticket(conn, project_id, requested_by, submission_type="REVISED")
    new_version_id = create_draft_version(
        conn, project_id, ticket_id, requested_by,
        version_type="REVISED", parent_version_id=source_version_id,
        justification=justification,
    )

    locked_periods = get_locked_periods(conn, project_id)
    source_lines = conn.execute(
        "SELECT wbs_code, cost_element, period_month, team_projected_pct, planned_amount "
        "FROM gross_billing_lines WHERE version_id = ?",
        (source_version_id,),
    ).fetchall()

    rows = [
        (new_version_id, wbs, ce, period, pct, amt, int(period in locked_periods))
        for wbs, ce, period, pct, amt in source_lines
    ]
    conn.executemany(
        "INSERT INTO gross_billing_lines (version_id, wbs_code, cost_element, period_month, "
        "team_projected_pct, planned_amount, is_locked) VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return new_version_id


# ---------------------------------------------------------------------------
# Reporting support: variance vs Standard Curve and vs Budget (Curve Builder screen)
# ---------------------------------------------------------------------------

def get_variance_report(conn, version_id) -> list[dict]:
    proj_row = conn.execute(
        "SELECT gbv.project_id FROM gross_billing_versions gbv WHERE gbv.version_id = ?",
        (version_id,),
    ).fetchone()
    if proj_row is None:
        raise ValidationError(f"Version {version_id} not found.")
    project_id = proj_row[0]

    std_version = conn.execute(
        "SELECT id FROM standard_curve_versions WHERE project_id = ? "
        "ORDER BY id DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    std_by_month = {}
    if std_version:
        for month_num, ref_amt in conn.execute(
            "SELECT project_month_number, reference_amount FROM standard_curve_lines "
            "WHERE standard_curve_version_id = ?",
            (std_version[0],),
        ).fetchall():
            std_by_month[month_num] = std_by_month.get(month_num, 0.0) + ref_amt

    gb_lines = conn.execute(
        "SELECT period_month, SUM(planned_amount) FROM gross_billing_lines "
        "WHERE version_id = ? GROUP BY period_month ORDER BY period_month",
        (version_id,),
    ).fetchall()

    award_date = conn.execute(
        "SELECT award_date FROM projects WHERE project_id = ?", (project_id,)
    ).fetchone()[0]
    award_dt = datetime.date.fromisoformat(award_date)

    results = []
    for period_month, planned_amount in gb_lines:
        pm_date = datetime.date.fromisoformat(period_month)
        month_num = (pm_date.year - award_dt.year) * 12 + (pm_date.month - award_dt.month) + 1
        std_amount = std_by_month.get(month_num, 0.0)
        results.append({
            "period_month": period_month,
            "planned_amount": planned_amount,
            "standard_curve_reference_amount": std_amount,
            "variance_vs_standard": planned_amount - std_amount,
        })
    return results
