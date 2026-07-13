"""Phase 1A migration tests.

All tests are deterministic and offline.  Unicode text that must appear in
error messages is expressed as ASCII escapes to avoid encoding-dependent
failures.
"""

import os
import re
import sqlite3
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text as sa_text
from sqlalchemy.exc import IntegrityError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_legacy_schema(engine) -> None:
    """Create tables matching the pre-Phase-1A schema via raw DDL.

    This deliberately does NOT use Base.metadata.create_all so that the
    test database starts without the new columns that the updated model
    now declares.
    """
    raw = engine.raw_connection()
    try:
        cursor = raw.cursor()

        cursor.executescript("""
            CREATE TABLE companies (
                id INTEGER PRIMARY KEY,
                slug VARCHAR(100) NOT NULL UNIQUE,
                name VARCHAR(200) NOT NULL,
                description TEXT DEFAULT '',
                homepage_url VARCHAR(500) DEFAULT '',
                careers_url VARCHAR(500) DEFAULT '',
                is_active INTEGER DEFAULT 1,
                created_at DATETIME,
                updated_at DATETIME
            );
            CREATE INDEX ix_companies_slug ON companies (slug);

            CREATE TABLE sources (
                id INTEGER PRIMARY KEY,
                company_id INTEGER NOT NULL REFERENCES companies(id),
                slug VARCHAR(100) NOT NULL,
                name VARCHAR(200) NOT NULL,
                source_type VARCHAR(50) NOT NULL DEFAULT 'official_careers',
                base_url VARCHAR(500) DEFAULT '',
                is_active INTEGER DEFAULT 1,
                config_yaml TEXT DEFAULT '',
                created_at DATETIME,
                updated_at DATETIME
            );
            CREATE INDEX ix_sources_slug ON sources (slug);

            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY,
                source_id INTEGER NOT NULL REFERENCES sources(id),
                company_id INTEGER NOT NULL REFERENCES companies(id),
                external_id VARCHAR(200) DEFAULT '',
                title VARCHAR(300) NOT NULL,
                url VARCHAR(500) DEFAULT '',
                description TEXT DEFAULT '',
                salary_text TEXT DEFAULT '',
                salary_min FLOAT,
                salary_max FLOAT,
                salary_currency VARCHAR(10) DEFAULT 'CNY',
                salary_period VARCHAR(20) DEFAULT 'yearly',
                salary_disclosed INTEGER DEFAULT 0,
                location VARCHAR(200) DEFAULT '',
                job_type VARCHAR(50) DEFAULT '',
                published_at DATETIME,
                first_seen_at DATETIME,
                last_seen_at DATETIME,
                status VARCHAR(20) DEFAULT 'active',
                matched_tags TEXT DEFAULT '',
                created_at DATETIME,
                updated_at DATETIME
            );
            CREATE INDEX ix_jobs_status ON jobs (status);

            CREATE TABLE collect_runs (
                id INTEGER PRIMARY KEY,
                source_id INTEGER NOT NULL REFERENCES sources(id),
                status VARCHAR(20) NOT NULL DEFAULT 'running',
                started_at DATETIME,
                finished_at DATETIME,
                jobs_found INTEGER DEFAULT 0,
                jobs_new INTEGER DEFAULT 0,
                errors TEXT DEFAULT ''
            );

            CREATE TABLE job_observations (
                id INTEGER PRIMARY KEY,
                job_id INTEGER NOT NULL REFERENCES jobs(id),
                collect_run_id INTEGER REFERENCES collect_runs(id),
                seen_at DATETIME NOT NULL,
                raw_payload TEXT,
                field_name VARCHAR(100),
                old_value TEXT DEFAULT '',
                new_value TEXT DEFAULT ''
            );

            CREATE TABLE user_marks (
                id INTEGER PRIMARY KEY,
                job_id INTEGER NOT NULL REFERENCES jobs(id),
                mark_type VARCHAR(20) NOT NULL DEFAULT 'bookmark',
                note TEXT DEFAULT '',
                created_at DATETIME,
                updated_at DATETIME
            );
        """)
        raw.commit()
    finally:
        raw.close()


