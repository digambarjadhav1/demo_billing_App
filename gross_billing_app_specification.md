# Gross Billing App — Specification Prompt

This document is written to be handed to a developer or an AI coding agent as a
standalone build brief. It synthesizes: the original As-Is/To-Be architecture
diagrams, the 4/29/26 Cost Engineering meeting notes, the formal Understanding
Document (7-screen target design), and the working prototype already built
against these requirements (see `/app` in this deliverable).

---

## 1. Problem Statement

Clark Construction's Gross Billings process — how a project's billing curve is
created, reviewed, approved, and uploaded into SAP — is entirely manual today:
a Shiny (R) app generates a one-time reference curve, a locked Excel workbook
carries budget and the team's own billing projections, and two ServiceNow
tickets gate two separate manual SAP uploads. Build an application that
replicates this workflow's business rules faithfully, replacing Excel/email
handoffs with a governed, auditable, versioned system — while leaving the
actual SAP upload as a manual step (explicitly out of scope to automate).

## 2. Two Curve Objects — Do Not Conflate

| | Standard Curve | Gross Billings Curve |
|---|---|---|
| Nature | System-generated reference/benchmark | Project team's own manual monthly billing % judgment |
| Basis | Lookup against ~13-15 named market/work-type templates (plus rare one-off customs), scaled by project duration, multiplied by contract value | Team enters % (or $) per WBS/cost-element/month, informed by — not derived from — the standard curve |
| Used for | PP2 meeting reference only | The actual Plan uploaded to SAP; source of the KPI report's Plan line |
| Revisited after initial setup? | No | Yes, continuously, including on rebaseline |
| Who can create/edit the underlying library | Cost Engineering Admin (super-user) only | N/A — this curve itself has no reusable "library"; each project's curve is unique to that project |

**This distinction is the single most important design constraint.** If the
Standard Curve is ever mistaken for the Plan anywhere in the system (a report,
an integration, a UI label), every downstream number is wrong.

## 3. Four Ticket-Driven Business Situations to Support

1. **Standard Curve Request** — team submits contract value, duration,
   construction/work type → Cost Engineering Admin generates the reference
   curve → ticket auto-closes.
2. **Gross Billings Curve Upload (NEW)** — team builds their own curve against
   synced budget data → submits for PP2/COE approval → on approval, one LOAD
   package is generated → manual SAP upload → confirmation closes the ticket.
3. **Gross Billings Curve Upload (REVISED / Rebaseline)** — triggered by a
   major change order, duration change, or executive directive. Clones the
   latest approved/uploaded version (does **not** request a new Standard
   Curve), locks any period with already-posted actuals, requires a written
   justification, and on approval produces **two** packages: a REMOVAL package
   (reversing the prior curve's line items in SAP) and a LOAD package (the
   revised curve). This two-file mechanic is explicit in the raw meeting notes
   and easy to miss if working from the Understanding Document alone.
4. **GCGR (General Conditions/Requirements)** — mentioned in the raw meeting
   notes as a separate ticket type triggered by PACE budget entry.
   **Confirmed out of scope for this application** per the formal Understanding
   Document (which does not mention it at all). Do not build it; if it
   resurfaces as a requirement later, treat it as a new, separate initiative.

## 4. The 7 Screens (Understanding Document, verbatim scope)

1. **Project Gross Billing Overview** — status badges (Draft / Pending Approval
   / Uploaded to SAP / Rebaseline in Progress), navigation hub, version history.
2. **Standard Curve Generator** — admin-gated; template library management
   (add/disable, one-off customs); generates and versions the reference curve.
3. **Budget & Actuals Sync** — system-pulled (from SAP CJI4 report for budget;
   GL accounts 400000/410000 for actuals); locks past periods read-only.
4. **Project Gross Billing Curve Builder** — guided monthly % entry; live
   variance vs. Standard Curve and vs. Budget; validations: cannot exceed
   contract value, cannot edit locked months, must reconcile to total budget.
5. **Review & Approval (PP2/COE)** — approve / request changes, with comments,
   timestamps, and an approved-version snapshot.
6. **SAP Upload File Preparation & Repository** — converts the approved curve
   into the SAP-ready file structure(s) (LOAD, and REMOVAL for rebaselines),
   stores them in a common repository, tracks manual-upload confirmation.
