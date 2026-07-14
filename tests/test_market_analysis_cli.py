from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from findjobs.cli import app
from findjobs.market_analysis import write_market_output


runner = CliRunner()
TAXONOMY_PATH = Path(__file__).parents[1] / "config" / "market_taxonomy.yaml"
KEYWORD_RULES_PATH = Path(__file__).parents[1] / "config" / "keyword_rules.yaml"


def full_row(**overrides: object) -> dict:
    row = {
        "id": 1,
        "company_slug": "example",
        "company_name": "Example",
        "title": "应用安全工程师",
        "location": "北京",
        "job_type": "安全",
        "status": "active",
        "salary_text": "",
        "salary_min": None,
        "salary_max": None,
        "salary_currency": "",
        "salary_period": "",
        "salary_disclosed": False,
        "matched_tags": ["Security"],
        "url": "https://example.test/1",
        "first_seen_at": "2026-07-10T00:00:00",
        "last_seen_at": "2026-07-12T00:00:00",
        "published_at": "2026-07-09T00:00:00",
        "relevance_status": "target",
        "classification_version": "2.1.1",
        "classification_reasons": ["security_surface_signals"],
        "description": "",
        "responsibilities": "负责应用安全平台建设",
        "requirements": "必须掌握 Python 和 AppSec",
        "detail_completeness": "full",
    }
    row.update(overrides)
    return row


def write_jobs(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_cli_generates_single_json_report(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs.jsonl"
    json_output = tmp_path / "reports" / "market.json"
    write_jobs(jobs, [full_row()])

    result = runner.invoke(
        app,
        [
            "market-analyze",
            "--jobs",
            str(jobs),
            "--taxonomy",
            str(TAXONOMY_PATH),
            "--keyword-rules",
            str(KEYWORD_RULES_PATH),
            "--output-json",
            str(json_output),
            "--as-of",
            "2026-07-14",
            "--no-profile-analysis",
        ],
    )

    assert result.exit_code == 0, result.output
    data = json.loads(json_output.read_text(encoding="utf-8"))
    assert data["schema_version"] == 3
    assert data["sample"]["analyzed_jobs"] == 1
    assert data["quality"]["requirements_available_jobs"] == 1
    assert data["keyword_analysis"]["rules_version"] == "2026.07.9"
    assert data["personal_advice"] is None


def test_missing_default_profile_omits_advice(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs.jsonl"
    write_jobs(jobs, [full_row()])
    result = runner.invoke(
        app,
        [
            "market-analyze",
            "--jobs",
            str(jobs),
            "--profile",
            str(tmp_path / "missing.md"),
            "--taxonomy",
            str(TAXONOMY_PATH),
            "--keyword-rules",
            str(KEYWORD_RULES_PATH),
            "--output-json",
            str(tmp_path / "out.json"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "personal advice omitted" in result.output
    data = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert data["personal_advice"] is None


def test_invalid_date_leaves_no_outputs(tmp_path: Path) -> None:
    json_output = tmp_path / "out.json"
    result = runner.invoke(
        app,
        [
            "market-analyze",
            "--jobs",
            str(tmp_path / "jobs.jsonl"),
            "--output-json",
            str(json_output),
            "--as-of",
            "2026/07/14",
        ],
    )

    assert result.exit_code == 1
    assert "YYYY-MM-DD" in result.output
    assert not json_output.exists()


def test_invalid_jsonl_leaves_no_outputs(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs.jsonl"
    jobs.write_text("{not-json}\n", encoding="utf-8")
    json_output = tmp_path / "out.json"
    result = runner.invoke(
        app,
        [
            "market-analyze",
            "--jobs",
            str(jobs),
            "--taxonomy",
            str(TAXONOMY_PATH),
            "--keyword-rules",
            str(KEYWORD_RULES_PATH),
            "--output-json",
            str(json_output),
        ],
    )

    assert result.exit_code == 1
    assert "Invalid JSONL" in result.output
    assert not json_output.exists()


def test_removed_markdown_option_is_rejected(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["market-analyze", "--output-markdown", str(tmp_path / "out.md")],
    )

    assert result.exit_code != 0
    assert "No such option" in result.output
    assert not (tmp_path / "out.md").exists()


def test_write_market_output_replaces_existing_file_atomically(tmp_path: Path) -> None:
    output = tmp_path / "market.json"
    output.write_text("old", encoding="utf-8")

    write_market_output(json_output=output, json_content="new")

    assert output.read_text(encoding="utf-8") == "new"
    assert not list(tmp_path.glob(".market_*.tmp"))


def test_replace_failure_preserves_existing_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    json_output = tmp_path / "market.json"
    json_output.write_text("old-json", encoding="utf-8")

    def fail_replace(path: Path, target: Path) -> Path:
        raise OSError("json replace failed")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="json replace failed"):
        write_market_output(
            json_output=json_output,
            json_content="new-json",
        )

    assert json_output.read_text(encoding="utf-8") == "old-json"
    assert not list(tmp_path.glob(".market_*.tmp"))


def test_market_analysis_module_has_no_database_or_ai_imports() -> None:
    import findjobs.market_analysis as module

    source = Path(module.__file__).read_text(encoding="utf-8").lower()
    assert "findjobs.db" not in source
    assert "sqlalchemy" not in source
    assert "anthropic" not in source
    assert "openai" not in source
