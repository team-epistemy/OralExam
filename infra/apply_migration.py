"""Apply migration_002_modules.sql directly to the RDS instance.

Reads DB credentials from AWS Secrets Manager (epistemy/db-dev by default),
connects to the Aurora Postgres cluster, and executes the migration SQL.

Usage:
    PYTHONPATH=. python -m infra.apply_migration
    PYTHONPATH=. python -m infra.apply_migration --secret epistemy/db-prod
    PYTHONPATH=. python -m infra.apply_migration --migration path/to/file.sql
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

import boto3

# Default paths and settings
DEFAULT_SECRET = "epistemy/db-dev"
DEFAULT_REGION = "us-west-2"
DEFAULT_DB_NAME = "epistemy"
MIGRATION_FILE = (
    pathlib.Path(__file__).resolve().parent.parent
    / "backend" / "db" / "migration_002_modules.sql"
)


def get_db_credentials(secret_name: str, region: str) -> dict:
    """Fetch and parse DB credentials from AWS Secrets Manager."""
    sm = boto3.client("secretsmanager", region_name=region)
    raw = sm.get_secret_value(SecretId=secret_name)["SecretString"]
    return json.loads(raw)


def connect(creds: dict, db_name: str):
    """Open a psycopg2 connection using the fetched credentials."""
    import psycopg2
    return psycopg2.connect(
        host=creds["host"],
        port=creds.get("port", 5432),
        dbname=creds.get("dbname", db_name),
        user=creds["username"],
        password=creds["password"],
        connect_timeout=15,
    )


def apply_migration(conn, sql_path: pathlib.Path) -> None:
    """Read the migration file and execute it within the connection."""
    sql = sql_path.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply Epistemy DB migration")
    parser.add_argument(
        "--secret", default=DEFAULT_SECRET,
        help=f"Secrets Manager secret name (default: {DEFAULT_SECRET})",
    )
    parser.add_argument(
        "--region", default=DEFAULT_REGION,
        help=f"AWS region (default: {DEFAULT_REGION})",
    )
    parser.add_argument(
        "--db-name", default=DEFAULT_DB_NAME,
        help=f"Database name (default: {DEFAULT_DB_NAME})",
    )
    parser.add_argument(
        "--migration", type=pathlib.Path, default=MIGRATION_FILE,
        help=f"Path to migration SQL file (default: {MIGRATION_FILE})",
    )
    args = parser.parse_args()

    migration_path = args.migration.resolve()
    if not migration_path.exists():
        print(f"ERROR: Migration file not found: {migration_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Migration file : {migration_path}")
    print(f"Secret name    : {args.secret}")
    print(f"Region         : {args.region}")
    print(f"Database       : {args.db_name}")
    print()

    # Step 1: Fetch credentials
    print("Fetching DB credentials from Secrets Manager...")
    try:
        creds = get_db_credentials(args.secret, args.region)
    except Exception as e:
        print(f"ERROR: Failed to fetch secret '{args.secret}': {e}", file=sys.stderr)
        sys.exit(1)
    print(f"  Host: {creds['host']}")
    print()

    # Step 2: Connect
    print("Connecting to database...")
    try:
        conn = connect(creds, args.db_name)
    except Exception as e:
        print(f"ERROR: Connection failed: {e}", file=sys.stderr)
        sys.exit(1)
    print("  Connected.")
    print()

    # Step 3: Apply migration
    print("Applying migration...")
    t0 = time.time()
    try:
        apply_migration(conn, migration_path)
    except Exception as e:
        conn.rollback()
        conn.close()
        print(f"ERROR: Migration failed: {e}", file=sys.stderr)
        sys.exit(1)
    elapsed = time.time() - t0
    print(f"  Migration applied successfully in {elapsed:.2f}s")
    print()

    # Step 4: Verify tables exist
    print("Verifying new tables...")
    expected_tables = [
        "graph_version", "graph_eds_results",
        "question", "question_set", "question_set_membership", "generation_job",
        "assignment", "exam_session", "session_turn",
        "evaluation", "grade",
    ]
    with conn.cursor() as cur:
        cur.execute("""
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public'
            ORDER BY tablename
        """)
        existing = {row[0] for row in cur.fetchall()}

    missing = [t for t in expected_tables if t not in existing]
    if missing:
        print(f"  WARNING: Missing tables: {missing}", file=sys.stderr)
        conn.close()
        sys.exit(1)
    else:
        print(f"  All {len(expected_tables)} new tables confirmed present.")

    conn.close()
    print()
    print("SUCCESS: migration_002_modules applied.")


if __name__ == "__main__":
    main()