7. **Rebaseline Manager** — explicit "Create Rebaseline" action; clones latest
   approved version; requires justification; feeds back into the Curve
   Builder → Approval → Upload loop.

## 5. Roles

| Role | Permissions |
|---|---|
| Project Team | Create tickets, build/edit their own project's curve, submit for approval |
| Cost Engineering (Admin/super-user) | All of the above, plus: manage the historical curve template library, generate standard curves, confirm manual SAP uploads |
| COE Approver (PP2 governance) | Approve or reject/request-changes on submitted versions |
| Finance | Read-only across all screens |

This maps directly onto the RBAC design already discussed for the broader
Forecasting App (Entra ID authentication, Saviynt-driven role/permission
mapping) — reuse that identity layer rather than inventing a parallel one.

## 6. Data Model

Twelve tables, in dependency order: `projects`, `curve_templates`,
`curve_template_points`, `tickets`, `standard_curve_versions`,
`standard_curve_lines`, `budget_lines`, `actual_billing_lines`,
`gross_billing_versions`, `gross_billing_lines`, `upload_packages`,
`approval_log`. Full DDL provided in both SQLite and T-SQL dialects
(`schema_sqlite.sql`, `schema_fabric.sql`). Design notes:

- `gross_billing_versions.parent_version_id` self-references to preserve
  rebaseline lineage — every revision is traceable back to what it superseded.
- `gross_billing_lines.is_locked` is set at clone time (rebaseline) based on
  whether actuals already exist for that period; enforced at write-time so it
  cannot be bypassed by a direct edit call.
- `upload_packages.package_type` (`LOAD` | `REMOVAL`) is the two-file
  rebaseline mechanic made explicit in the schema, not just in application logic.

## 7. Source Systems (confirmed, not assumed)

| Data | Confirmed source |
|---|---|
| Budget | SAP CJI4 report (Cost Line Items), by WBS and cost element |
| Actuals | GL account balances, specifically accounts **400000** and **410000**, month-end balance in the Universal Journal (ACDOCA in S/4HANA) |
| Curve reference data | A local Excel/CSV file, historically accessible only to Cost Engineering super-users — this app's `curve_templates`/`curve_template_points` tables are the intended replacement for that file |

**Open/unconfirmed at time of writing:** the exact algorithm the legacy Shiny
app uses to match/scale historical curves. The implementation here (lookup by
market type, scale by duration-percentage-offset, multiply by contract value)
is inferred from the meeting description, not verified against the R source.
Re-validate this section once that source code is obtained.

## 8. Explicit Non-Goals (Out of Scope)

- Automating the actual SAP upload — stays a manual human action, confirmed
  via the app after the fact.
- Any change to Salesforce/PACE/SAP upstream award-intake processes.
- Redesigning SAP's chart of accounts or billing-revenue GL structure.
- GCGR / General Conditions-Requirements as a merged or parallel feature.
- Enterprise-wide forecasting model redesign beyond Gross Billings itself.

## 9. Acceptance Criteria

- [ ] All four ticket-based situations (Standard Curve Request, NEW upload,
      REVISED/rebaseline upload — GCGR excluded) are representable end-to-end
      via the app and persisted in the database.
- [ ] A NEW version produces exactly one LOAD package on approval.
- [ ] A REVISED (rebaseline) version produces both a REMOVAL and a LOAD package.
- [ ] Submission is blocked if total planned amount exceeds contract value or
      fails to reconcile to total budget.
- [ ] A period with posted actuals cannot be edited on a cloned rebaseline version.
- [ ] Only the Cost Engineering Admin role can add/disable curve templates or
      generate a standard curve.
- [ ] Only the COE Approver role can approve or reject a submitted version.
- [ ] Every approval/rejection action is recorded in an append-only audit log
      with actor, timestamp, and comments.
- [ ] The same business-logic code path is exercised whether driven by the UI
      or by an automated test (no UI-only shortcuts around validation rules).

## 10. Delivered Prototype

A working implementation against these exact requirements exists under
`/app` in this deliverable: SQLite-first (zero-dependency local testing),
with a parallel T-SQL schema and a `pyodbc`-based loader for the real Fabric
SQL DB target. See `app/README.md` for what has and hasn't been verified —
notably, the Fabric/pyodbc network path was written but could not be tested
in the environment it was built in, and should be verified against a live
tenant before being treated as production-ready.
