"""Additive schema upgrades for the running prototype database.

`Base.metadata.create_all` creates missing *tables* but never alters existing
ones, and the development database holds live demo data we do not want to
drop. Production would use Alembic migrations; this helper keeps the prototype
upgradeable in place by adding columns introduced after the first release.

Every statement must be safe to run repeatedly and on every boot.

Two dialects are handled because the prototype runs on both:
  * PostgreSQL — `ADD COLUMN IF NOT EXISTS`, one statement per column.
  * SQLite — no `IF NOT EXISTS` for columns, so the existing column list is
    read from PRAGMA first and only the missing ones are added.

Skipping SQLite entirely (as an earlier version did) meant a dev database
created before a column was introduced failed at the first query with
"no such column" — the model and the on-disk schema silently diverged.
"""
import logging

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from app.database.session import engine

logger = logging.getLogger("aeris.schema")

# (table, column, DDL) — DDL is the fragment after "ADD COLUMN".
# DDL must be valid on both dialects (SQLite is strict about defaults).
_ADDITIVE_COLUMNS: list[tuple[str, str, str]] = [
    ("devices", "enabled", "BOOLEAN NOT NULL DEFAULT 1"),
    ("devices", "provisioned_at", "TIMESTAMP WITH TIME ZONE"),
    ("devices", "mqtt_topic", "VARCHAR(200)"),
    ("devices", "protocol", "VARCHAR(16) NOT NULL DEFAULT 'mqtt'"),
    ("factories", "latitude", "FLOAT"),
    ("factories", "longitude", "FLOAT"),
    ("factories", "site_span_m", "FLOAT"),
]


def apply_prototype_upgrades() -> None:
    """Apply additive upgrades; log and continue if a statement fails."""
    dialect = engine.dialect.name
    try:
        if dialect == "sqlite":
            _apply_sqlite()
        else:
            _apply_with_if_not_exists()
    except SQLAlchemyError:
        logger.exception("Schema upgrade failed — continuing with existing schema")
    else:
        logger.debug("Prototype schema upgrades applied (%s)", dialect)


def _apply_with_if_not_exists() -> None:
    with engine.begin() as conn:
        for table, column, ddl in _ADDITIVE_COLUMNS:
            conn.execute(
                text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}")
            )


def _apply_sqlite() -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, column, ddl in _ADDITIVE_COLUMNS:
            if table not in existing_tables:
                continue
            present = {c["name"] for c in inspector.get_columns(table)}
            if column in present:
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            logger.info("Added column %s.%s", table, column)
