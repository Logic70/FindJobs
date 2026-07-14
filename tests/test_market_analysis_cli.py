from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from findjobs.cli import app
from findjobs.market_analysis import write_market_outputs


runner = CliRunner()
TAXONOMY_PATH = Path(__file__).parents[1] / "config" / "market_taxonomy.yaml"


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


def test_cli_generates_consistent_json_and_markdown(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs.jsonl"
    json_output = tmp_path / "reports" / "market.json"
    markdown_output = tmp_path / "reports" / "market.md"
    write_jobs(jobs, [full_row()])

    result = runner.invoke(
        app,
        [
            "market-analyze",
            "--jobs",
            str(jobs),
            "--taxonomy",
            str(TAXONOMY_PATH),
            "--output-json",
            str(json_output),
            "--output-markdown",
            str(markdown_output),
            "--as-of",
            "2026-07-14",
            "--no-profile-analysis",
        ],
    )

    assert result.exit_code == 0, result.output
    data = json.loads(json_output.read_text(encoding="utf-8"))
    markdown = markdown_output.read_text(encoding="utf-8")
    assert data["sample"]["analyzed_jobs"] == 1
    assert data["quality"]["requirements_available_jobs"] == 1
    assert str(data["quality"]["requirements_available_jobs"]) in markdown
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
            "--output-json",
            str(tmp_path / "out.json"),
            "--output-markdown",
            str(tmp_path / "out.md"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "personal advice omitted" in result.output
    data = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert data["personal_advice"] is None


def test_invalid_date_leaves_no_outputs(tmp_path: Path) -> None:
    json_output = tmp_path / "out.json"
    markdown_output = tmp_path / "out.md"
    result = runner.invoke(
        app,
        [
            "market-analyze",
            "--jobs",
            str(tmp_path / "jobs.jsonl"),
            "--output-json",
            str(json_output),
            "--output-markdown",
            str(markdown_output),
            "--as-of",
            "2026/07/14",
        ],
    )

    assert result.exit_code == 1
    assert "YYYY-MM-DD" in result.output
    assert not json_output.exists()
    assert not markdown_output.exists()


def test_invalid_jsonl_leaves_no_outputs(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs.jsonl"
    jobs.write_text("{not-json}\n", encoding="utf-8")
    json_output = tmp_path / "out.json"
    markdown_output = tmp_path / "out.md"
    result = runner.invoke(
        app,
        [
            "market-analyze",
            "--jobs",
            str(jobs),
            "--taxonomy",
            str(TAXONOMY_PATH),
            "--output-json",
            str(json_output),
            "--output-markdown",
            str(markdown_output),
        ],
    )

    assert result.exit_code == 1
    assert "Invalid JSONL" in result.output
    assert not json_output.exists()
    assert not markdown_output.exists()


def test_identical_outputs_are_rejected_without_overwrite(tmp_path: Path) -> None:
    output = tmp_path / "same.out"
    output.write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="different paths"):
        write_market_outputs(
            json_output=output,
            markdown_output=output,
            json_content="new-json",
            markdown_content="new-markdown",
        )

    assert output.read_text(encoding="utf-8") == "keep"


def test_second_replace_failure_restores_both_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    json_output = tmp_path / "market.json"
    markdown_output = tmp_path / "market.md"
    json_output.write_text("old-json", encoding="utf-8")
    markdown_output.write_text("old-markdown", encoding="utf-8")
    original_replace = Path.replace

    def fail_markdown_stage(path: Path, target: Path) -> Path:
        if path.name.startswith(".market_markdown_"):
            raise OSError("markdown replace failed")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_markdown_stage)
    with pytest.raises(OSError, match="markdown replace failed"):
        write_market_outputs(
            json_output=json_output,
            markdown_output=markdown_output,
            json_content="new-json",
            markdown_content="new-markdown",
        )

    assert json_output.read_text(encoding="utf-8") == "old-json"
    assert markdown_output.read_text(encoding="utf-8") == "old-markdown"
    assert not list(tmp_path.glob(".market_*.tmp"))


def test_market_analysis_module_has_no_database_or_ai_imports() -> None:
    import findjobs.market_analysis as module

    source = Path(module.__file__).read_text(encoding="utf-8").lower()
    assert "findjobs.db" not in source
    assert "sqlalchemy" not in source
    assert "anthropic" not in source
    assert "openai" not in source
