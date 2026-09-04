# Gross Billing App — Local Prototype

A locally-hosted Streamlit app implementing the ticket-driven Gross Billing workflow
described in the 4/29/26 Cost Engineering meeting notes and the Understanding
Document's 7-screen target design. Writes to either a local SQLite file (default,
zero setup) or the real Fabric SQL DB via ODBC.

## What this is / isn't

- This **replaces** the Shiny app + Excel workbook + ServiceNow-ticket-juggling
  described as the as-is process, for everything up to and including generating
  the SAP upload file(s).
- This does **not** perform a real SAP upload. Per the Understanding Document's
  scope, SAP upload execution stays manual — this app prepares the file(s) and
  records a human's confirmation after they've done the real upload themselves.
- The historical curve-matching algorithm (Standard Curve Generator) is a
  template-lookup-and-duration-scale implementation, built from the meeting
  description ("~13-15 approved market types... queries a local Excel/CSV
  data file"). **The actual Shiny app's R source was never received** — treat
  Section 2 of the engine as the best available inference, not a confirmed
  replication, until that source code is reviewed.

## What was actually tested, and how

This was built in a sandboxed environment with **no network access** — `pyodbc`,
`streamlit`, and `SQLAlchemy` could not be installed, and the real Fabric endpoint
could not be reached. To still prove the logic works, everything was built on
Python's built-in `sqlite3` as the common core, with a thin dispatch in `db.py`
that switches to `pyodbc` only when pointed at Fabric.

Actually run and passing in that environment:
- `generate_dummy_data.py` against SQLite — creates 15 templates, 12 projects,
  ~270 budget lines, ~240 actual-billing lines.
- `test_walkthrough.py` — 26 automated checks covering every ticket type, both
  validation rules (contract-value cap, budget reconciliation), both RBAC gates
  (admin-only templates, approver-only approval), period locking on rebaseline,
  the custom-curve fallback path, and — critically — the two-file (LOAD +
  REMOVAL) rebaseline upload mechanic from the meeting notes.
- `app.py`'s actual logic (not the real Streamlit UI, which isn't installable
  here) — exercised via `_streamlit_stub.py`, a minimal fake of the `streamlit`
  module that calls through to the same DB/engine code the real UI would. This
  caught two real bugs (a missing chart column shape, a stub-only gap) before
  you'd have hit them in a live run.

**Not verified in this environment, and genuinely unverified until you run them:**
- The real Streamlit UI (layout, widget behavior, session state across reruns) —
  `_streamlit_stub.py` proves the logic underneath is sound, not that the actual
  rendered app looks/behaves correctly. Run `streamlit run app.py` yourself.
- The Fabric SQL DB / pyodbc / `ActiveDirectoryInteractive` path end-to-end —
  needs your network, your driver install, your Entra ID login.
- Concurrent/multi-user behavior — this prototype assumes one interactive
  session at a time; no locking beyond what SQLite/SQL Server give you for free.

## Setup

```bash
pip install -r requirements.txt
# Also install "ODBC Driver 18 for SQL Server" on this machine if you'll connect to Fabric.
```

### Run locally against SQLite (default, no Fabric needed)

```bash
python generate_dummy_data.py     # creates gross_billing.db with seed/reference data
streamlit run app.py
```

### Run against the real Fabric SQL DB

```bash
export GROSS_BILLING_DB_TARGET=fabric
export FABRIC_SQL_SERVER="ohk6lkhiim6ezfv6gravnt3iq4-r6f7flbup3zetbjdcfvnnu4jzm.database.fabric.microsoft.com"
export FABRIC_SQL_DATABASE="app_db-2a48ce59-2018-41c8-a611-06cfa6aadf6f"

python load_to_fabric_sql.py      # creates schema + loads dummy data; opens an
                                   # Entra ID interactive sign-in the first time

streamlit run app.py              # same app, now backed by Fabric SQL DB
```

### Run the automated test suite

```bash
python test_walkthrough.py
```

## File map

| File | Purpose |
|---|---|
| `schema_sqlite.sql` | DDL, SQLite dialect |
| `schema_fabric.sql` | DDL, T-SQL dialect (Fabric SQL DB / Azure SQL) |
| `db.py` | Connection layer; switches SQLite ↔ Fabric via `GROSS_BILLING_DB_TARGET` |
| `engine.py` | All business logic — RBAC, validation, ticket/version state machine |
| `generate_dummy_data.py` | Fabricates projects, curve templates, budget, actuals |
| `load_to_fabric_sql.py` | Creates schema + loads dummy data into the real Fabric SQL DB |
| `app.py` | Streamlit UI — the 7 screens |
| `test_walkthrough.py` | End-to-end automated test (26 checks), SQLite only |
| `_streamlit_stub.py` | Dev-only fake of `streamlit`, used to logic-test `app.py` without the real package installed. Not part of the app; delete before shipping. |

## RBAC roles simulated (sidebar role switcher — replace with real auth before production)

| Role | Can do |
|---|---|
| `PROJECT_TEAM` | Create tickets, build/edit the Gross Billings curve, submit for approval |
| `COST_ENGINEERING_ADMIN` | Everything Project Team can, plus: generate standard curves, add/disable curve templates, confirm SAP uploads |
| `COE_APPROVER` | Approve / request changes on submitted versions |
| `FINANCE_READONLY` | View only — no write actions are gated to this role in the UI, but nothing offered to it either |

## Known gaps to close before this goes anywhere near production

1. **Shiny app source not yet reviewed** — the standard-curve algorithm needs
   verification against the real R logic.
2. **No real auth** — role switcher is a dropdown, not Entra ID/Saviynt-backed.
3. **No real SAP integration** — budget/actuals are dummy data standing in for
   the CJI4 report and GL 400000/410000 extracts; upload confirmation is a
   manual button click, not a real SAP API call (by design, per scope).
4. **Concurrency/locking** not addressed beyond what the DB engine gives for free.
5. **`upload_packages.storage_path`** is a mock path string — wire this up to
   actual Azure Storage (the Understanding Document's stated target repository)
   rather than treating the Fabric SQL DB row as the file itself.
