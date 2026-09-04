# Gross Billing App — Streamlit Cloud Demo

A Streamlit app implementing the ticket-driven Gross Billing workflow.
This demo uses SQLite for data storage.

## Live Demo

🚀 **[View Live App](https://demo-billing-app.streamlit.app)** _(will be available after deployment)_

## What this app does

- Project Gross Billing Overview
- Standard Curve Generator (admin-gated)
- Budget & Actuals Sync (read-only view)
- Project Gross Billing Curve Builder
- Review & Approval (PP2/COE)
- SAP Upload File Preparation & Repository
- Rebaseline Manager

## Local Setup

```bash
pip install -r requirements.txt
python generate_dummy_data.py     # creates gross_billing.db with seed data
streamlit run app.py
```

## RBAC Roles (Simulated)

| Role | Can do |
|---|---|
| `PROJECT_TEAM` | Create tickets, build/edit curves, submit for approval |
| `COST_ENGINEERING_ADMIN` | Generate standard curves, add/disable templates, confirm uploads |
| `COE_APPROVER` | Approve / request changes on submitted versions |
| `FINANCE_READONLY` | View only |

## Note

This is a prototype application. The role switcher is a demo dropdown, not real authentication.
