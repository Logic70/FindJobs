"""Phase 1 tests: database init, config validation, and CLI.

All tests are deterministic and offline. Chinese text is expressed as ASCII
Unicode escapes to avoid encoding-dependent failures.
"""

import os
import tempfile
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------


def test_init_db_creates_tables():
    """init_db should create all expected tables in a fresh database."""
    from findjobs.db import init_db
    from sqlalchemy import inspect

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        session = init_db(db_path)

        inspector = inspect(session.bind)
        tables = set(inspector.get_table_names())

        assert "companies" in tables
        assert "sources" in tables
        assert "jobs" in tables
        assert "job_observations" in tables
        assert "collect_runs" in tables
        assert "user_marks" in tables

        session.close()


def test_jobs_table_has_all_planned_columns():
    """The jobs table should include all planned salary, tracking, and tag columns."""
    from findjobs.db import init_db
    from sqlalchemy import inspect

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        session = init_db(db_path)
        inspector = inspect(session.bind)
        cols = {c["name"] for c in inspector.get_columns("jobs")}

        # Salary fields
        assert "salary_text" in cols
        assert "salary_min" in cols
        assert "salary_max" in cols
        assert "salary_currency" in cols
        assert "salary_period" in cols
        assert "salary_disclosed" in cols

        # Tracking fields
        assert "first_seen_at" in cols
        assert "last_seen_at" in cols
        assert "status" in cols
        assert "matched_tags" in cols

        session.close()


# ---------------------------------------------------------------------------
# load_sources
# ---------------------------------------------------------------------------


def _write_temp_yaml(data: dict) -> Path:
    """Write a YAML dict to a temp file and return its path."""
    tmpdir = tempfile.mkdtemp()
    path = Path(tmpdir) / "sources.yaml"
    path.write_text(yaml.dump(data), encoding="utf-8")
    return path


# -- Company-level Huawei rejection --


def test_load_sources_rejects_huawei_slug():
    """load_sources should raise ValueError when a company slug contains 'huawei'."""
    from findjobs.config import load_sources

    path = _write_temp_yaml({
        "companies": [
            {
                "slug": "huawei",
                "name": "Huawei Technologies",
                "description": "",
                "homepage_url": "",
                "careers_url": "",
            }
        ],
        "sources": [],
    })

    with pytest.raises(ValueError, match="(?i)huawei"):
        load_sources(path)


def test_load_sources_rejects_huawei_company_name_cjk():
    """load_sources should reject a company name containing the CJK form of Huawei."""
    from findjobs.config import load_sources

    # 华为 = CJK for "Huawei"
    huawei_cjk = "华为"
    path = _write_temp_yaml({
        "companies": [
            {
                "slug": "hw-devices",
                "name": huawei_cjk + "技术有限公司",
                "description": "",
                "homepage_url": "",
                "careers_url": "",
            }
        ],
        "sources": [],
    })

    with pytest.raises(ValueError, match="(?i)" + huawei_cjk):
        load_sources(path)


def test_load_sources_accepts_valid_config():
    """load_sources should succeed for a standard company list."""
    from findjobs.config import load_sources

    path = _write_temp_yaml({
        "companies": [
            {
                "slug": "tencent",
                "name": "Tencent",
                "description": "",
                "homepage_url": "",
                "careers_url": "",
            }
        ],
        "sources": [
            {
                "slug": "tencent-careers",
                "name": "Tencent Careers",
                "company_slug": "tencent",
                "source_type": "official_careers",
                "base_url": "https://careers.tencent.com",
                "is_active": False,
            }
        ],
    })

    config = load_sources(path)
    assert len(config.companies) == 1
    assert config.companies[0].slug == "tencent"
    assert len(config.sources) == 1
    assert config.sources[0].slug == "tencent-careers"


# -- Source-level Huawei rejection --


def test_source_config_rejects_huawei_slug():
    """SourceConfig slug containing 'huawei' should raise ValueError."""
    from findjobs.config import SourceConfig

    with pytest.raises(ValueError, match="(?i)huawei"):
        SourceConfig(
            slug="huawei-careers",
            name="Some Careers",
            company_slug="some-company",
            source_type="official_careers",
            base_url="https://example.com",
        )


def test_source_config_rejects_huawei_name():
    """SourceConfig name containing 'huawei' should raise ValueError."""
    from findjobs.config import SourceConfig

    with pytest.raises(ValueError, match="(?i)huawei"):
        SourceConfig(
            slug="some-careers",
            name="Huawei Careers",
            company_slug="some-company",
            source_type="official_careers",
            base_url="https://example.com",
        )


