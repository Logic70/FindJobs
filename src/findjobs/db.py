"""SQLAlchemy engine, session factory, database initialization, and migration."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, inspect
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

# ---------------------------------------------------------------------------
# Revision constants  (keep in sync with migration file down_revision chains)
# ---------------------------------------------------------------------------
_BASELINE_REVISION = "0001"  # initial schema (all legacy tables)
_HEAD_REVISION = "0002"  # Phase-1 additions

_KNOWN_LEGACY_TABLES = frozenset(
    {"companies", "sources", "jobs", "job_observations", "collect_runs", "user_marks"}
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _get_project_root() -> Path:
    """Return the absolute project root (3 levels up from findjobs/paths.py)."""
    from findjobs.paths import get_project_root

    return get_project_root()


def _make_alembic_config(engine: Engine, root: Path | None = None) -> Config:
    """Build an Alembic Config pointed at the project's migrations/ directory.

    The *script_location* is set to an absolute path so that startup does not
    depend on the process current directory.  The caller's engine is injected
    so that ``env.py`` uses the exact database connection supplied by the
    application.
    """
    if root is None:
        root = _get_project_root()
    ini = root / "alembic.ini"
    if not ini.exists():
        raise RuntimeError(
            f"Alembic configuration not found at {ini}.  "
            "Ensure the project is installed from the repository root."
        )
    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.attributes["engine"] = engine
    return cfg


def _current_alembic_revision(engine: Engine) -> str | None:
    """Return the current Alembic revision stored in the database, or *None*."""
    conn = engine.connect()
    try:
        ctx = MigrationContext.configure(conn)
        return ctx.get_current_revision()
    finally:
        conn.close()


def _alembic_head_revision(root: Path | None = None) -> str | None:
    """Return the head revision string from the migration chain."""
    if root is None:
        root = _get_project_root()
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def _db_is_legacy(tables: set[str]) -> bool:
    """Return True when *tables* is exactly the known set of legacy tables."""
    return tables == _KNOWN_LEGACY_TABLES


# ---------------------------------------------------------------------------
# Backup  (SQLite backup API -- safe with WAL/journal)
# ---------------------------------------------------------------------------

def _backup_database(db_path: str | Path) -> Path | None:
    """Create a collision-resistant backup of *db_path* via the SQLite backup API.

    The backup file is placed next to the source with the naming convention::

        <stem>.backup-<UTC-microsecond-timestamp>.db

    If a backup with that exact name already exists, a numeric counter is
    appended after the timestamp.  Returns the backup path, or *None* if the
    source does not exist.
    """
    path = Path(db_path)
    if not path.exists():
        return None

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    stem = f"{path.stem}.backup-{ts}"
    backup_path = path.with_name(f"{stem}{path.suffix}")

    # Collision resistance – practically never triggers, but guarantees no
    # overwrite.
    counter = 0
    while backup_path.exists():
        counter += 1
        backup_path = path.with_name(f"{stem}_{counter}{path.suffix}")

    src = sqlite3.connect(str(path))
    dst = sqlite3.connect(str(backup_path))
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()

    return backup_path


# ---------------------------------------------------------------------------
# Schema upgrade
# ---------------------------------------------------------------------------

def upgrade_schema(engine: Engine) -> None:
    """Bring the database schema up to the Alembic ``head`` revision.

    Detection logic
    ---------------
    * ``alembic_version`` table present  -> managed DB, run ``upgrade head``.
    * Empty database (no tables)         -> fresh DB, run ``upgrade head``.
    * Tables exist without ``alembic_version``:
      - Exactly the known legacy set  -> stamp at baseline, then apply phase-1.
      - Anything else                 -> raise ``ValueError``.

    An on-disk database that genuinely needs migration is backed up once
    before any DDL or stamping takes place.
    """
    root = _get_project_root()
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    has_alembic = "alembic_version" in tables

    # -- no migration needed? -----------------------------------------------
    if has_alembic:
        curr = _current_alembic_revision(engine)
        head = _alembic_head_revision(root)
        if curr == head:
            return  # already current
    # -----------------------------------------------------------------------

    # -- discover database shape --------------------------------------------
    if has_alembic:
        needs_backup = True
        stamp_first = False
    elif not tables:
        needs_backup = False
        stamp_first = False
    elif _db_is_legacy(tables):
        needs_backup = True
        stamp_first = True
    else:
        raise ValueError(
            "Unrecognised non-empty database schema.  "
            f"Found tables: {sorted(tables)}.  "
            "Cannot safely apply migrations."
        )

    # -- backup before any change -------------------------------------------
    if needs_backup and engine.url.database:
        _backup_database(engine.url.database)

    # -- stamp legacy baseline when needed ----------------------------------
    if stamp_first:
        cfg = _make_alembic_config(engine, root)
        command.stamp(cfg, _BASELINE_REVISION)

    # -- run all pending migrations -----------------------------------------
    cfg = _make_alembic_config(engine, root)
    command.upgrade(cfg, _HEAD_REVISION)


# ---------------------------------------------------------------------------
# Engine and session factory
# ---------------------------------------------------------------------------

def get_engine(db_path: str | Path) -> Engine:
    """Create a SQLAlchemy engine for the given SQLite database path."""
    return create_engine(f"sqlite:///{db_path}", echo=False, poolclass=NullPool)


def init_db(db_path: str | Path | None = None) -> Session:
    """Initialise the database, running migrations, and return a Session.

    Args:
        db_path: Path to the SQLite database file.  If None, uses the default
                 path from ``paths.get_default_db_path()``.

    Returns:
        A SQLAlchemy ``Session`` instance bound to the new engine.
    """
    if db_path is None:
        from findjobs.paths import get_default_db_path

        db_path = get_default_db_path()

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    engine = get_engine(str(db_path))
    upgrade_schema(engine)

    Session = sessionmaker(bind=engine)
    return Session()
