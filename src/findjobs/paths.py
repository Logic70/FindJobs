"""Path resolution for the findjobs project."""

import os
from pathlib import Path


def _get_project_root() -> Path:
    """Resolve project root from this file's location.

    src/findjobs/paths.py -> src/findjobs -> src -> project root
    """
    return Path(__file__).resolve().parent.parent.parent


def get_project_root() -> Path:
    """Return the absolute project root directory."""
    return _get_project_root()


def get_config_dir() -> Path:
    """Return the config directory (project root / config)."""
    return get_project_root() / "config"


def get_data_dir() -> Path:
    """Return the data directory (project root / data), creating it if absent."""
    data_dir = get_project_root() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_default_db_path() -> Path:
    """Return the default SQLite database path.

    Respects the FINDJOBS_DB_PATH environment variable when set.
    Otherwise defaults to <data_dir>/findjobs.db.
    """
    env_path = os.environ.get("FINDJOBS_DB_PATH")
    if env_path:
        return Path(env_path)
    return get_data_dir() / "findjobs.db"
