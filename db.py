"""
Database connection layer for the Gross Billing App prototype.

Two targets, same SQL dialect assumptions (ANSI-ish, '?' parameter markers,
no reliance on SQL-side NOW()/GETDATE() — timestamps are always generated in
Python so behavior is identical on both backends):

  target="sqlite"  -> local file DB, zero external dependencies, used for all
                      testing in this build (no network available to verify
                      the Fabric path in the environment this was written in).
  target="fabric"  -> Fabric SQL DB via pyodbc + ActiveDirectoryInteractive,
                      using the connection string supplied for this project.
                      Requires `pip install pyodbc` and the msodbcsql18 driver
                      installed on the machine running this — NOT verified in
                      this build; wire it up and test against your own tenant.

Set GROSS_BILLING_DB_TARGET=fabric (env var) to switch, or pass target= explicitly.
"""

import os
import sqlite3

# From the connection details provided for this project. Swap FABRIC_SERVER /
# FABRIC_DATABASE for your own values via environment variables rather than
# editing this file, so credentials/identifiers never get committed to source
# control.
FABRIC_SERVER = os.environ.get(
    "FABRIC_SQL_SERVER",
    "ohk6lkhiim6ezfv6gravnt3iq4-r6f7flbup3zetbjdcfvnnu4jzm.database.fabric.microsoft.com",
)
FABRIC_DATABASE = os.environ.get(
    "FABRIC_SQL_DATABASE",
    "app_db-2a48ce59-2018-41c8-a611-06cfa6aadf6f",
)


def get_fabric_odbc_connection_string() -> str:
    """
    ODBC connection string, matching the pattern supplied for this project:
    Driver={ODBC Driver 18 for SQL Server};Server=...;Database=...;Encrypt=yes;
    TrustServerCertificate=no;Authentication=ActiveDirectoryInteractive
    """
    return (
        "Driver={ODBC Driver 18 for SQL Server};"
        f"Server={FABRIC_SERVER},1433;"
        f"Database={FABRIC_DATABASE};"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
        "Authentication=ActiveDirectoryInteractive"
    )


def get_fabric_jdbc_connection_string() -> str:
    """
    Reference only — this build connects via ODBC/pyodbc (Python), not JDBC
    (Java). Kept here in case a separate Java-based ETL step is ever wired
    up against the same database.
    """
    return (
        f"jdbc:sqlserver://{FABRIC_SERVER}:1433;"
        f"database={{{FABRIC_DATABASE}}};"
        "encrypt=true;trustServerCertificate=false;"
        "authentication=ActiveDirectoryInteractive"
    )


def get_connection(target: str | None = None):
    """
    Returns a DB-API connection. target defaults to the
    GROSS_BILLING_DB_TARGET env var, or "sqlite" if unset.
    """
    target = target or os.environ.get("GROSS_BILLING_DB_TARGET", "sqlite")

    if target == "sqlite":
        sqlite_path = os.environ.get("GROSS_BILLING_SQLITE_PATH", "gross_billing.db")
        # check_same_thread=False: Streamlit reruns the script on different
        # threads as widgets are interacted with, but the connection is cached
        # once via @st.cache_resource and reused across those reruns. Plain
        # sqlite3 refuses cross-thread use by default; this is the standard,
        # safe-enough fix for a single-user local prototype like this one.
        # (Not a concurrency solution for multiple simultaneous users — see
        # README "Known gaps" if this app ever needs to support that.)
        conn = sqlite3.connect(sqlite_path, check_same_thread=False)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    if target == "fabric":
        try:
            import pyodbc  # noqa: local import — optional dependency
        except ImportError as e:
            raise RuntimeError(
                "pyodbc is not installed. Run: pip install pyodbc  "
                "(and install the 'ODBC Driver 18 for SQL Server' on this machine). "
                "This path was not testable in the environment this code was written in — "
                "verify it against your own Fabric tenant before relying on it."
            ) from e
        return pyodbc.connect(get_fabric_odbc_connection_string(), autocommit=False)

    raise ValueError(f"Unknown target: {target!r}. Expected 'sqlite' or 'fabric'.")


def init_schema(conn, target: str | None = None) -> None:
    """
    Runs the appropriate DDL file against the given connection.
    Safe to call repeatedly (DDL uses IF NOT EXISTS / IF NOT EXISTS checks).
    """
    target = target or os.environ.get("GROSS_BILLING_DB_TARGET", "sqlite")
    schema_file = "schema_sqlite.sql" if target == "sqlite" else "schema_fabric.sql"
    schema_path = os.path.join(os.path.dirname(__file__), schema_file)
    with open(schema_path, "r") as f:
        ddl = f.read()

    cur = conn.cursor()
    if target == "sqlite":
        conn.executescript(ddl)
    else:
        # T-SQL batches separated by blank-line-delimited CREATE TABLE statements;
        # pyodbc can't execute multiple statements separated by GO, so split on the
        # IF NOT EXISTS blocks and run each independently.
        raw_segments = [s.strip() for s in ddl.split("IF NOT EXISTS") if s.strip()]
        statements = ["IF NOT EXISTS" + s for s in raw_segments if "CREATE TABLE" in s]
        for stmt in statements:
            cur.execute(stmt)
        conn.commit()
