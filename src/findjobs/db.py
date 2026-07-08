"""SQLAlchemy engine, session factory, and database initialization."""

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from findjobs.models import Base


def get_engine(db_path: str | Path) -> create_engine:
    """Create a SQLAlchemy engine for the given SQLite database path."""
    return create_engine(f"sqlite:///{db_path}", echo=False, poolclass=NullPool)


def init_db(db_path: str | Path | None = None) -> sessionmaker:
    """Initialize the database, creating all tables, and return a Session.

    Args:
        db_path: Path to the SQLite database file. If None, uses the default
                 path from paths.get_default_db_path().

    Returns:
        A SQLAlchemy Session instance bound to the new engine.
    """
    if db_path is None:
        from findjobs.paths import get_default_db_path

        db_path = get_default_db_path()

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    engine = get_engine(str(db_path))
    Base.metadata.create_all(engine)

    Session = sessionmaker(bind=engine)
    return Session()
