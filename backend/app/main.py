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

    conn.commit()
    print("all schemas applied")


def smoke() -> None:
    """End-to-end AWS test: seed → upload → enqueue → poll worker to ready."""
    from backend.app import smoke_test
    smoke_test.run(load_settings())


def main() -> None:
    """Dispatch on the first CLI argument."""
    command = sys.argv[1] if len(sys.argv) > 1 else "serve"
    {"serve": serve, "worker": worker, "migrate": migrate,
     "smoke": smoke}.get(command, serve)()


if __name__ == "__main__":
    main()
