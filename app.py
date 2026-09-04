"""
Gross Billing App — local Streamlit prototype.

Implements the 7 screens from the Understanding Document:
  1. Project Gross Billing Overview
  2. Standard Curve Generator (admin-gated)
  3. Budget & Actuals Sync (read-only view of synced data)
  4. Project Gross Billing Curve Builder
  5. Review & Approval (PP2/COE)
  6. SAP Upload File Preparation & Repository
  7. Rebaseline Manager

Run:  streamlit run app.py
Requires: pip install streamlit  (not available/verified in the sandbox this
was authored in — syntax-checked with py_compile only; run and click through
it yourself before treating it as done).

Role switcher in the sidebar simulates RBAC (Project Team / Cost Engineering
Admin / COE Approver / Finance read-only) without wiring up real auth — swap
this for your actual identity provider before going anywhere near production.
"""

import os
import sys
import datetime

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(__file__))
import db as dbmod
import engine


st.set_page_config(page_title="Gross Billing App", layout="wide")


@st.cache_resource
def get_conn():
    conn = dbmod.get_connection()
    dbmod.init_schema(conn)
    return conn


conn = get_conn()

st.sidebar.title("Gross Billing App")
role = st.sidebar.selectbox(
    "Acting as role",
    [engine.PROJECT_TEAM_ROLE, engine.ADMIN_ROLE, engine.COE_APPROVER_ROLE, engine.FINANCE_READONLY_ROLE],
)
user = st.sidebar.text_input("User name", value="demo.user")

screen = st.sidebar.radio(
    "Screen",
    [
        "1. Project Overview",
        "2. Standard Curve Generator",
        "3. Budget & Actuals Sync",
        "4. Gross Billing Curve Builder",
        "5. Review & Approval",
        "6. SAP Upload File Prep",
        "7. Rebaseline Manager",
    ],
)

projects = conn.execute("SELECT project_id, project_name FROM projects ORDER BY project_id").fetchall()

if not projects and screen != "1. Project Overview":
    st.warning(
        "No projects exist yet. Go to '1. Project Overview' to register one, "
        "or run generate_dummy_data.py to seed sample data."
    )
    st.stop()

if not projects:
    project_id = None
else:
    project_labels = [f"{pid} — {name}" for pid, name in projects]
    selected_label = st.sidebar.selectbox("Project", project_labels)
    project_id = selected_label.split(" — ")[0]