def test_source_config_rejects_huawei_company_slug():
    """SourceConfig company_slug containing 'huawei' should raise ValueError."""
    from findjobs.config import SourceConfig

    with pytest.raises(ValueError, match="(?i)huawei"):
        SourceConfig(
            slug="some-careers",
            name="Some Careers",
            company_slug="huawei-devices",
            source_type="official_careers",
            base_url="https://example.com",
        )


def test_source_config_rejects_huawei_base_url():
    """SourceConfig base_url containing 'huawei' should raise ValueError."""
    from findjobs.config import SourceConfig

    with pytest.raises(ValueError, match="(?i)huawei"):
        SourceConfig(
            slug="some-careers",
            name="Some Careers",
            company_slug="some-company",
            source_type="official_careers",
            base_url="https://careers.huawei.com",
        )


def test_load_sources_rejects_huawei_source_slug():
    """load_sources should reject a source whose slug contains 'huawei'."""
    from findjobs.config import load_sources

    path = _write_temp_yaml({
        "companies": [{"slug": "acme", "name": "Acme Inc."}],
        "sources": [
            {
                "slug": "huawei-careers",
                "name": "Acme Careers",
                "company_slug": "acme",
                "source_type": "official_careers",
                "base_url": "https://example.com",
            }
        ],
    })

    with pytest.raises(ValueError, match="(?i)huawei"):
        load_sources(path)


def test_load_sources_rejects_huawei_source_name():
    """load_sources should reject a source whose name contains 'huawei'."""
    from findjobs.config import load_sources

    path = _write_temp_yaml({
        "companies": [{"slug": "acme", "name": "Acme Inc."}],
        "sources": [
            {
                "slug": "acme-careers",
                "name": "Huawei Careers",
                "company_slug": "acme",
                "source_type": "official_careers",
                "base_url": "https://example.com",
            }
        ],
    })

    with pytest.raises(ValueError, match="(?i)huawei"):
        load_sources(path)


def test_load_sources_rejects_huawei_source_company_slug():
    """load_sources should reject a source whose company_slug contains 'huawei'."""
    from findjobs.config import load_sources

    path = _write_temp_yaml({
        "companies": [{"slug": "acme", "name": "Acme Inc."}],
        "sources": [
            {
                "slug": "acme-careers",
                "name": "Acme Careers",
                "company_slug": "huawei-group",
                "source_type": "official_careers",
                "base_url": "https://example.com",
            }
        ],
    })

    with pytest.raises(ValueError, match="(?i)huawei"):
        load_sources(path)


def test_load_sources_rejects_huawei_source_base_url():
    """load_sources should reject a source whose base_url contains 'huawei'."""
    from findjobs.config import load_sources

    path = _write_temp_yaml({
        "companies": [{"slug": "acme", "name": "Acme Inc."}],
        "sources": [
            {
                "slug": "acme-careers",
                "name": "Acme Careers",
                "company_slug": "acme",
                "source_type": "official_careers",
                "base_url": "https://careers.huawei.com",
            }
        ],
    })

    with pytest.raises(ValueError, match="(?i)huawei"):
        load_sources(path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_init_exits_successfully():
    """Running 'findjobs init --db-path <tmp>' should exit with code 0."""
    from findjobs.cli import app

    runner = CliRunner()

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        result = runner.invoke(app, ["init", "--db-path", str(db_path)])
        assert result.exit_code == 0, f"CLI exited with {result.exit_code}: {result.output}"
        assert db_path.exists(), "Database file was not created"


def test_cli_init_uses_env_var():
    """The FINDJOBS_DB_PATH env var should be respected by init."""
    from findjobs.cli import app

    runner = CliRunner()

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "env-test.db"
        os.environ["FINDJOBS_DB_PATH"] = str(db_path)
        try:
            result = runner.invoke(app, ["init"])
            assert result.exit_code == 0, f"CLI exited with {result.exit_code}: {result.output}"
            assert db_path.exists(), "Database file was not created"
        finally:
            os.environ.pop("FINDJOBS_DB_PATH", None)


def test_cli_placeholder_commands():
    """The collect, export, and schedule install commands should exit 0."""
    from findjobs.cli import app

    runner = CliRunner()

    for cmd in [["collect"], ["export"], ["schedule", "install"]]:
        result = runner.invoke(app, cmd)
        assert result.exit_code == 0, f"CLI {' '.join(cmd)} exited with {result.exit_code}"