def _legacy_db_url(tmpdir: Path) -> str:
    """Return a sqlite:/// URL for a temp file inside *tmpdir*."""
    return f"sqlite:///{tmpdir / 'legacy.db'}"


def _legacy_engine(tmpdir: Path):
    """Create and return an engine pointing to a legacy-schema database."""
    engine = create_engine(_legacy_db_url(tmpdir), echo=False)
    _create_legacy_schema(engine)
    return engine


def _count_backups(db_path: Path) -> int:
    """Count sibling backup files (named <stem>.backup-*)."""
    pattern = f"{db_path.stem}.backup-*{db_path.suffix}"
    return len(list(db_path.parent.glob(pattern)))


def _alembic_version(engine) -> str | None:
    """Return the current Alembic revision from the database, or None."""
    inspector = inspect(engine)
    if "alembic_version" not in inspector.get_table_names():
        return None
    conn = engine.connect()
    try:
        row = conn.execute(
            sa_text("SELECT version_num FROM alembic_version")
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFreshDatabase:
    """A completely empty database should be upgraded to head."""

    def test_fresh_db_creates_all_tables(self):
        """init_db on a fresh path creates all expected tables."""
        from findjobs.db import init_db

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fresh.db"
            session = init_db(db_path)

            inspector = inspect(session.bind)
            tables = set(inspector.get_table_names())

            assert "alembic_version" in tables
            assert "companies" in tables
            assert "sources" in tables
            assert "jobs" in tables
            assert "job_observations" in tables
            assert "collect_runs" in tables
            assert "user_marks" in tables
            session.close()

    def test_fresh_db_head_revision(self):
        """The alembic_version table should contain the head revision."""
        from findjobs.db import init_db

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fresh2.db"
            session = init_db(db_path)
            rev = _alembic_version(session.bind)
            assert rev == "0003", f"Expected head 0003, got {rev!r}"
            session.close()

    def test_fresh_db_has_new_job_columns(self):
        """A fresh database should include all Phase-1A columns in the jobs table."""
        from findjobs.db import init_db

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fresh3.db"
            session = init_db(db_path)
            inspector = inspect(session.bind)
            cols = {c["name"] for c in inspector.get_columns("jobs")}

            assert "relevance_status" in cols
            assert "missing_run_count" in cols
            assert "classification_version" in cols
            assert "classification_reasons" in cols
            assert "responsibilities" in cols
            assert "requirements" in cols
            assert "detail_completeness" in cols
            session.close()

    def test_init_db_outside_repo_cwd(self):
        """Schema init succeeds even when the process cwd is outside the repo."""
        from findjobs.db import init_db

        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "outside_cwd.db"
            try:
                os.chdir(tmpdir)
                session = init_db(db_path)
                tables = set(inspect(session.bind).get_table_names())
                assert "alembic_version" in tables
                assert "jobs" in tables
                session.close()
            finally:
                os.chdir(original_cwd)


class TestLegacySchemaUpgrade:
    """An existing legacy database should be detected, stamped, and upgraded."""

    def _seed_legacy(self, engine):
        """Insert representative rows into all six legacy tables."""
        with engine.begin() as conn:
            conn.execute(
                sa_text(
                    "INSERT INTO companies (slug, name) VALUES ('acme', 'Acme Corp')"
                )
            )
            conn.execute(
                sa_text(
                    "INSERT INTO sources (company_id, slug, name, source_type) "
                    "VALUES (1, 'acme-careers', 'Acme Careers', 'official_careers')"
                )
            )
            conn.execute(
                sa_text(
                    "INSERT INTO jobs (source_id, company_id, title, status) "
                    "VALUES (1, 1, 'Software Engineer', 'active')"
                )
            )
            conn.execute(
                sa_text(
                    "INSERT INTO collect_runs (source_id, status, jobs_found, "
                    "jobs_new) VALUES (1, 'completed', 10, 2)"
                )
            )
            conn.execute(
                sa_text(
                    "INSERT INTO job_observations (job_id, collect_run_id, seen_at) "
                    "VALUES (1, 1, '2026-07-01 12:00:00')"
                )
            )
            conn.execute(
                sa_text(
                    "INSERT INTO user_marks (job_id, mark_type, note) "
                    "VALUES (1, 'bookmark', 'Interesting role')"
                )
            )

    def test_legacy_data_preserved_after_upgrade(self):
        """All rows survive the migration."""
        from findjobs.db import upgrade_schema

        with tempfile.TemporaryDirectory() as tmpdir:
            engine = _legacy_engine(Path(tmpdir))
            self._seed_legacy(engine)
            engine.dispose()

            # Run upgrade through a fresh engine (like init_db does)
            engine2 = create_engine(_legacy_db_url(Path(tmpdir)), echo=False)
            upgrade_schema(engine2)

            with engine2.connect() as conn:
                companies = conn.execute(
                    sa_text("SELECT slug FROM companies")
                ).fetchall()
                assert len(companies) == 1
                assert companies[0][0] == "acme"

                jobs = conn.execute(
                    sa_text("SELECT id, title, status FROM jobs")
                ).fetchall()
                assert len(jobs) == 1
                assert jobs[0][1] == "Software Engineer"

                marks = conn.execute(
                    sa_text("SELECT job_id, mark_type, note FROM user_marks")
                ).fetchall()
                assert len(marks) == 1
                assert marks[0][2] == "Interesting role"

                obs = conn.execute(
                    sa_text("SELECT id FROM job_observations")
                ).fetchall()
                assert len(obs) == 1

                runs = conn.execute(
                    sa_text("SELECT id FROM collect_runs")
                ).fetchall()
                assert len(runs) == 1

            engine2.dispose()

    def test_legacy_upgrade_adds_new_columns(self):
        """The new job columns should exist and have default values."""
        from findjobs.db import upgrade_schema

        with tempfile.TemporaryDirectory() as tmpdir:
            engine = _legacy_engine(Path(tmpdir))
            self._seed_legacy(engine)
            engine.dispose()

            engine2 = create_engine(_legacy_db_url(Path(tmpdir)), echo=False)
            upgrade_schema(engine2)

            inspector = inspect(engine2)
            cols = {c["name"] for c in inspector.get_columns("jobs")}
            assert "relevance_status" in cols
            assert "missing_run_count" in cols
            assert "classification_version" in cols
            assert "classification_reasons" in cols
            assert "responsibilities" in cols
            assert "requirements" in cols
            assert "detail_completeness" in cols

            # Check defaults applied to existing row
            with engine2.connect() as conn:
                row = conn.execute(
                    sa_text(
                        "SELECT relevance_status, missing_run_count, "
                        "classification_version, classification_reasons, "
                        "responsibilities, requirements, detail_completeness "
                        "FROM jobs WHERE id=1"
                    )
                ).fetchone()
                assert row[0] == "target"
                assert row[1] == 0
                assert row[2] == ""
                assert row[3] == "[]"
                assert row[4] == ""
                assert row[5] == ""
                assert row[6] == "missing"

            engine2.dispose()

    def test_legacy_upgrade_has_correct_revision(self):
        """After upgrade the alembic_version should be the head revision."""
        from findjobs.db import upgrade_schema

        with tempfile.TemporaryDirectory() as tmpdir:
            engine = _legacy_engine(Path(tmpdir))
            self._seed_legacy(engine)
            engine.dispose()

            engine2 = create_engine(_legacy_db_url(Path(tmpdir)), echo=False)
            upgrade_schema(engine2)
            rev = _alembic_version(engine2)
            assert rev == "0003"
            engine2.dispose()

    def test_legacy_upgrade_preserves_data_count(self):
        """Upgrade through 0003 preserves all existing rows and counts."""
        from findjobs.db import upgrade_schema

        with tempfile.TemporaryDirectory() as tmpdir:
            engine = _legacy_engine(Path(tmpdir))
            self._seed_legacy(engine)
            engine.dispose()

            engine2 = create_engine(_legacy_db_url(Path(tmpdir)), echo=False)
            upgrade_schema(engine2)

            with engine2.connect() as conn:
                counts = {
                    "companies": conn.execute(
                        sa_text("SELECT COUNT(*) FROM companies")
                    ).scalar(),
                    "sources": conn.execute(
                        sa_text("SELECT COUNT(*) FROM sources")
                    ).scalar(),
                    "jobs": conn.execute(
                        sa_text("SELECT COUNT(*) FROM jobs")
                    ).scalar(),
                    "collect_runs": conn.execute(
                        sa_text("SELECT COUNT(*) FROM collect_runs")
                    ).scalar(),
                    "job_observations": conn.execute(
                        sa_text("SELECT COUNT(*) FROM job_observations")
                    ).scalar(),
                    "user_marks": conn.execute(
                        sa_text("SELECT COUNT(*) FROM user_marks")
                    ).scalar(),
                }

            assert counts == {
                "companies": 1,
                "sources": 1,
                "jobs": 1,
                "collect_runs": 1,
                "job_observations": 1,
                "user_marks": 1,
            }, f"Row counts changed after migration: {counts}"

            engine2.dispose()

    def test_legacy_plus_extra_table_refused(self):
        """The six legacy tables plus an extra table should NOT be accepted."""
        from findjobs.db import upgrade_schema

        with tempfile.TemporaryDirectory() as tmpdir:
            engine = _legacy_engine(Path(tmpdir))

            # Add one extra table on top of the full legacy set
            raw = engine.raw_connection()
            try:
                raw.execute("CREATE TABLE extra_table (id INTEGER PRIMARY KEY)")
                raw.commit()
            finally:
                raw.close()

            engine.dispose()

            engine2 = create_engine(_legacy_db_url(Path(tmpdir)), echo=False)
            with pytest.raises(ValueError, match="(?i)unrecognis"):
                upgrade_schema(engine2)
            engine2.dispose()

            # Also verify no backup was created
            db_path = Path(tmpdir) / "legacy.db"
            assert _count_backups(db_path) == 0, (
                "No backup should be created for an unrecognised schema"
            )


class TestDuplicateMarkMerge:
    """Deterministic merge of duplicate user_marks rows."""

    def _make_engine_with_duplicates(self, tmpdir: str):
        """Create a legacy engine with base data for testing."""
        engine = _legacy_engine(Path(tmpdir))

        with engine.begin() as conn:
            conn.execute(
                sa_text(
                    "INSERT INTO companies (slug, name) VALUES "
                    "('acme', 'Acme Corp')"
                )
            )
            conn.execute(
                sa_text(
                    "INSERT INTO sources (company_id, slug, name) "
                    "VALUES (1, 'acme-careers', 'Acme Careers')"
                )
            )
            conn.execute(
                sa_text(
                    "INSERT INTO jobs (source_id, company_id, title) "
                    "VALUES (1, 1, 'Engineer')"
                )
            )
        return engine

    def test_merge_keeps_lowest_id(self):
        """When merging duplicates, the row with the lowest id is kept."""
        from findjobs.db import init_db

        with tempfile.TemporaryDirectory() as tmpdir:
            engine = self._make_engine_with_duplicates(tmpdir)
            with engine.begin() as conn:
                conn.execute(
                    sa_text(
                        "INSERT INTO user_marks (id, job_id, mark_type, note, "
                        "created_at, updated_at) "
                        "VALUES (10, 1, 'bookmark', 'First', "
                        "'2026-01-01', '2026-01-01')"
                    )
                )
                conn.execute(
                    sa_text(
                        "INSERT INTO user_marks (id, job_id, mark_type, note, "
                        "created_at, updated_at) "
                        "VALUES (20, 1, 'bookmark', 'Second', "
                        "'2026-02-01', '2026-02-01')"
                    )
                )
            engine.dispose()

            db_path = Path(tmpdir) / "legacy.db"
            session = init_db(db_path)
            rows = session.execute(
                sa_text(
                    "SELECT id, note FROM user_marks "
                    "WHERE job_id=1 AND mark_type='bookmark'"
                )
            ).fetchall()
            assert len(rows) == 1
            assert rows[0][0] == 10  # lowest id kept
            session.close()

    def test_merge_preserves_non_empty_note(self):
        """The non-empty note from the most recently updated duplicate is kept."""
        from findjobs.db import init_db

        with tempfile.TemporaryDirectory() as tmpdir:
            engine = self._make_engine_with_duplicates(tmpdir)
            with engine.begin() as conn:
                conn.execute(
                    sa_text(
                        "INSERT INTO user_marks (id, job_id, mark_type, note, "
                        "created_at, updated_at) "
                        "VALUES (1, 1, 'bookmark', '', "
                        "'2026-01-01', '2026-01-01')"
                    )
                )
                conn.execute(
                    sa_text(
                        "INSERT INTO user_marks (id, job_id, mark_type, note, "
                        "created_at, updated_at) "
                        "VALUES (2, 1, 'bookmark', 'Has note', "
                        "'2026-01-02', '2026-01-05')"
                    )
                )
                conn.execute(
                    sa_text(
                        "INSERT INTO user_marks (id, job_id, mark_type, note, "
                        "created_at, updated_at) "
                        "VALUES (3, 1, 'bookmark', 'Updated note', "
                        "'2026-01-03', '2026-01-10')"
                    )
                )
            engine.dispose()

            db_path = Path(tmpdir) / "legacy.db"
            session = init_db(db_path)
            rows = session.execute(
                sa_text(
                    "SELECT note FROM user_marks "
                    "WHERE job_id=1 AND mark_type='bookmark'"
                )
            ).fetchall()
            assert len(rows) == 1
            # Most recently updated with non-empty note
            assert rows[0][0] == "Updated note"
            session.close()

    def test_merge_keeps_earliest_created_latest_updated(self):
        """created_at should be the earliest, updated_at the latest."""
        from findjobs.db import init_db

        with tempfile.TemporaryDirectory() as tmpdir:
            engine = self._make_engine_with_duplicates(tmpdir)
            with engine.begin() as conn:
                conn.execute(
                    sa_text(
                        "INSERT INTO user_marks (id, job_id, mark_type, note, "
                        "created_at, updated_at) "
                        "VALUES (1, 1, 'bookmark', 'note', "
                        "'2026-01-01', '2026-06-01')"
                    )
                )
                conn.execute(
                    sa_text(
                        "INSERT INTO user_marks (id, job_id, mark_type, note, "
                        "created_at, updated_at) "
                        "VALUES (2, 1, 'bookmark', 'note2', "
                        "'2026-03-01', '2026-01-01')"
                    )
                )
            engine.dispose()

            db_path = Path(tmpdir) / "legacy.db"
            session = init_db(db_path)
            row = session.execute(
                sa_text(
                    "SELECT created_at, updated_at FROM user_marks "
                    "WHERE job_id=1 AND mark_type='bookmark'"
                )
            ).fetchone()
            # created_at should be 2026-01-01 (earliest),
            # updated_at should be 2026-06-01 (latest)
            assert str(row[0]).startswith("2026-01-01"), f"Got: {row[0]}"
            assert str(row[1]).startswith("2026-06-01"), f"Got: {row[1]}"
            session.close()

    def test_merge_null_timestamps(self):
        """Null created_at or updated_at should not cause merge failures."""
        from findjobs.db import init_db

        with tempfile.TemporaryDirectory() as tmpdir:
            engine = self._make_engine_with_duplicates(tmpdir)
            with engine.begin() as conn:
                # Insert rows with explicit NULL timestamps
                conn.execute(
                    sa_text(
                        "INSERT INTO user_marks (id, job_id, mark_type, note, "
                        "created_at, updated_at) "
                        "VALUES (1, 1, 'bookmark', 'Alpha', "
                        "NULL, NULL)"
                    )
                )
                conn.execute(
                    sa_text(
                        "INSERT INTO user_marks (id, job_id, mark_type, note, "
                        "created_at, updated_at) "
                        "VALUES (2, 1, 'bookmark', 'Beta', "
                        "NULL, NULL)"
                    )
                )
            engine.dispose()

            db_path = Path(tmpdir) / "legacy.db"
            # Must not raise.
            session = init_db(db_path)

            # Exactly one row should survive, the lowest id.
            rows = session.execute(
                sa_text(
                    "SELECT id, note, created_at, updated_at FROM user_marks "
                    "WHERE job_id=1 AND mark_type='bookmark'"
                )
            ).fetchall()
            assert len(rows) == 1
            # The "most recently updated" tiebreaker falls back to id when
            # both updated_ats are null -- the higher id wins.  Since id==2
            # has the highest id in this group, "Beta" should be the note.
            # (Both have null updated_at, so id is the tiebreaker.)
            assert rows[0][0] == 1  # lowest id kept
            assert rows[0][1] == "Beta"  # higher-id note wins on null ts tie
            assert rows[0][2] is None  # no non-null created_at survived
            assert rows[0][3] is None  # no non-null updated_at survived
            session.close()

    def test_unique_constraint_enforced(self):
        """After migration, inserting a duplicate (job_id, mark_type) fails."""
        from findjobs.db import init_db

        with tempfile.TemporaryDirectory() as tmpdir:
            engine = self._make_engine_with_duplicates(tmpdir)
            engine.dispose()

            db_path = Path(tmpdir) / "legacy.db"
            session = init_db(db_path)

            # First insert should succeed
            session.execute(
                sa_text(
                    "INSERT INTO user_marks (job_id, mark_type) "
                    "VALUES (1, 'bookmark')"
                )
            )
            session.commit()

            # Second insert with same (job_id, mark_type) must fail
            with pytest.raises(IntegrityError):
                session.execute(
                    sa_text(
                        "INSERT INTO user_marks (job_id, mark_type) "
                        "VALUES (1, 'bookmark')"
                    )
                )
                session.commit()
            session.close()


class TestBackup:
    """Backup behaviour around schema upgrades."""

    def test_backup_created_when_migration_required(self):
        """An on-disk legacy database should produce at least one backup file."""
        from findjobs.db import upgrade_schema

        with tempfile.TemporaryDirectory() as tmpdir:
            engine = _legacy_engine(Path(tmpdir))
            db_path = Path(tmpdir) / "legacy.db"
            engine.dispose()

            engine2 = create_engine(_legacy_db_url(Path(tmpdir)), echo=False)
            upgrade_schema(engine2)
            engine2.dispose()

            assert _count_backups(db_path) >= 1

    def test_no_backup_when_schema_already_current(self):
        """A database already at head should NOT produce a backup."""
        from findjobs.db import init_db

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "current.db"
            session = init_db(db_path)
            session.close()

            before = _count_backups(db_path)
            session2 = init_db(db_path)
            session2.close()
            after = _count_backups(db_path)

            assert after == before, (
                f"Expected no new backup when schema is current "
                f"(before={before}, after={after})"
            )

    def test_backup_contains_original_data(self):
        """A backup should be a functional copy containing the legacy data."""
        from findjobs.db import upgrade_schema

        with tempfile.TemporaryDirectory() as tmpdir:
            engine = _legacy_engine(Path(tmpdir))
            with engine.begin() as conn:
                conn.execute(
                    sa_text(
                        "INSERT INTO companies (slug, name) VALUES "
                        "('backup-test', 'Backup Inc')"
                    )
                )
            engine.dispose()

            engine2 = create_engine(_legacy_db_url(Path(tmpdir)), echo=False)
            upgrade_schema(engine2)
            engine2.dispose()

            # Find the backup file
            db_path = Path(tmpdir) / "legacy.db"
            backups = sorted(
                db_path.parent.glob(f"{db_path.stem}.backup-*{db_path.suffix}")
            )
            assert len(backups) >= 1

            # Open backup and verify data
            bak = sqlite3.connect(str(backups[0]))
            try:
                row = bak.execute(
                    "SELECT slug, name FROM companies"
                ).fetchone()
                assert row is not None
                assert row[0] == "backup-test"
                assert row[1] == "Backup Inc"
            finally:
                bak.close()

    def test_backup_name_format(self):
        """Backup file name uses the .backup- prefix and microsecond timestamp."""
        from findjobs.db import upgrade_schema

        with tempfile.TemporaryDirectory() as tmpdir:
            engine = _legacy_engine(Path(tmpdir))
            db_path = Path(tmpdir) / "legacy.db"
            engine.dispose()

            engine2 = create_engine(_legacy_db_url(Path(tmpdir)), echo=False)
            upgrade_schema(engine2)
            engine2.dispose()

            backups = sorted(
                db_path.parent.glob(f"{db_path.stem}.backup-*{db_path.suffix}")
            )
            assert len(backups) >= 1
            name = backups[0].name
            # Name should be like legacy.backup-20260710T120000123456.db
            assert name.startswith("legacy.backup-"), f"Unexpected name: {name}"
            # Verify timestamp part matches YYYYMMDDTHHMMSSffffff[_N].
            middle = name.removeprefix("legacy.backup-").removesuffix(".db")
            assert re.fullmatch(r"\d{8}T\d{12}(?:_\d+)?", middle), (
                f"Timestamp part has unexpected format: {middle}"
            )


class TestUnknownSchema:
    """An unrecogised non-empty database should be refused."""

    def test_unknown_schema_raises(self):
        """A database with unrecognised tables should raise ValueError."""
        from findjobs.db import upgrade_schema

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "unknown.db"
            conn = sqlite3.connect(str(db_path))
            try:
                conn.execute(
                    "CREATE TABLE my_random_data "
                    "(id INTEGER PRIMARY KEY, val TEXT)"
                )
                conn.commit()
            finally:
                conn.close()

            engine = create_engine(f"sqlite:///{db_path}", echo=False)
            with pytest.raises(ValueError, match="(?i)unrecognis"):
                upgrade_schema(engine)
            engine.dispose()

    def test_partial_legacy_schema_raises(self):
        """A database with only a subset of legacy tables should be refused."""
        from findjobs.db import upgrade_schema

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "partial.db"
            conn = sqlite3.connect(str(db_path))
            try:
                conn.execute(
                    "CREATE TABLE companies (id INTEGER PRIMARY KEY, "
                    "slug VARCHAR(100) NOT NULL)"
                )
                conn.execute(
                    "CREATE TABLE jobs (id INTEGER PRIMARY KEY, "
                    "title VARCHAR(300) NOT NULL)"
                )
                conn.commit()
            finally:
                conn.close()

            engine = create_engine(f"sqlite:///{db_path}", echo=False)
            with pytest.raises(ValueError, match="(?i)unrecognis"):
                upgrade_schema(engine)
            engine.dispose()