# ---------------------------------------------------------------------------
def screen_overview():
    st.header("1. Project Gross Billing Overview")

    with st.expander("➕ Register a new project"):
        st.caption("For a project that was just awarded and doesn't exist in this system yet.")
        standard_types = [r[0] for r in conn.execute(
            "SELECT DISTINCT market_type FROM curve_templates WHERE is_custom = 0 ORDER BY market_type"
        ).fetchall()]
        with st.form("register_project_form"):
            new_project_id = st.text_input("Project ID (matches SAP project number)")
            new_project_name = st.text_input("Project Name")
            new_contract_value = st.number_input("Contract Value ($)", min_value=0.0, step=100_000.0)
            new_duration = st.number_input("Project Duration (months)", min_value=1, max_value=120, value=24)
            is_custom_type = st.checkbox("This is a custom/one-off work type not in the standard list")
            if is_custom_type:
                new_work_type = st.text_input("Custom work type name")
            else:
                new_work_type = st.selectbox("Construction / Work Type", standard_types)
            new_award_date = st.date_input("Award Date")
            submitted = st.form_submit_button("Register Project")

        if submitted:
            if not new_project_id or not new_project_name:
                st.error("Project ID and Project Name are required.")
            elif engine.project_exists(conn, new_project_id):
                st.error(f"Project {new_project_id} already exists.")
            elif is_custom_type and not new_work_type:
                st.error("Enter a name for the custom work type.")
            else:
                engine.create_project(
                    conn, new_project_id, new_project_name, new_contract_value,
                    int(new_duration), new_work_type, new_award_date.isoformat(),
                )
                st.success(f"Registered {new_project_id}. Select it from the sidebar.")
                st.rerun()

    if project_id is None:
        st.info("No projects registered yet. Use the form above to register the first one.")
        return

    proj = conn.execute(
        "SELECT project_name, contract_value, project_duration_months, construction_work_type, award_date "
        "FROM projects WHERE project_id = ?", (project_id,)
    ).fetchone()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Contract Value", f"${proj[1]:,.0f}")
    c2.metric("Duration", f"{proj[2]} months")
    c3.metric("Work Type", proj[3])
    c4.metric("Award Date", proj[4])

    with st.expander("✏️ Edit this project's parameters"):
        st.caption(
            "Correct or override these values (e.g. if SAP data was wrong or not yet synced "
            "when the project was first registered). Blocked once a curve has been approved/uploaded — "
            "use Rebaseline Manager at that point instead."
        )
        standard_types = [r[0] for r in conn.execute(
            "SELECT DISTINCT market_type FROM curve_templates WHERE is_custom = 0 ORDER BY market_type"
        ).fetchall()]
        with st.form("edit_project_form"):
            edit_contract_value = st.number_input(
                "Contract Value ($)", min_value=0.0, step=100_000.0, value=float(proj[1])
            )
            edit_duration = st.number_input(
                "Project Duration (months)", min_value=1, max_value=120, value=int(proj[2])
            )
            work_type_options = standard_types if proj[3] in standard_types else standard_types + [proj[3]]
            edit_work_type = st.selectbox(
                "Construction / Work Type", work_type_options,
                index=work_type_options.index(proj[3]) if proj[3] in work_type_options else 0,
            )
            edit_submitted = st.form_submit_button("Save Changes")

        if edit_submitted:
            try:
                engine.update_project_parameters(
                    conn, project_id, edit_contract_value, int(edit_duration), edit_work_type
                )
                st.success("Project parameters updated.")
                st.rerun()
            except engine.ValidationError as e:
                st.error(str(e))

    status = engine.get_project_overview_status(conn, project_id)
    badge = {
        "NO_CURVE_YET": "⚪ No curve yet",
        "DRAFT": "🟡 Draft",
        "PENDING_APPROVAL": "🟠 Pending Approval",
        "REBASELINE_IN_PROGRESS": "🟠 Rebaseline in Progress",
        "APPROVED": "🟢 Approved (not yet uploaded)",
        "REJECTED": "🔴 Changes Requested",
        "UPLOADED": "✅ Uploaded to SAP",
    }.get(status["status"], status["status"])
    st.subheader(f"Status: {badge}")

    st.markdown("**Version history**")
    versions = conn.execute(
        "SELECT version_id, version_number, version_type, status, created_at FROM gross_billing_versions "
        "WHERE project_id = ? ORDER BY version_number", (project_id,)
    ).fetchall()
    if versions:
        st.table(
            [{"Version": v[1], "Type": v[2], "Status": v[3], "Created": v[4]} for v in versions]
        )
    else:
        st.info("No Gross Billings curve versions yet for this project.")


