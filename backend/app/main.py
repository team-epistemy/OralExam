"""Container entrypoint: `serve` runs the HTTP app, `worker` runs the consumer."""
from __future__ import annotations
import sys
import logging

from backend.config import load_settings

# Ensure all loggers emit INFO+ to stdout (CloudWatch captures stdout)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def serve() -> None:
    """Launch the FastAPI app under uvicorn on port 8080."""
    import uvicorn
    uvicorn.run("backend.app.http_app:app", host="0.0.0.0", port=8080)


def worker() -> None:
    """Build the SQS worker and consume ingest jobs until stopped."""
    from backend.app import factory
    w = factory.build_worker(load_settings())
    w.install_signals()
    w.run_forever()


def migrate() -> None:
    """Apply schema migrations. schema.sql only on first run (has DROP statements)."""
    import pathlib
    from backend.app import factory
    settings = load_settings()
    conn = factory.db_connection(settings)
    db_dir = pathlib.Path(__file__).resolve().parent.parent / "db"

    with conn.cursor() as cur:
        # Only apply schema.sql if the org table doesn't exist yet
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'org')"
        )
        tables_exist = cur.fetchone()[0]

        if not tables_exist:
            sql_file = db_dir / "schema.sql"
            print(f"applying {sql_file.name} (first run) ...")
            cur.execute(sql_file.read_text())
        else:
            print("schema.sql skipped (tables already exist)")

        # Always apply migration_002 (uses IF NOT EXISTS, safe to re-run)
        migration = db_dir / "migration_002_modules.sql"
        if migration.exists():
            print(f"applying {migration.name} ...")
            cur.execute(migration.read_text())

        # Always apply migration_003 (uses IF NOT EXISTS, safe to re-run)
        migration_003 = db_dir / "migration_003_eds_formula.sql"
        if migration_003.exists():
            print(f"applying {migration_003.name} ...")
            cur.execute(migration_003.read_text())

        # Always apply migration_004 (uses IF NOT EXISTS, safe to re-run)
        migration_004 = db_dir / "migration_004_graph_stale.sql"
        if migration_004.exists():
            print(f"applying {migration_004.name} ...")
            cur.execute(migration_004.read_text())

        # Always apply migration_005 (RLS hardening; idempotent)
        migration_005 = db_dir / "migration_005_rls_hardening.sql"
        if migration_005.exists():
            print(f"applying {migration_005.name} ...")
            cur.execute(migration_005.read_text())

        # Always apply migration_006 (auth schema + resolver; idempotent)
        migration_006 = db_dir / "migration_006_auth_schema.sql"
        if migration_006.exists():
            print(f"applying {migration_006.name} ...")
            cur.execute(migration_006.read_text())

        # Always apply migration_007 (per-professor course ownership; idempotent)
        migration_007 = db_dir / "migration_007_course_owner.sql"
        if migration_007.exists():
            print(f"applying {migration_007.name} ...")
            cur.execute(migration_007.read_text())

        # Always apply migration_008 (grant DELETE on enrollment; idempotent)
        migration_008 = db_dir / "migration_008_enrollment_drop.sql"
        if migration_008.exists():
            print(f"applying {migration_008.name} ...")
            cur.execute(migration_008.read_text())

    conn.commit()
    print("all schemas applied")


def clean() -> None:
    """Truncate all domain data (keep schema + org tenants) for a fresh test env."""
    from backend.app import factory
    conn = factory.db_connection(load_settings())
    with conn.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        tables = [r[0] for r in cur.fetchall() if r[0] != "org"]
        if tables:
            cur.execute("TRUNCATE " + ", ".join(tables) + " RESTART IDENTITY CASCADE")
    conn.commit()
    print(f"cleaned {len(tables)} tables (kept 'org'): {sorted(tables)}")


def bootstrap() -> None:
    """Idempotently seed the platform org + a platform_admin (Cognito + app_user).

    Reads EPISTEMY_ADMIN_EMAIL, EPISTEMY_ADMIN_PASSWORD, EPISTEMY_ORG_NAME.
    If EPISTEMY_ADMIN_SUB is set the Cognito call is skipped (user already
    created out of band). Runs as the admin owner so it writes app_user past RLS.
    """
    import os
    import uuid
    from backend.app import factory
    from backend.auth.provision import cognito_admin_create, insert_app_user
    settings = load_settings()
    email = os.environ["EPISTEMY_ADMIN_EMAIL"]
    password = os.environ.get("EPISTEMY_ADMIN_PASSWORD")
    org_name = os.environ.get("EPISTEMY_ORG_NAME", "epistemy")
    sub = os.environ.get("EPISTEMY_ADMIN_SUB")
    if sub:
        print(f"using pre-created cognito sub for {email}")
    else:
        print("creating cognito user ...")
        cognito = factory.build_cognito_config(settings)
        sub = cognito_admin_create(cognito["user_pool_id"], email,
                                   cognito["region"], password)
    print("connecting to db ...")
    conn = factory.db_connection(settings)
    print("upserting org + app_user ...")
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO org (org_id, org_name, title) VALUES (%s, %s, %s)
               ON CONFLICT (org_name) DO UPDATE SET title = EXCLUDED.title
               RETURNING org_id""",
            (str(uuid.uuid4()), org_name, org_name.title()))
        org_id = cur.fetchone()[0]
        insert_app_user(cur, sub, email, org_id, "platform_admin")
    conn.commit()
    print(f"bootstrap complete: admin={email} org={org_name}")


def smoke() -> None:
    """End-to-end AWS test: seed → upload → enqueue → poll worker to ready."""
    from backend.app import smoke_test
    smoke_test.run(load_settings())


def main() -> None:
    """Dispatch on the first CLI argument."""
    command = sys.argv[1] if len(sys.argv) > 1 else "serve"
    {"serve": serve, "worker": worker, "migrate": migrate,
     "clean": clean, "bootstrap": bootstrap, "smoke": smoke}.get(command, serve)()


if __name__ == "__main__":
    main()
