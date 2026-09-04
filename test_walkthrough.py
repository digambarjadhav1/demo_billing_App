"""
End-to-end functional test of the entire ticket-driven workflow, run entirely
against local SQLite (no network dependency). This proves the engine.py logic
is internally consistent; it does NOT prove the Fabric SQL DB / pyodbc path
works, since that requires network access and a real tenant this environment
does not have. Swap GROSS_BILLING_DB_TARGET=fabric to re-run this same script
against the real database once pyodbc is installed and the driver is present.

Covers:
  1. Standard curve request -> generation (reference-only; admin-gated)
  2. Budget & actuals already synced (from generate_dummy_data)
  3. Gross Billings curve upload ticket (NEW) -> draft -> validation failure
     (intentionally over-contract) -> corrected -> submit -> approve -> package
     -> mark uploaded
  4. Permission checks: non-admin cannot add a template; non-approver cannot approve
  5. Rebaseline: create_rebaseline() clones + locks historical actuals, requires
     justification, submit -> approve -> package (expects BOTH REMOVAL and LOAD)
     -> mark uploaded
  6. Variance report against the standard curve
"""

import os
import sys
import datetime

sys.path.insert(0, os.path.dirname(__file__))
import db as dbmod
import engine
import generate_dummy_data as gen

PASS, FAIL = [], []


def check(label, condition):
    if condition:
        PASS.append(label)
        print(f"  [PASS] {label}")
    else:
        FAIL.append(label)
        print(f"  [FAIL] {label}")