# ---------------------------------------------------------------------------
def screen_standard_curve():
    st.header("2. Standard Curve Generator")
    st.caption("Reference/benchmark only — never uploaded to SAP, never drives the Plan.")

    if role == engine.ADMIN_ROLE:
        with st.expander("Admin: manage historical curve library"):
            templates = conn.execute(
                "SELECT template_id, market_type, is_custom, is_active FROM curve_templates ORDER BY template_id"
            ).fetchall()
            st.table([{"Template": t[0], "Market Type": t[1], "Custom": bool(t[2]), "Active": bool(t[3])}
                       for t in templates])
            disable_id = st.selectbox("Disable a template", [""] + [t[0] for t in templates if t[3]])
            if st.button("Disable selected template") and disable_id:
                engine.disable_curve_template(conn, role, disable_id)
                st.success(f"Disabled {disable_id}")
                st.rerun()

    st.subheader("Request a standard curve")
    if st.button("Submit Standard Curve Request ticket"):
        ticket_id = engine.create_standard_curve_ticket(conn, project_id, requested_by=user)
        st.success(f"Ticket {ticket_id} submitted.")

    open_tickets = conn.execute(
        "SELECT ticket_id FROM tickets WHERE project_id = ? AND ticket_type = 'STANDARD_CURVE_REQUEST' "
        "AND status = 'SUBMITTED'", (project_id,)
    ).fetchall()
    if open_tickets:
        ticket_id = st.selectbox("Open standard curve tickets", [t[0] for t in open_tickets])
        if role == engine.ADMIN_ROLE:
            if st.button("Generate standard curve for this ticket"):
                try:
                    version_id = engine.generate_standard_curve(conn, role, ticket_id, generated_by=user)
                    st.success(f"Standard curve generated (version {version_id}).")
                    st.rerun()
                except (engine.ValidationError, engine.PermissionError_) as e:
                    st.error(str(e))
        else:
            st.info("Only Cost Engineering Admin can generate the curve for an open ticket.")

    std_versions = conn.execute(
        "SELECT id, generated_at FROM standard_curve_versions WHERE project_id = ? ORDER BY id DESC",
        (project_id,),
    ).fetchall()
    if std_versions:
        latest_id, generated_at = std_versions[0]
        st.subheader(f"Latest standard curve (generated {generated_at})")
        lines = conn.execute(
            "SELECT project_month_number, pct_allocation, reference_amount FROM standard_curve_lines "
            "WHERE standard_curve_version_id = ? ORDER BY project_month_number", (latest_id,)
        ).fetchall()
        chart_df = pd.DataFrame(lines, columns=["Project Month", "Pct", "Reference Amount"])
        chart_df = chart_df.set_index("Project Month")[["Reference Amount"]]
        st.line_chart(chart_df)


# ---------------------------------------------------------------------------
def screen_budget_actuals():
    st.header("3. Budget & Actuals Sync")
    st.caption("System-pulled (dummy data stands in for a live SAP integration in this prototype).")

    budget = conn.execute(
        "SELECT wbs_code, cost_element, revised_budget_amount FROM budget_lines WHERE project_id = ? "
        "ORDER BY wbs_code, cost_element", (project_id,)
    ).fetchall()
    st.subheader("Budget by WBS / Cost Element (from SAP CJI4)")
    st.dataframe(
        [{"WBS": w, "Cost Element": ce, "Revised Budget": f"${amt:,.2f}"} for w, ce, amt in budget],
        use_container_width=True,
    )
    st.metric("Total Budget", f"${engine.get_budget_total(conn, project_id):,.2f}")

    actuals = conn.execute(
        "SELECT fiscal_period, SUM(month_end_balance) FROM actual_billing_lines WHERE project_id = ? "
        "GROUP BY fiscal_period ORDER BY fiscal_period", (project_id,)
    ).fetchall()
    st.subheader("Actual Billings (GL 400000/410000, month-end balance)")
    if actuals:
        st.bar_chart({period: amt for period, amt in actuals})
        locked = engine.get_locked_periods(conn, project_id)
        st.caption(f"{len(locked)} period(s) are locked (read-only) because actuals are posted.")
    else:
        st.info("No actuals posted yet for this project.")


