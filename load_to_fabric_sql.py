"""
Creates the schema and loads fabricated reference/source data into the actual
Fabric SQL DB, using the ODBC connection string for this project.

NOT executed or verified in the environment this was written in — there is no
network access there, so pyodbc/the driver/the live tenant were never reachable.
Run this on a machine that has:
  1. `pip install pyodbc`
  2. The "ODBC Driver 18 for SQL Server" installed
     (https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server)
  3. Network access to the Fabric workspace and an Entra ID account with write
     access to the target database (ActiveDirectoryInteractive will pop a
     browser-based sign-in the first time it runs)

Usage:
    python load_to_fabric_sql.py
    python load_to_fabric_sql.py --projects 20      # generate more/fewer dummy projects
    python load_to_fabric_sql.py --server <host> --database <db>   # override target
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import db as dbmod
import generate_dummy_data as gen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--projects", type=int, default=12)
    parser.add_argument("--server", default=None, help="Overrides FABRIC_SQL_SERVER")
    parser.add_argument("--database", default=None, help="Overrides FABRIC_SQL_DATABASE")
    args = parser.parse_args()

    if args.server:
        os.environ["FABRIC_SQL_SERVER"] = args.server
    if args.database:
        os.environ["FABRIC_SQL_DATABASE"] = args.database

    print(f"Connecting to Fabric SQL DB...")
    print(f"  Server:   {dbmod.FABRIC_SERVER}")
    print(f"  Database: {dbmod.FABRIC_DATABASE}")
    print("  (An Entra ID interactive sign-in window may open — complete it to proceed.)")

    conn = dbmod.get_connection("fabric")
    try:
        print("Connected. Creating schema (idempotent — safe to re-run)...")
        dbmod.init_schema(conn, target="fabric")
        print("Schema ready. Generating and loading dummy data...")
        os.environ["GROSS_BILLING_DB_TARGET"] = "fabric"
        gen.generate_all(conn, n_projects=args.projects)
        print("Done. Dummy data loaded into Fabric SQL DB.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