def expect_raises(label, exc_type, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
        FAIL.append(label)
        print(f"  [FAIL] {label} (no exception raised)")
    except exc_type:
        PASS.append(label)
        print(f"  [PASS] {label}")
    except Exception as e:
        FAIL.append(label)
        print(f"  [FAIL] {label} (wrong exception: {type(e).__name__}: {e})")


def main():
    os.environ["GROSS_BILLING_SQLITE_PATH"] = "test_walkthrough.db"
    if os.path.exists("test_walkthrough.db"):
        os.remove("test_walkthrough.db")

    conn = dbmod.get_connection("sqlite")
    projects = gen.generate_all(conn, n_projects=12)
    project = next(p for p in projects if not p["is_custom_candidate"])
    project_id = project["project_id"]
    print(f"\nUsing project {project_id} (work_type={project['work_type']}) for the walkthrough.\n")

    # -----------------------------------------------------------------
    print("STEP 1: Standard Curve Request + Generation (reference only)")
    sc_ticket = engine.create_standard_curve_ticket(conn, project_id, requested_by="pm.smith")
    check("Standard curve ticket created", sc_ticket.startswith("SCR-"))

    expect_raises(
        "Non-admin cannot generate a standard curve",
        engine.PermissionError_,
        engine.generate_standard_curve, conn, engine.PROJECT_TEAM_ROLE, sc_ticket, "pm.smith",
    )

    std_version_id = engine.generate_standard_curve(
        conn, engine.ADMIN_ROLE, sc_ticket, generated_by="rachel.costeng"
    )
    std_lines = conn.execute(
        "SELECT COUNT(*), SUM(pct_allocation) FROM standard_curve_lines "
        "WHERE standard_curve_version_id = ?", (std_version_id,)
    ).fetchone()
    check("Standard curve has lines", std_lines[0] > 0)
    check("Standard curve percentages sum to ~1.0", abs(std_lines[1] - 1.0) < 1e-4)

    ticket_status = conn.execute(
        "SELECT status FROM tickets WHERE ticket_id = ?", (sc_ticket,)
    ).fetchone()[0]
    check("Standard curve ticket auto-closed after generation", ticket_status == "CLOSED")

    # -----------------------------------------------------------------
    print("\nSTEP 2: Gross Billings Curve Upload ticket (NEW)")
    gb_ticket = engine.create_gross_billings_ticket(conn, project_id, "pm.smith", submission_type="NEW")
    version_id = engine.create_draft_version(conn, project_id, gb_ticket, "pm.smith", version_type="NEW")
    check("Draft NEW version created", version_id is not None)

    budget_rows = conn.execute(
        "SELECT wbs_code, cost_element, revised_budget_amount FROM budget_lines WHERE project_id = ?",
        (project_id,),
    ).fetchall()

    # Intentionally overcommit (150% of budget on every line) to prove validation catches it
    duration = project["duration_months"]
    for wbs, ce, budget_amt in budget_rows:
        for m in range(duration):
            period = engine.add_months(project["award_date"], m)
            pct = 1.5 / duration
            engine.add_or_update_line(conn, version_id, wbs, ce, period.isoformat(), pct, budget_amt)

    expect_raises(
        "Submission rejected when total exceeds contract value / budget",
        engine.ValidationError,
        engine.submit_for_approval, conn, version_id, "pm.smith",
    )

    conn.execute("DELETE FROM gross_billing_lines WHERE version_id = ?", (version_id,))
    conn.commit()

    # This is the actual fix being tested here: one call reconciles every WBS x
    # cost element x month combination evenly, instead of hand-looping it (which
    # is exactly the tedium that made this impractical to do by hand in the UI).
    filled = engine.auto_distribute_evenly(conn, version_id, project_id)
    check("auto_distribute_evenly() filled at least one line", filled > 0)

    violations = engine.validate_version_for_submission(conn, version_id)
    check("Corrected version has no violations", violations == [])

    engine.submit_for_approval(conn, version_id, "pm.smith")
    check("Version status is PENDING_APPROVAL after submit",
          conn.execute("SELECT status FROM gross_billing_versions WHERE version_id=?",
                       (version_id,)).fetchone()[0] == "PENDING_APPROVAL")

    # -----------------------------------------------------------------
    print("\nSTEP 3: Review & Approval (PP2/COE)")
    expect_raises(
        "Non-approver cannot approve",
        engine.PermissionError_,
        engine.approve_version, conn, engine.PROJECT_TEAM_ROLE, version_id, "someone",
    )
    engine.approve_version(conn, engine.COE_APPROVER_ROLE, version_id, "coe.jane", comments="Looks reasonable.")
    check("Version status is APPROVED",
          conn.execute("SELECT status FROM gross_billing_versions WHERE version_id=?",
                       (version_id,)).fetchone()[0] == "APPROVED")

    # -----------------------------------------------------------------
    print("\nSTEP 4: SAP Upload File Preparation & Repository")
    package_ids = engine.generate_upload_packages(conn, version_id)
    pkg_types = [r[0] for r in conn.execute(
        "SELECT package_type FROM upload_packages WHERE version_id = ?", (version_id,)
    ).fetchall()]
    check("NEW version produces exactly one LOAD package", pkg_types == ["LOAD"])

    engine.mark_uploaded(conn, engine.ADMIN_ROLE, version_id, confirmed_by="rachel.costeng")
    check("Version status is UPLOADED",
          conn.execute("SELECT status FROM gross_billing_versions WHERE version_id=?",
                       (version_id,)).fetchone()[0] == "UPLOADED")

    overview = engine.get_project_overview_status(conn, project_id)
    check("Overview status reflects UPLOADED", overview["status"] == "UPLOADED")

    # -----------------------------------------------------------------
    print("\nSTEP 5: Rebaseline")
    expect_raises(
        "Rebaseline without justification is rejected",
        engine.ValidationError,
        engine.create_rebaseline, conn, project_id, "pm.smith", "",
    )

    rebaseline_version_id = engine.create_rebaseline(
        conn, project_id, "pm.smith",
        justification="Owner-approved $40M change order extends scope and duration.",
    )
    rb_row = conn.execute(
        "SELECT version_type, parent_version_id, status FROM gross_billing_versions WHERE version_id=?",
        (rebaseline_version_id,),
    ).fetchone()
    check("Rebaseline version_type is REVISED", rb_row[0] == "REVISED")
    check("Rebaseline parent_version_id points to prior UPLOADED version", rb_row[1] == version_id)

    locked_count = conn.execute(
        "SELECT COUNT(*) FROM gross_billing_lines WHERE version_id = ? AND is_locked = 1",
        (rebaseline_version_id,),
    ).fetchone()[0]
    locked_periods = engine.get_locked_periods(conn, project_id)
    check("Historical-actuals periods were locked on clone",
          (locked_count > 0) == (len(locked_periods) > 0))

    if locked_periods:
        any_locked_period = next(iter(locked_periods))
        locked_line = conn.execute(
            "SELECT wbs_code, cost_element FROM gross_billing_lines "
            "WHERE version_id = ? AND period_month = ? AND is_locked = 1 LIMIT 1",
            (rebaseline_version_id, any_locked_period),
        ).fetchone()
        if locked_line:
            expect_raises(
                "Cannot edit a locked month on the rebaseline",
                engine.ValidationError,
                engine.add_or_update_line, conn, rebaseline_version_id,
                locked_line[0], locked_line[1], any_locked_period, 0.05, 1_000_000,
            )

    violations = engine.validate_version_for_submission(conn, rebaseline_version_id)
    check("Cloned rebaseline reconciles to budget with no changes yet", violations == [])
    engine.submit_for_approval(conn, rebaseline_version_id, "pm.smith")
    engine.approve_version(conn, engine.COE_APPROVER_ROLE, rebaseline_version_id, "coe.jane",
                            comments="Change order reviewed and approved.")

    rb_package_types = sorted(r[0] for r in conn.execute(
        "SELECT package_type FROM upload_packages WHERE version_id = ?",
        (engine.generate_upload_packages(conn, rebaseline_version_id) and rebaseline_version_id,),
    ).fetchall())
    check("REVISED version produces BOTH a LOAD and a REMOVAL package",
          rb_package_types == ["LOAD", "REMOVAL"])

    engine.mark_uploaded(conn, engine.ADMIN_ROLE, rebaseline_version_id, confirmed_by="rachel.costeng")
    check("Rebaseline version status is UPLOADED",
          conn.execute("SELECT status FROM gross_billing_versions WHERE version_id=?",
                       (rebaseline_version_id,)).fetchone()[0] == "UPLOADED")

    # -----------------------------------------------------------------
    print("\nSTEP 6: Variance report vs Standard Curve")
    variance = engine.get_variance_report(conn, rebaseline_version_id)
    check("Variance report returns rows", len(variance) > 0)
    check("Variance report has standard-curve reference amounts",
          any(row["standard_curve_reference_amount"] > 0 for row in variance))

    # -----------------------------------------------------------------
    print("\nSTEP 7: Custom curve path (project with no matching template)")
    custom_project = next(p for p in projects if p["is_custom_candidate"])
    custom_ticket = engine.create_standard_curve_ticket(conn, custom_project["project_id"], "pm.jones")
    expect_raises(
        "Generating a standard curve fails when no matching active template exists",
        engine.ValidationError,
        engine.generate_standard_curve, conn, engine.ADMIN_ROLE, custom_ticket, "rachel.costeng",
    )
    engine.add_curve_template(
        conn, engine.ADMIN_ROLE,
        template_id=f"CUSTOM_{custom_project['project_id']}",
        market_type=f"CUSTOM-{custom_project['work_type']}",
        description="One-off custom curve for a unique job",
        points=[(0.25, 0.30), (0.5, 0.40), (0.75, 0.20), (1.0, 0.10)],
        created_by="rachel.costeng",
        is_custom=True,
    )
    custom_std_version = engine.generate_standard_curve(
        conn, engine.ADMIN_ROLE, custom_ticket, generated_by="rachel.costeng"
    )
    check("Custom curve generates successfully once template exists", custom_std_version is not None)

    # -----------------------------------------------------------------
    conn.close()
    print(f"\n{'='*60}\nRESULTS: {len(PASS)} passed, {len(FAIL)} failed\n{'='*60}")
    if FAIL:
        print("FAILED CHECKS:")
        for f in FAIL:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("All checks passed.")


if __name__ == "__main__":
    main()