# ---------------------------------------------------------------------------
def screen_curve_builder():
    st.header("4. Project Gross Billing Curve Builder")

    draft_versions = conn.execute(
        "SELECT version_id, version_number, version_type FROM gross_billing_versions "
        "WHERE project_id = ? AND status = 'DRAFT' ORDER BY version_number DESC", (project_id,)
    ).fetchall()

    if not draft_versions:
        st.subheader("Start a new Gross Billings curve")
        if st.button("Create ticket + draft version (NEW)"):
            ticket_id = engine.create_gross_billings_ticket(conn, project_id, user, submission_type="NEW")
            engine.create_draft_version(conn, project_id, ticket_id, user, version_type="NEW")
            st.success("Draft version created.")
            st.rerun()
        return

    version_id = draft_versions[0][0]
    st.subheader(f"Editing draft version {draft_versions[0][1]} ({draft_versions[0][2]})")

    st.info(
        "Filling every WBS/cost-element/month combination one at a time is impractical "
        "for a real project. Use **Auto-fill** to reconcile instantly with an even spread, "
        "then adjust individual months below if the team's actual billing timing differs."
    )
    c1, c2 = st.columns(2)
    if c1.button("Auto-fill: distribute budget evenly across all periods"):
        filled = engine.auto_distribute_evenly(conn, version_id, project_id)
        st.success(f"Filled {filled} line(s) evenly. Adjust individual months below if needed.")
        st.rerun()
    if c2.button("Clear all unlocked lines (start over)"):
        removed = engine.clear_unlocked_lines(conn, version_id)
        st.warning(f"Removed {removed} line(s).")
        st.rerun()

    budget = conn.execute(
        "SELECT wbs_code, cost_element, revised_budget_amount FROM budget_lines WHERE project_id = ?",
        (project_id,),
    ).fetchall()
    proj_duration, award_date = conn.execute(
        "SELECT project_duration_months, award_date FROM projects WHERE project_id = ?", (project_id,)
    ).fetchone()
    award_dt = datetime.date.fromisoformat(award_date)

    wbs_ce_options = [(w, ce) for w, ce, _ in budget]
    wbs_choice, ce_choice = st.selectbox(
        "WBS / Cost Element", wbs_ce_options, format_func=lambda x: f"{x[0]} / {x[1]}"
    )
    budget_amt = next(amt for w, ce, amt in budget if w == wbs_choice and ce == ce_choice)
    month_offset = st.slider("Project month #", 1, proj_duration, 1)
    period = engine.add_months(award_dt, month_offset - 1)
    pct = st.number_input("Team's billing % for this month", min_value=0.0, max_value=1.0, value=0.05, step=0.01)

    if st.button("Save line"):
        try:
            engine.add_or_update_line(conn, version_id, wbs_choice, ce_choice, period.isoformat(), pct, budget_amt)
            st.success("Saved.")
        except engine.ValidationError as e:
            st.error(str(e))

    lines = conn.execute(
        "SELECT wbs_code, cost_element, period_month, team_projected_pct, planned_amount, is_locked "
        "FROM gross_billing_lines WHERE version_id = ? ORDER BY period_month", (version_id,)
    ).fetchall()
    st.dataframe(
        [{"WBS": l[0], "Cost Elem": l[1], "Period": l[2], "%": f"{l[3]:.1%}",
          "Planned $": f"${l[4]:,.2f}", "Locked": bool(l[5])} for l in lines],
        use_container_width=True,
    )

    total_planned = sum(l[4] for l in lines)
    st.metric("Total planned to date", f"${total_planned:,.2f}")

    violations = engine.validate_version_for_submission(conn, version_id)
    if violations:
        for v in violations:
            st.warning(v)
    else:
        st.success("Reconciles to budget and contract value.")

    if st.button("Submit for PP2 / COE approval", disabled=bool(violations)):
        try:
            engine.submit_for_approval(conn, version_id, user)
            st.success("Submitted for approval.")
            st.rerun()
        except engine.ValidationError as e:
            st.error(str(e))


# ---------------------------------------------------------------------------
def screen_approval():
    st.header("5. Review & Approval (PP2 / COE)")
    pending = conn.execute(
        "SELECT version_id, version_number, version_type, submitted_at FROM gross_billing_versions "
        "WHERE project_id = ? AND status = 'PENDING_APPROVAL'", (project_id,)
    ).fetchall()
    if not pending:
        st.info("Nothing pending approval for this project.")
        return

    for version_id, version_number, version_type, submitted_at in pending:
        st.subheader(f"Version {version_number} ({version_type}) — submitted {submitted_at}")
        variance = engine.get_variance_report(conn, version_id)
        if variance:
            chart_df = pd.DataFrame(variance).set_index("period_month")[
                ["planned_amount", "standard_curve_reference_amount"]
            ]
            chart_df.columns = ["Planned", "Standard Curve Reference"]
            st.line_chart(chart_df)
        comments = st.text_area(f"Comments (v{version_number})", key=f"comments_{version_id}")
        c1, c2 = st.columns(2)
        if c1.button("Approve", key=f"approve_{version_id}"):
            try:
                engine.approve_version(conn, role, version_id, user, comments)
                st.success("Approved.")
                st.rerun()
            except (engine.ValidationError, engine.PermissionError_) as e:
                st.error(str(e))
        if c2.button("Request Changes", key=f"reject_{version_id}"):
            try:
                engine.reject_version(conn, role, version_id, user, comments)
                st.warning("Changes requested.")
                st.rerun()
            except (engine.ValidationError, engine.PermissionError_) as e:
                st.error(str(e))


