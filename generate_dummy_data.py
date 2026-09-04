"""
Generates fabricated reference and source data:
  - ~15 standard curve templates (market types), each a normalized S-curve
    summing to 1.0 across duration deciles
  - ~12 dummy projects (2 flagged for a custom one-off curve path)
  - Dummy SAP CJI4-style budget lines per project (WBS x cost element)
  - Dummy GL 400000/410000 actual billing lines for "in-progress" projects

Run standalone against SQLite:  python generate_dummy_data.py
Run against Fabric SQL DB:      see load_to_fabric_sql.py, which imports and
                                 calls generate_all() against a Fabric connection.

No external packages required — only random/datetime/math from the standard library.
"""

import random
import datetime
import math
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
import db as dbmod
import engine

random.seed(42)  # deterministic dummy data, easier to reason about in review

MARKET_TYPES = [
    "Multifamily Residential", "Healthcare", "K-12 Education", "Higher Education",
    "Federal / Government", "Data Center", "Aviation", "Hospitality",
    "Industrial / Manufacturing", "Mixed-Use", "Retail", "Life Sciences",
    "Parking Structure", "Renovation / Interiors", "Infrastructure / Civil",
]

COST_ELEMENTS = ["LABOR", "MATERIAL", "SUBCONTRACT", "EQUIPMENT", "GC_GR", "FEE"]

PROJECT_NAME_POOL = [
    "Riverside Tower", "Harborview Medical Center", "Lincoln STEM Academy",
    "Cedar Point Data Campus", "Northgate Distribution Center", "Bayfront Hotel & Conference Center",
    "Summit Ridge Apartments", "Federal Plaza Annex", "Union Station Parking Structure",
    "Willow Creek Life Sciences Building", "Eastside Retail Commons", "Grand Concourse Renovation",
]


def _beta_pdf_points(alpha: float, beta: float, n_points: int = 10) -> list:
    """Discretized, normalized beta-shaped S-curve across n_points deciles (0.1..1.0)."""
    offsets = [round((i + 1) / n_points, 2) for i in range(n_points)]
    raw = []
    for x in offsets:
        x_clamped = min(max(x, 1e-6), 1 - 1e-6)
        density = (x_clamped ** (alpha - 1)) * ((1 - x_clamped) ** (beta - 1))
        raw.append(density)
    total = sum(raw)
    return list(zip(offsets, [r / total for r in raw]))


def generate_curve_templates(conn, admin_user="rachel.costeng"):
    """One S-curve shape per market type, alpha/beta varied so shapes differ
    (front-loaded vs back-loaded vs symmetric), matching how the meeting notes
    describe distinct historical curves per market/work type."""
    for i, market_type in enumerate(MARKET_TYPES):
        alpha = 2.0 + (i % 4) * 0.5      # 2.0 - 3.5
        beta = 2.0 + ((i + 2) % 4) * 0.5  # 2.0 - 3.5
        points = _beta_pdf_points(alpha, beta, n_points=10)
        engine.add_curve_template(
            conn, role=engine.ADMIN_ROLE,
            template_id=f"TMPL_{i+1:02d}",
            market_type=market_type,
            description=f"Standard S-curve for {market_type} (alpha={alpha}, beta={beta})",
            points=points,
            created_by=admin_user,
        )
    print(f"Created {len(MARKET_TYPES)} standard curve templates.")


def generate_projects(conn, n=12):
    projects = []
    today = datetime.date.today()
    for i in range(n):
        project_id = f"25{100+i:03d}B1"
        contract_value = round(random.uniform(20_000_000, 300_000_000), 2)
        duration_months = random.choice([12, 18, 24, 30, 36, 42, 48])
        work_type = random.choice(MARKET_TYPES)
        # last two projects get flagged as custom-curve candidates (no matching template
        # will exist for them once we intentionally skip creating one, exercising the
        # "no active template found" -> admin creates a CUSTOM_<project_id> path)
        is_custom_candidate = i >= n - 2
        award_offset_months = random.randint(1, 30)
        award_date = today.replace(day=1) - datetime.timedelta(days=30 * award_offset_months)

        engine.create_project(
            conn, project_id,
            project_name=PROJECT_NAME_POOL[i % len(PROJECT_NAME_POOL)],
            contract_value=contract_value,
            project_duration_months=duration_months,
            construction_work_type=(f"CUSTOM-{work_type}" if is_custom_candidate else work_type),
            award_date=award_date.isoformat(),
        )
        projects.append({
            "project_id": project_id, "contract_value": contract_value,
            "duration_months": duration_months, "work_type": work_type,
            "award_date": award_date, "is_custom_candidate": is_custom_candidate,
        })
    print(f"Created {len(projects)} dummy projects.")
    return projects


def generate_budget_lines(conn, projects):
    n_lines = 0
    for p in projects:
        n_wbs = random.randint(4, 8)
        wbs_shares = [random.random() for _ in range(n_wbs)]
        wbs_total = sum(wbs_shares)
        fiscal_year = p["award_date"].year
        for w in range(n_wbs):
            wbs_code = f"{p['project_id']}.WBS{w+1:02d}"
            wbs_budget = p["contract_value"] * (wbs_shares[w] / wbs_total)
            n_ce = random.randint(2, len(COST_ELEMENTS))
            ce_choices = random.sample(COST_ELEMENTS, n_ce)
            ce_shares = [random.random() for _ in ce_choices]
            ce_total = sum(ce_shares)
            for ce, share in zip(ce_choices, ce_shares):
                engine.sync_budget_line(
                    conn, p["project_id"], wbs_code, ce, fiscal_year,
                    round(wbs_budget * (share / ce_total), 2),
                )
                n_lines += 1
    print(f"Created {n_lines} dummy budget lines (mimicking SAP CJI4 export).")


def generate_actuals(conn, projects):
    """Only 'in-progress' projects (award > 3 months ago) get actuals, and only
    for the months elapsed so far — everything else stays unbilled, same as a
    real project that just started."""
    today = datetime.date.today()
    n_lines = 0
    for p in projects:
        months_elapsed = (today.year - p["award_date"].year) * 12 + (today.month - p["award_date"].month)
        if months_elapsed < 3:
            continue
        monthly_run_rate = p["contract_value"] / p["duration_months"]
        for m in range(min(months_elapsed, p["duration_months"])):
            period = engine.add_months(p["award_date"], m + 1)
            noise = random.uniform(0.7, 1.3)
            balance = round(monthly_run_rate * noise, 2)
            # Split across the two GL accounts roughly 60/40, mirroring two billing accounts
            engine.sync_actual_billing(conn, p["project_id"], "400000", period.isoformat(),
                                        round(balance * 0.6, 2))
            engine.sync_actual_billing(conn, p["project_id"], "410000", period.isoformat(),
                                        round(balance * 0.4, 2))
            n_lines += 2
    print(f"Created {n_lines} dummy actual billing lines (GL 400000/410000).")


def generate_all(conn, n_projects=12):
    dbmod.init_schema(conn)
    generate_curve_templates(conn)
    projects = generate_projects(conn, n=n_projects)
    generate_budget_lines(conn, projects)
    generate_actuals(conn, projects)
    return projects


if __name__ == "__main__":
    target = os.environ.get("GROSS_BILLING_DB_TARGET", "sqlite")
    conn = dbmod.get_connection(target)
    generate_all(conn)
    conn.close()
    print(f"Done. Target={target}.")