# ---------------------------------------------------------------------------
def screen_upload():
    st.header("6. SAP Upload File Preparation & Repository")
    st.caption("SAP upload execution stays manual per scope — this screen prepares the file(s) and "
               "records the human confirmation after the real upload happens.")

    approved = conn.execute(
        "SELECT version_id, version_number, version_type FROM gross_billing_versions "
        "WHERE project_id = ? AND status = 'APPROVED'", (project_id,)
    ).fetchall()
    for version_id, version_number, version_type in approved:
        st.subheader(f"Version {version_number} ({version_type}) — APPROVED, ready to package")
        if st.button(f"Generate upload package(s) for v{version_number}", key=f"pkg_{version_id}"):
            pkg_ids = engine.generate_upload_packages(conn, version_id)
            st.success(f"Generated {len(pkg_ids)} package(s).")
            st.rerun()

    st.subheader("Generated packages awaiting manual SAP upload")
    packages = conn.execute(
        "SELECT up.id, gbv.version_number, up.package_type, up.file_name, up.storage_path, "
        "up.confirmation_status FROM upload_packages up "
        "JOIN gross_billing_versions gbv ON gbv.version_id = up.version_id "
        "WHERE gbv.project_id = ? ORDER BY up.id DESC", (project_id,)
    ).fetchall()
    st.dataframe(
        [{"Version": p[1], "Type": p[2], "File": p[3], "Path": p[4], "Status": p[5]} for p in packages],
        use_container_width=True,
    )

    pending_pkg_versions = sorted({p[1] for p in packages if p[5] == "PENDING"})
    if pending_pkg_versions and role in (engine.ADMIN_ROLE, engine.COE_APPROVER_ROLE):
        v_num = st.selectbox("Mark version as uploaded (after completing the manual SAP upload)",
                              pending_pkg_versions)
        v_id = conn.execute(
            "SELECT version_id FROM gross_billing_versions WHERE project_id = ? AND version_number = ?",
            (project_id, v_num),
        ).fetchone()[0]
        if st.button("Confirm manual SAP upload complete"):
            engine.mark_uploaded(conn, role, v_id, confirmed_by=user)
            st.success("Marked as uploaded. Ticket closed.")
            st.rerun()


# ---------------------------------------------------------------------------
def screen_rebaseline():
    st.header("7. Rebaseline Manager")
    st.caption("Clones the latest approved/uploaded version. A new standard curve is NOT requested "
               "(per the as-is process — teams don't revisit the standard curve after initial setup).")

    has_source = conn.execute(
        "SELECT 1 FROM gross_billing_versions WHERE project_id = ? AND status IN ('UPLOADED', 'APPROVED')",
        (project_id,),
    ).fetchone()
    if not has_source:
        st.info("No approved/uploaded version exists yet to rebaseline from.")
        return

    trigger = st.selectbox("Rebaseline trigger", ["Major change order", "Duration change", "Executive directive"])
    justification = st.text_area("Justification (required)")
    if st.button("Create Rebaseline"):
        try:
            new_version_id = engine.create_rebaseline(conn, project_id, user,
                                                        justification=f"[{trigger}] {justification}")
            st.success(f"Rebaseline draft created (version {new_version_id}). "
                       f"Go to screen 4 to adjust the billing spread, then resubmit.")
            st.rerun()
        except engine.ValidationError as e:
            st.error(str(e))


# ---------------------------------------------------------------------------
{
    "1. Project Overview": screen_overview,
    "2. Standard Curve Generator": screen_standard_curve,
    "3. Budget & Actuals Sync": screen_budget_actuals,
    "4. Gross Billing Curve Builder": screen_curve_builder,
    "5. Review & Approval": screen_approval,
    "6. SAP Upload File Prep": screen_upload,
    "7. Rebaseline Manager": screen_rebaseline,
}[screen]()
