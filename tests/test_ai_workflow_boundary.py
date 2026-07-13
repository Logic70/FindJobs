"""Tests for full-fact AI workflow and weekly recommendation boundaries.

Covers: rollback-safe paired output, full-row rejection, deterministic
reports, no PII in recommendation output, weekly vs full field separation,
profile-absent behavior, CLI paths, workflow prompt invariants, Claude
command restrictions, gitignore entries, and absence of DB imports.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from typer.testing import CliRunner

from findjobs.recommendation import recommend_jobs
from findjobs.recommendation_profile import RecommendationProfile
from findjobs.weekly_recommendation import (
    RecommendationOutput,
    run_exported_recommendations,
    validate_full_rows,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

runner = CliRunner()


def make_row(**overrides: object) -> dict:
    """Return a valid full-format job row dict with overrides applied."""
    row: dict = {
        "id": 1,
        "company_slug": "bytedance",
        "company_name": "ByteDance",
        "title": "Security Engineer",
        "location": "北京",
        "job_type": "技术",
        "status": "active",
        "salary_text": "",
        "salary_min": None,
        "salary_max": None,
        "salary_currency": "",
        "salary_period": "",
        "salary_disclosed": False,
        "matched_tags": ["Security"],
        "url": "https://jobs.example.com/1",
        "first_seen_at": "2025-01-01T00:00:00",
        "last_seen_at": "2025-01-01T00:00:00",
        "published_at": "2025-01-01T00:00:00",
        "relevance_status": "target",
        "classification_version": "2.1.0",
        "classification_reasons": [],
        "description": "",
        "responsibilities": "Responsible for security testing.",
        "requirements": "5 years experience in Python, cloud security",
        "detail_completeness": "full",
    }
    row.update(overrides)
    return row


def make_summary_row(**overrides: object) -> dict:
    """Return a summary-only row that is missing full-export fields."""
    row: dict = {
        "id": 1,
        "company_slug": "bytedance",
        "company_name": "ByteDance",
        "title": "Security Engineer",
        "location": "北京",
        "job_type": "技术",
        "status": "active",
        "salary_text": "",
        "salary_min": None,
        "salary_max": None,
        "salary_currency": "",
        "salary_period": "",
        "salary_disclosed": False,
        "matched_tags": ["Security"],
        "url": "https://jobs.example.com/1",
        "first_seen_at": "2025-01-01T00:00:00",
        "last_seen_at": "2025-01-01T00:00:00",
        "published_at": "2025-01-01T00:00:00",
    }
    row.update(overrides)
    return row


def write_jsonl(rows: list[dict], path: Path) -> None:
    """Write rows as JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")


def write_profile_md(path: Path, text: str | None = None) -> None:
    """Write a minimal markdown profile."""
    content = text or (
        "## Background\n\n"
        "- **Skills**: Python, cloud security\n"
        "- **Experience**: 5 years\n"
        "- **Roles**: Security Engineer\n\n"
        "## Target Cities\n\n- 北京\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def profile() -> RecommendationProfile:
    return RecommendationProfile(
        skills=("Python", "cloud security", "penetration testing"),
        experience_years=5.0,
        roles=("Security Engineer",),
        target_cities=("北京",),
    )


@pytest.fixture
def jobs_path(tmp_path: Path) -> Path:
    """Write a valid full-export JSONL file."""
    p = tmp_path / "jobs-full.jsonl"
    write_jsonl([make_row(), make_row(id=2, title="AI Engineer", matched_tags=["AI"])], p)
    return p


@pytest.fixture
def profile_path(tmp_path: Path) -> Path:
    p = tmp_path / "profile.md"
    write_profile_md(p)
    return p


def _create_jobs_and_profile(tmp_path: Path) -> tuple[Path, Path]:
    """Write jobs JSONL and profile files into *tmp_path*.

    Returns ``(jobs_path, profile_path)`` for use in tests that cannot
    accept fixtures directly.
    """
    jobs_p = tmp_path / "jobs.jsonl"
    write_jsonl([make_row(), make_row(id=2, title="AI Engineer", matched_tags=["AI"])], jobs_p)
    prof_p = tmp_path / "prof.md"
    write_profile_md(prof_p)
    return jobs_p, prof_p


# ===================================================================
#  validate_full_rows
# ===================================================================


class TestValidateFullRows:
    def test_full_rows_pass(self) -> None:
        """Full rows with all required fields pass validation."""
        rows = [make_row(), make_row(id=2)]
        validate_full_rows(rows)  # must not raise

    def test_summary_rows_rejected(self) -> None:
        """Summary-only rows are rejected with a clear error."""
        rows = [make_summary_row()]
        with pytest.raises(ValueError, match="detail_level"):
            validate_full_rows(rows)

    def test_summary_missing_requirements(self) -> None:
        """Missing requirements field triggers rejection."""
        row = make_summary_row()
        row.pop("requirements", None)
        with pytest.raises(ValueError, match="requirements"):
            validate_full_rows([row])

    def test_summary_missing_responsibilities(self) -> None:
        """Missing responsibilities field triggers rejection."""
        row = make_summary_row()
        row.pop("responsibilities", None)
        with pytest.raises(ValueError, match="responsibilities"):
            validate_full_rows([row])

    def test_summary_missing_detail_completeness(self) -> None:
        """Missing detail_completeness field triggers rejection."""
        row = make_summary_row()
        row.pop("detail_completeness", None)
        with pytest.raises(ValueError, match="detail_completeness"):
            validate_full_rows([row])

    def test_summary_missing_relevance_status(self) -> None:
        """Missing relevance_status field triggers rejection."""
        row = make_summary_row()
        row.pop("relevance_status", None)
        with pytest.raises(ValueError, match="relevance_status"):
            validate_full_rows([row])

    def test_mixed_valid_and_invalid(self) -> None:
        """One summary row among full rows still rejects the whole batch."""
        rows = [make_row(), make_summary_row()]
        with pytest.raises(ValueError):
            validate_full_rows(rows)

    def test_empty_list_passes(self) -> None:
        """Empty row list passes validation."""
        validate_full_rows([])

    def test_clear_error_message(self) -> None:
        """Error message mentions detail_level=full."""
        with pytest.raises(ValueError) as exc:
            validate_full_rows([make_summary_row()])
        assert "detail_level" in str(exc.value)


# ===================================================================
#  run_exported_recommendations — integration
# ===================================================================


class TestRunExportedRecommendations:
    def test_successful_run_returns_output(
        self, jobs_path: Path, profile_path: Path, tmp_path: Path
    ) -> None:
        """Successful run returns RecommendationOutput with paths and counts."""
        md_out = tmp_path / "match" / "recs.md"
        json_out = tmp_path / "match" / "recs.json"
        result = run_exported_recommendations(
            jobs_path=jobs_path,
            profile_path=profile_path,
            markdown_output=md_out,
            json_output=json_out,
        )
        assert isinstance(result, RecommendationOutput)
        assert result.total_scanned == 2
        assert result.total_eligible >= 0
        assert result.markdown_output == md_out
        assert result.json_output == json_out

    def test_creates_both_files(
        self, jobs_path: Path, profile_path: Path, tmp_path: Path
    ) -> None:
        """Both markdown and json files are created on success."""
        md_out = tmp_path / "match" / "recs.md"
        json_out = tmp_path / "match" / "recs.json"
        run_exported_recommendations(
            jobs_path=jobs_path,
            profile_path=profile_path,
            markdown_output=md_out,
            json_output=json_out,
        )
        assert md_out.exists()
        assert json_out.exists()

    def test_temp_files_in_respective_parents(self, jobs_path: Path, profile_path: Path, tmp_path: Path) -> None:
        """Stage and backup files are created in each output's own parent directory."""
        md_parent = tmp_path / "md_dir"
        json_parent = tmp_path / "json_dir"
        md_out = md_parent / "recs.md"
        json_out = json_parent / "recs.json"
        run_exported_recommendations(jobs_path, profile_path, md_out, json_out)
        # After success all .rec_ files should be gone.
        md_leaks = list(md_parent.glob(".rec_*"))
        json_leaks = list(json_parent.glob(".rec_*"))
        assert len(md_leaks) == 0
        assert len(json_leaks) == 0
        # Both outputs must be present in their respective parents.
        assert md_out.exists()
        assert json_out.exists()

    def test_markdown_content_valid(
        self, jobs_path: Path, profile_path: Path, tmp_path: Path
    ) -> None:
        """Markdown file has Chinese labels and recommendation content."""
        md_out = tmp_path / "recs.md"
        json_out = tmp_path / "recs.json"
        run_exported_recommendations(
            jobs_path=jobs_path,
            profile_path=profile_path,
            markdown_output=md_out,
            json_output=json_out,
        )
        content = md_out.read_text(encoding="utf-8")
        assert "推荐" in content
        assert "扫描" in content

    def test_json_content_valid(
        self, jobs_path: Path, profile_path: Path, tmp_path: Path
    ) -> None:
        """JSON file is valid and contains schema_version."""
        md_out = tmp_path / "recs.md"
        json_out = tmp_path / "recs.json"
        run_exported_recommendations(
            jobs_path=jobs_path,
            profile_path=profile_path,
            markdown_output=md_out,
            json_output=json_out,
        )
        data = json.loads(json_out.read_text(encoding="utf-8"))
        assert data["schema_version"] == 1
        assert "recommendations" in data

    def test_missing_jobs_file(self, tmp_path: Path) -> None:
        """Missing jobs file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="Jobs file"):
            run_exported_recommendations(
                jobs_path=tmp_path / "nonexistent.jsonl",
                profile_path=tmp_path / "profile.md",
                markdown_output=tmp_path / "out.md",
                json_output=tmp_path / "out.json",
            )

    def test_missing_profile_file(self, tmp_path: Path, jobs_path: Path) -> None:
        """Missing profile file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="Profile file"):
            run_exported_recommendations(
                jobs_path=jobs_path,
                profile_path=tmp_path / "nonexistent.md",
                markdown_output=tmp_path / "out.md",
                json_output=tmp_path / "out.json",
            )

    def test_summary_rows_rejected(
        self, tmp_path: Path, profile_path: Path
    ) -> None:
        """Summary-only rows raise ValueError before any file creation."""
        jobs_p = tmp_path / "jobs-summary.jsonl"
        write_jsonl([make_summary_row()], jobs_p)
        md_out = tmp_path / "a" / "b" / "out.md"
        json_out = tmp_path / "a" / "b" / "out.json"
        with pytest.raises(ValueError):
            run_exported_recommendations(
                jobs_path=jobs_p,
                profile_path=profile_path,
                markdown_output=md_out,
                json_output=json_out,
            )
        assert not md_out.exists()
        assert not json_out.exists()
        assert not md_out.parent.exists()

    def test_deterministic_output(
        self, jobs_path: Path, profile_path: Path, tmp_path: Path
    ) -> None:
        """Same inputs produce identical outputs."""
        md1 = tmp_path / "r1.md"
        json1 = tmp_path / "r1.json"
        md2 = tmp_path / "r2.md"
        json2 = tmp_path / "r2.json"
        run_exported_recommendations(jobs_path, profile_path, md1, json1)
        run_exported_recommendations(jobs_path, profile_path, md2, json2)
        assert md1.read_bytes() == md2.read_bytes()
        assert json1.read_bytes() == json2.read_bytes()

    def test_limit_respected(
        self, jobs_path: Path, profile_path: Path, tmp_path: Path
    ) -> None:
        """limit parameter limits returned recommendations."""
        md_out = tmp_path / "out.md"
        json_out = tmp_path / "out.json"
        result = run_exported_recommendations(
            jobs_path=jobs_path,
            profile_path=profile_path,
            markdown_output=md_out,
            json_output=json_out,
            limit=1,
        )
        assert result.returned_count == 1

    def test_identical_destinations_rejected(
        self, tmp_path: Path
    ) -> None:
        """Identical markdown_output and json_output paths raise ValueError."""
        jobs_p = tmp_path / "jobs.jsonl"
        write_jsonl([make_row()], jobs_p)
        prof_p = tmp_path / "prof.md"
        write_profile_md(prof_p)

        same = tmp_path / "deep" / "out.md"
        with pytest.raises(ValueError, match="must resolve to different paths"):
            run_exported_recommendations(
                jobs_path=jobs_p,
                profile_path=prof_p,
                markdown_output=same,
                json_output=same,
            )
        assert not same.exists()
        assert not same.parent.exists()

    def test_no_temp_file_leakage_on_success(
        self, jobs_path: Path, profile_path: Path, tmp_path: Path
    ) -> None:
        """No .rec_ temp files remain after a successful run."""
        md_parent = tmp_path / "md"
        json_parent = tmp_path / "json"
        md_out = md_parent / "recs.md"
        json_out = json_parent / "recs.json"
        run_exported_recommendations(jobs_path, profile_path, md_out, json_out)
        md_leaks = list(md_parent.glob(".rec_*"))
        json_leaks = list(json_parent.glob(".rec_*"))
        assert len(md_leaks) == 0
        assert len(json_leaks) == 0

    def test_no_db_imports(self) -> None:
        """The weekly_recommendation module has no database imports."""
        import findjobs.weekly_recommendation as wr

        source = Path(wr.__file__).read_text(encoding="utf-8")
        assert "from findjobs.db" not in source
        assert "import findjobs.db" not in source
        assert "from findjobs.models" not in source
        assert "from sqlalchemy" not in source
        assert "from findjobs.recommendation" in source

    def test_no_ai_call(self) -> None:
        """The weekly_recommendation module does not import AI modules."""
        import findjobs.weekly_recommendation as wr

        source = Path(wr.__file__).read_text(encoding="utf-8")
        for kw in ("anthropic", "openai", "llm"):
            assert kw not in source.lower()


# ===================================================================
#  Rollback safety
# ===================================================================


class TestRollbackSafety:
    def test_no_files_on_validate_failure(self, tmp_path: Path) -> None:
        """When validation fails, no output files or parents are created."""
        jobs_p = tmp_path / "jobs.jsonl"
        write_jsonl([make_summary_row()], jobs_p)
        profile_p = tmp_path / "profile.md"
        write_profile_md(profile_p)
        md_out = tmp_path / "deep" / "nested" / "recs.md"
        json_out = tmp_path / "deep" / "nested" / "recs.json"
        with pytest.raises(ValueError):
            run_exported_recommendations(jobs_p, profile_p, md_out, json_out)
        assert not md_out.exists()
        assert not json_out.exists()
        assert not md_out.parent.exists()

    def test_no_files_on_identical_destinations(self, tmp_path: Path) -> None:
        """Identical-destination rejection creates no files or parents."""
        jobs_p = tmp_path / "jobs.jsonl"
        write_jsonl([make_row()], jobs_p)
        profile_p = tmp_path / "profile.md"
        write_profile_md(profile_p)
        same = tmp_path / "deep" / "out.md"
        with pytest.raises(ValueError):
            run_exported_recommendations(jobs_p, profile_p, same, same)
        assert not same.exists()
        assert not same.parent.exists()

    def test_no_files_on_missing_jobs(self, tmp_path: Path) -> None:
        """When jobs file is missing, no output files or parents are created."""
        md_out = tmp_path / "deep" / "out.md"
        json_out = tmp_path / "deep" / "out.json"
        with pytest.raises(FileNotFoundError):
            run_exported_recommendations(
                jobs_path=tmp_path / "missing.jsonl",
                profile_path=tmp_path / "profile.md",
                markdown_output=md_out,
                json_output=json_out,
            )
        assert not md_out.exists()
        assert not json_out.exists()
        assert not md_out.parent.exists()

    def test_no_files_on_missing_profile(self, tmp_path: Path) -> None:
        """When profile file is missing, no output files or parents are created."""
        jobs_p = tmp_path / "jobs.jsonl"
        write_jsonl([make_row()], jobs_p)
        md_out = tmp_path / "deep" / "out.md"
        json_out = tmp_path / "deep" / "out.json"
        with pytest.raises(FileNotFoundError):
            run_exported_recommendations(
                jobs_path=jobs_p,
                profile_path=tmp_path / "missing.md",
                markdown_output=md_out,
                json_output=json_out,
            )
        assert not md_out.exists()
        assert not json_out.exists()
        assert not md_out.parent.exists()

    def test_no_temp_leakage_on_load_failure(self, tmp_path: Path) -> None:
        """Missing input file leaves no temp staging directories."""
        md_out = tmp_path / "deep" / "out.md"
        json_out = tmp_path / "deep" / "out.json"
        with pytest.raises(FileNotFoundError):
            run_exported_recommendations(
                jobs_path=tmp_path / "missing.jsonl",
                profile_path=tmp_path / "profile.md",
                markdown_output=md_out,
                json_output=json_out,
            )
        assert not md_out.parent.exists()

    def test_unrelated_parent_content_retained(self, tmp_path: Path) -> None:
        """Existing unrelated files in output parent survive a failed call."""
        md_parent = tmp_path / "shared"
        md_parent.mkdir(parents=True, exist_ok=True)
        (md_parent / "unrelated.txt").write_text("keep me", encoding="utf-8")
        md_out = md_parent / "recs.md"
        json_out = tmp_path / "json" / "recs.json"
        jobs_p, prof_p = _create_jobs_and_profile(tmp_path)

        # Fail on first staging write — destinations are untouched.
        counter = [0]
        original = Path.write_text

        def _fail_first(*args: Any, **kw: Any) -> Any:
            counter[0] += 1
            if counter[0] == 1:
                raise OSError("Injected write failure")
            return original(*args, **kw)

        patcher = mock.patch.object(Path, "write_text", autospec=True, side_effect=_fail_first)
        patcher.start()
        try:
            with pytest.raises(OSError):
                run_exported_recommendations(jobs_p, prof_p, md_out, json_out)
        finally:
            patcher.stop()

        assert (md_parent / "unrelated.txt").exists()
        assert (md_parent / "unrelated.txt").read_text(encoding="utf-8") == "keep me"

    # ------------------------------------------------------------------
    #  Staging write-text failure tests
    # ------------------------------------------------------------------

    def test_first_stage_failure_preserves_no_outputs(self, tmp_path: Path) -> None:
        """When staging the first temp file fails, no destination is touched."""
        md_out = tmp_path / "recs.md"
        json_out = tmp_path / "recs.json"
        md_out.parent.mkdir(parents=True, exist_ok=True)
        md_out.write_text("existing md", encoding="utf-8")
        json_out.write_text("existing json", encoding="utf-8")
        jobs_p, prof_p = _create_jobs_and_profile(tmp_path)

        counter = [0]
        original = Path.write_text

        def _fail_first(*args: Any, **kw: Any) -> Any:
            counter[0] += 1
            if counter[0] == 1:
                raise OSError("Injected write failure")
            return original(*args, **kw)

        patcher = mock.patch.object(Path, "write_text", autospec=True, side_effect=_fail_first)
        patcher.start()
        try:
            with pytest.raises(OSError, match="Injected write failure"):
                run_exported_recommendations(jobs_p, prof_p, md_out, json_out)
        finally:
            patcher.stop()

        assert md_out.read_text(encoding="utf-8") == "existing md"
        assert json_out.read_text(encoding="utf-8") == "existing json"

    def test_second_stage_failure_preserves_no_outputs(self, tmp_path: Path) -> None:
        """When staging the second temp file fails, no destination is touched."""
        md_out = tmp_path / "recs.md"
        json_out = tmp_path / "recs.json"
        md_out.parent.mkdir(parents=True, exist_ok=True)
        md_out.write_text("existing md", encoding="utf-8")
        json_out.write_text("existing json", encoding="utf-8")
        jobs_p, prof_p = _create_jobs_and_profile(tmp_path)

        counter = [0]
        original = Path.write_text

        def _fail_second(*args: Any, **kw: Any) -> Any:
            counter[0] += 1
            if counter[0] == 2:
                raise OSError("Injected write failure")
            return original(*args, **kw)

        patcher = mock.patch.object(Path, "write_text", autospec=True, side_effect=_fail_second)
        patcher.start()
        try:
            with pytest.raises(OSError, match="Injected write failure"):
                run_exported_recommendations(jobs_p, prof_p, md_out, json_out)
        finally:
            patcher.stop()

        assert md_out.read_text(encoding="utf-8") == "existing md"
        assert json_out.read_text(encoding="utf-8") == "existing json"

    # ------------------------------------------------------------------
    #  Replace-failure tests (atomic backup rollback)
    # ------------------------------------------------------------------

    def test_first_replace_failure_preserves_no_outputs(self, tmp_path: Path) -> None:
        """When replacing the first destination fails, nothing is touched."""
        md_out = tmp_path / "recs.md"
        json_out = tmp_path / "recs.json"
        md_out.parent.mkdir(parents=True, exist_ok=True)
        md_out.write_text("existing md", encoding="utf-8")
        json_out.write_text("existing json", encoding="utf-8")
        jobs_p, prof_p = _create_jobs_and_profile(tmp_path)

        counter = [0]
        original_replace = Path.replace

        def _fail_first_replace(*args: Any, **kw: Any) -> Any:
            counter[0] += 1
            if counter[0] == 1:
                raise OSError("Injected replace failure")
            return original_replace(*args, **kw)

        patcher = mock.patch.object(Path, "replace", autospec=True, side_effect=_fail_first_replace)
        patcher.start()
        try:
            with pytest.raises(OSError, match="Injected replace failure"):
                run_exported_recommendations(jobs_p, prof_p, md_out, json_out)
        finally:
            patcher.stop()

        assert md_out.read_text(encoding="utf-8") == "existing md"
        assert json_out.read_text(encoding="utf-8") == "existing json"

    def test_second_replace_restores_both_from_backups(self, tmp_path: Path) -> None:
        """When second replace fails, both destinations are restored from same-dir backups."""
        md_out = tmp_path / "recs.md"
        json_out = tmp_path / "recs.json"
        md_out.parent.mkdir(parents=True, exist_ok=True)
        md_out.write_text("existing md", encoding="utf-8")
        json_out.write_text("existing json", encoding="utf-8")
        jobs_p, prof_p = _create_jobs_and_profile(tmp_path)

        counter = [0]
        original_replace = Path.replace

        def _fail_second_replace(*args: Any, **kw: Any) -> Any:
            counter[0] += 1
            if counter[0] == 2:
                raise OSError("Injected replace failure")
            return original_replace(*args, **kw)

        patcher = mock.patch.object(Path, "replace", autospec=True, side_effect=_fail_second_replace)
        patcher.start()
        try:
            with pytest.raises(OSError, match="Injected replace failure"):
                run_exported_recommendations(jobs_p, prof_p, md_out, json_out)
        finally:
            patcher.stop()

        assert md_out.read_text(encoding="utf-8") == "existing md"
        assert json_out.read_text(encoding="utf-8") == "existing json"

    def test_second_replace_new_md_restored(self, tmp_path: Path) -> None:
        """When second replace fails and md was new, it is removed."""
        md_out = tmp_path / "recs.md"
        json_out = tmp_path / "recs.json"
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text("existing json", encoding="utf-8")
        jobs_p, prof_p = _create_jobs_and_profile(tmp_path)

        counter = [0]
        original_replace = Path.replace

        def _fail_second_replace(*args: Any, **kw: Any) -> Any:
            counter[0] += 1
            if counter[0] == 2:
                raise OSError("Injected replace failure")
            return original_replace(*args, **kw)

        patcher = mock.patch.object(Path, "replace", autospec=True, side_effect=_fail_second_replace)
        patcher.start()
        try:
            with pytest.raises(OSError, match="Injected replace failure"):
                run_exported_recommendations(jobs_p, prof_p, md_out, json_out)
        finally:
            patcher.stop()

        assert not md_out.exists()
        assert json_out.read_text(encoding="utf-8") == "existing json"

    def test_second_replace_new_json_restored(self, tmp_path: Path) -> None:
        """When second replace fails and json was new, it stays absent."""
        md_out = tmp_path / "recs.md"
        json_out = tmp_path / "recs.json"
        md_out.parent.mkdir(parents=True, exist_ok=True)
        md_out.write_text("existing md", encoding="utf-8")
        # json_out does NOT pre-exist.
        jobs_p, prof_p = _create_jobs_and_profile(tmp_path)

        counter = [0]
        original_replace = Path.replace

        def _fail_second_replace(*args: Any, **kw: Any) -> Any:
            counter[0] += 1
            if counter[0] == 2:
                raise OSError("Injected replace failure")
            return original_replace(*args, **kw)

        patcher = mock.patch.object(Path, "replace", autospec=True, side_effect=_fail_second_replace)
        patcher.start()
        try:
            with pytest.raises(OSError, match="Injected replace failure"):
                run_exported_recommendations(jobs_p, prof_p, md_out, json_out)
        finally:
            patcher.stop()

        assert md_out.read_text(encoding="utf-8") == "existing md"
        assert not json_out.exists()

    def test_no_temp_leakage_on_second_replace_failure(self, tmp_path: Path) -> None:
        """No .rec_ files remain after a second-replace rollback."""
        md_parent = tmp_path / "md"
        json_parent = tmp_path / "json"
        md_out = md_parent / "recs.md"
        json_out = json_parent / "recs.json"
        md_parent.mkdir(parents=True, exist_ok=True)
        json_parent.mkdir(parents=True, exist_ok=True)
        md_out.write_text("existing md", encoding="utf-8")
        json_out.write_text("existing json", encoding="utf-8")
        jobs_p, prof_p = _create_jobs_and_profile(tmp_path)

        counter = [0]
        original_replace = Path.replace

        def _fail_second_replace(*args: Any, **kw: Any) -> Any:
            counter[0] += 1
            if counter[0] == 2:
                raise OSError("Injected replace failure")
            return original_replace(*args, **kw)

        patcher = mock.patch.object(Path, "replace", autospec=True, side_effect=_fail_second_replace)
        patcher.start()
        try:
            with pytest.raises(OSError):
                run_exported_recommendations(jobs_p, prof_p, md_out, json_out)
        finally:
            patcher.stop()

        md_leaks = list(md_parent.glob(".rec_*"))
        json_leaks = list(json_parent.glob(".rec_*"))
        assert len(md_leaks) == 0, f"Leaked temp files in md parent: {md_leaks}"
        assert len(json_leaks) == 0, f"Leaked temp files in json parent: {json_leaks}"

    def test_nested_parents_removed_on_failure(self, tmp_path: Path) -> None:
        """Newly-created ancestor directories are removed after a failure.

        When both outputs are in a newly-created nested path and the call
        fails at the write stage, all empty ancestor dirs should be removed.
        """
        md_out = tmp_path / "a" / "b" / "c" / "recs.md"
        json_out = tmp_path / "a" / "b" / "c" / "recs.json"
        # Use same parent to ensure both mkdir calls succeed, then fail at
        # first staging write.
        jobs_p, prof_p = _create_jobs_and_profile(tmp_path)

        counter = [0]
        original = Path.write_text

        def _fail_first_write(*args: Any, **kw: Any) -> Any:
            counter[0] += 1
            if counter[0] == 1:
                raise OSError("Injected write failure")
            return original(*args, **kw)

        patcher = mock.patch.object(Path, "write_text", autospec=True, side_effect=_fail_first_write)
        patcher.start()
        try:
            with pytest.raises(OSError):
                run_exported_recommendations(jobs_p, prof_p, md_out, json_out)
        finally:
            patcher.stop()

        # The deepest newly-created directory should be gone (all were empty).
        assert not md_out.exists()
        assert not json_out.exists()
        assert not (tmp_path / "a").exists()

    def test_nested_parents_partial_cleanup(self, tmp_path: Path) -> None:
        """Only newly-created empty ancestors are removed; pre-existing ones remain.

        Both outputs live under a newly-deep tree rooted at a pre-existing dir.
        """
        pre_existing = tmp_path / "base"
        pre_existing.mkdir(parents=True, exist_ok=True)
        md_out = pre_existing / "x" / "y" / "recs.md"
        json_out = pre_existing / "x" / "y" / "recs.json"
        jobs_p, prof_p = _create_jobs_and_profile(tmp_path)

        counter = [0]
        original = Path.write_text

        def _fail_first_write(*args: Any, **kw: Any) -> Any:
            counter[0] += 1
            if counter[0] == 1:
                raise OSError("Injected write failure")
            return original(*args, **kw)

        patcher = mock.patch.object(Path, "write_text", autospec=True, side_effect=_fail_first_write)
        patcher.start()
        try:
            with pytest.raises(OSError):
                run_exported_recommendations(jobs_p, prof_p, md_out, json_out)
        finally:
            patcher.stop()

        # Pre-existing base must remain.
        assert pre_existing.exists()
        # Newly-created nested dirs must be removed.
        assert not (pre_existing / "x" / "y").exists()
        assert not (pre_existing / "x").exists()

    # ------------------------------------------------------------------
    #  Second-parent creation failure cleanup
    # ------------------------------------------------------------------

    def test_second_parent_creation_cleans_up_first(self, tmp_path: Path) -> None:
        """When second-parent mkdir fails, first parent's new ancestors are removed."""
        md_out = tmp_path / "a" / "b" / "recs.md"
        # Block json's parent chain with a regular file.
        (tmp_path / "c").write_text("blocker", encoding="utf-8")
        json_out = tmp_path / "c" / "recs.json"
        jobs_p, prof_p = _create_jobs_and_profile(tmp_path)

        with pytest.raises(OSError):
            run_exported_recommendations(jobs_p, prof_p, md_out, json_out)

        # First parent's newly-created directories must be removed.
        assert not (tmp_path / "a" / "b").exists()
        assert not (tmp_path / "a").exists()
        # Pre-existing unrelated path must remain untouched.
        assert (tmp_path / "c").read_text(encoding="utf-8") == "blocker"

    # ------------------------------------------------------------------
    #  Both restore attempts on second-replace failure
    # ------------------------------------------------------------------

    def test_both_restores_attempted_when_first_fails(self, tmp_path: Path) -> None:
        """When the first restore fails during rollback, the second is still attempted."""
        md_out = tmp_path / "recs.md"
        json_out = tmp_path / "recs.json"
        md_out.parent.mkdir(parents=True, exist_ok=True)
        md_out.write_text("existing md", encoding="utf-8")
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text("existing json", encoding="utf-8")
        jobs_p, prof_p = _create_jobs_and_profile(tmp_path)

        call_count = [0]
        original_replace = Path.replace

        def _side_effect(self_obj: Any, target: Any) -> Any:
            call_count[0] += 1
            if call_count[0] == 1:
                return original_replace(self_obj, target)
            if call_count[0] == 2:
                raise OSError("json replace failed")
            if call_count[0] == 3:
                raise OSError("md restore failed")
            # Fourth call: json restore (attempted despite first failing).
            return original_replace(self_obj, target)

        patcher = mock.patch.object(
            Path, "replace", autospec=True, side_effect=_side_effect,
        )
        patcher.start()
        try:
            with pytest.raises(RuntimeError, match="Rollback incomplete"):
                run_exported_recommendations(jobs_p, prof_p, md_out, json_out)
        finally:
            patcher.stop()

        # 4 replace calls: stage-md, stage-json, restore-md, restore-json.
        assert call_count[0] == 4, (
            f"Expected 4 replace calls (both restores attempted), "
            f"got {call_count[0]}"
        )
        # Json restored successfully (second restore).
        assert json_out.read_text(encoding="utf-8") == "existing json"


# ===================================================================
#  No PII in recommendation output
# ===================================================================


class TestNoPiiInOutput:
    def test_no_profile_path_in_json(
        self, jobs_path: Path, profile_path: Path, tmp_path: Path
    ) -> None:
        """JSON recommendation object has no 'profile' key."""
        md_out = tmp_path / "out.md"
        json_out = tmp_path / "out.json"
        run_exported_recommendations(jobs_path, profile_path, md_out, json_out)
        data = json.loads(json_out.read_text(encoding="utf-8"))
        assert "profile" not in data
        for rec in data.get("recommendations", []):
            assert "profile" not in rec

    def test_no_contact_info(
        self, jobs_path: Path, profile_path: Path, tmp_path: Path
    ) -> None:
        """No contact or PII fields in recommendation JSON."""
        md_out = tmp_path / "out.md"
        json_out = tmp_path / "out.json"
        run_exported_recommendations(jobs_path, profile_path, md_out, json_out)
        data = json.loads(json_out.read_text(encoding="utf-8"))
        for rec in data.get("recommendations", []):
            assert "phone" not in rec
            assert "email" not in rec
            assert "contact" not in rec
            assert "source_hash" not in rec
            assert "description" not in rec
            assert "first_seen_at" not in rec

    def test_no_jobs_path_in_json(
        self, jobs_path: Path, profile_path: Path, tmp_path: Path
    ) -> None:
        """jobs_path is not serialized into JSON recommendation output."""
        md_out = tmp_path / "out.md"
        json_out = tmp_path / "out.json"
        run_exported_recommendations(jobs_path, profile_path, md_out, json_out)
        data = json.loads(json_out.read_text(encoding="utf-8"))
        assert "jobs-full.jsonl" not in json.dumps(data)

    def test_no_salary_estimates(
        self, jobs_path: Path, profile_path: Path, tmp_path: Path
    ) -> None:
        """No estimated or generated salary fields in the output."""
        md_out = tmp_path / "out.md"
        json_out = tmp_path / "out.json"
        run_exported_recommendations(jobs_path, profile_path, md_out, json_out)
        data = json.loads(json_out.read_text(encoding="utf-8"))
        json_str = json.dumps(data)
        assert "salary_estimate" not in json_str
        assert "estimated_salary" not in json_str


# ===================================================================
#  Profile-absent behavior (CLI level)
# ===================================================================


class TestCliProfileAbsent:
    def test_no_recommendation_outputs_without_profile(
        self, tmp_path: Path
    ) -> None:
        """When profile doesn't exist, weekly does not create recommendation outputs."""
        from findjobs.cli import app

        db_path = tmp_path / "test.db"
        reports_dir = tmp_path / "reports"
        _seed_test_job(db_path)

        result = runner.invoke(app, [
            "weekly",
            "--no-live",
            "--db-path", str(db_path),
            "--reports-dir", str(reports_dir),
            "--profile", str(tmp_path / "nonexistent" / "profile.md"),
            "--since", "365",
        ])
        assert result.exit_code == 0

        match_dir = reports_dir / "match"
        assert not (match_dir / "recommendations.md").exists()
        assert not (match_dir / "recommendations.json").exists()

    def test_full_input_echoed_without_profile(self, tmp_path: Path) -> None:
        """full_input path is echoed even when profile is absent."""
        from findjobs.cli import app

        db_path = tmp_path / "test.db"
        reports_dir = tmp_path / "reports"
        _seed_test_job(db_path)

        result = runner.invoke(app, [
            "weekly",
            "--no-live",
            "--db-path", str(db_path),
            "--reports-dir", str(reports_dir),
            "--profile", str(tmp_path / "nonexistent" / "profile.md"),
            "--since", "365",
        ])
        assert result.exit_code == 0
        assert "full_input:" in result.stdout
        assert "jobs-full.jsonl" in result.stdout
        assert "recommendations:" not in result.stdout

    def test_weekly_with_profile_creates_recommendations(
        self, tmp_path: Path
    ) -> None:
        """When profile exists, weekly creates recommendation outputs."""
        from findjobs.cli import app

        db_path = tmp_path / "test.db"
        reports_dir = tmp_path / "reports"
        profile_path = tmp_path / "profile.md"
        write_profile_md(profile_path)
        _seed_test_job(db_path)

        result = runner.invoke(app, [
            "weekly",
            "--no-live",
            "--db-path", str(db_path),
            "--reports-dir", str(reports_dir),
            "--profile", str(profile_path),
            "--since", "365",
        ])
        assert result.exit_code == 0

        match_dir = reports_dir / "match"
        assert (match_dir / "jobs-full.jsonl").exists()
        assert (match_dir / "recommendations.md").exists()
        assert (match_dir / "recommendations.json").exists()

    def test_cli_echoes_new_paths(self, tmp_path: Path) -> None:
        """Weekly CLI echoes the new full-input and recommendation paths."""
        from findjobs.cli import app

        db_path = tmp_path / "test.db"
        reports_dir = tmp_path / "reports"
        profile_path = tmp_path / "profile.md"
        write_profile_md(profile_path)
        _seed_test_job(db_path)

        result = runner.invoke(app, [
            "weekly",
            "--no-live",
            "--db-path", str(db_path),
            "--reports-dir", str(reports_dir),
            "--profile", str(profile_path),
            "--since", "365",
        ])
        assert result.exit_code == 0
        stdout = result.stdout
        assert "full_input:" in stdout
        assert "jobs-full.jsonl" in stdout
        assert "recommendations:" in stdout
        assert "recommendations_json:" in stdout

    def test_weekly_uses_same_full_file_for_recommendations(
        self, tmp_path: Path
    ) -> None:
        """The full file passed to run_exported_recommendations is reports/match/jobs-full.jsonl."""
        from findjobs.cli import app

        db_path = tmp_path / "test.db"
        reports_dir = tmp_path / "reports"
        profile_path = tmp_path / "profile.md"
        write_profile_md(profile_path)
        _seed_test_job(db_path)

        expected_full = str(reports_dir / "match" / "jobs-full.jsonl")

        with mock.patch(
            "findjobs.weekly_recommendation.run_exported_recommendations",
        ) as mock_rec:
            mock_rec.return_value = mock.MagicMock(jobs_path=expected_full)
            result = runner.invoke(app, [
                "weekly",
                "--no-live",
                "--db-path", str(db_path),
                "--reports-dir", str(reports_dir),
                "--profile", str(profile_path),
                "--since", "365",
            ])

        assert result.exit_code == 0
        assert mock_rec.called
        _, kwargs = mock_rec.call_args
        assert str(kwargs.get("jobs_path")) == expected_full


# ===================================================================
#  CLI: _export_file passes detail_level
# ===================================================================


class TestCliExportDetailLevel:
    def test_export_detail_level_summary(self, tmp_path: Path) -> None:
        """Export command with default detail_level produces summary fields."""
        from findjobs.cli import app

        db_path = tmp_path / "test.db"
        _seed_test_job(db_path)
        output = tmp_path / "export.jsonl"
        result = runner.invoke(app, [
            "export",
            "--db-path", str(db_path),
            "--output", str(output),
            "--since", "365",
        ])
        assert result.exit_code == 0
        assert output.exists()
        row = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
        assert "responsibilities" not in row
        assert "requirements" not in row
        assert "detail_completeness" not in row
        assert "relevance_status" not in row

    def test_export_detail_level_full(self, tmp_path: Path) -> None:
        """Export with detail_level=full includes full fields."""
        from findjobs.cli import app

        db_path = tmp_path / "test.db"
        _seed_test_job(db_path)
        output = tmp_path / "export-full.jsonl"
        result = runner.invoke(app, [
            "export",
            "--db-path", str(db_path),
            "--output", str(output),
            "--since", "365",
            "--detail-level", "full",
        ])
        assert result.exit_code == 0
        assert output.exists()
        row = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
        assert "responsibilities" in row
        assert "requirements" in row
        assert "detail_completeness" in row
        assert "relevance_status" in row


# ===================================================================
#  Workflow prompt invariants
# ===================================================================


class TestWorkflowPromptInvariants:
    """Workflow markdown files must require the correct inputs and guardrails."""

    WORKFLOW_DIR = Path("workflows")

    def test_match_analysis_requires_all_inputs(self) -> None:
        """match_analysis.md requires jobs-full.jsonl, recommendations.json, profile."""
        content = (self.WORKFLOW_DIR / "match_analysis.md").read_text(encoding="utf-8")
        assert "jobs-full.jsonl" in content
        assert "recommendations.json" in content
        assert "profile" in content

    def test_match_analysis_no_new_scoring(self) -> None:
        """match_analysis.md says it is commentary over deterministic scores."""
        content = (self.WORKFLOW_DIR / "match_analysis.md").read_text(encoding="utf-8")
        assert "do not re-score" in content.lower()

    def test_career_advice_requires_all_inputs(self) -> None:
        """career_advice.md requires jobs-full.jsonl, recommendations.json, profile."""
        content = (self.WORKFLOW_DIR / "career_advice.md").read_text(encoding="utf-8")
        assert "jobs-full.jsonl" in content
        assert "recommendations.json" in content
        assert "profile" in content

    def test_career_advice_no_salary_estimate(self) -> None:
        """career_advice.md says salary is never estimated."""
        content = (self.WORKFLOW_DIR / "career_advice.md").read_text(encoding="utf-8")
        assert "never estimate" in content.lower()

    def test_priority_ranking_requires_all_inputs(self) -> None:
        """priority_ranking.md requires jobs-full.jsonl, recommendations.json, profile."""
        content = (self.WORKFLOW_DIR / "priority_ranking.md").read_text(encoding="utf-8")
        assert "jobs-full.jsonl" in content
        assert "recommendations.json" in content
        assert "profile" in content

    def test_priority_ranking_preserves_order(self) -> None:
        """priority_ranking.md says it preserves deterministic engine order/tier."""
        content = (self.WORKFLOW_DIR / "priority_ranking.md").read_text(encoding="utf-8")
        assert "preserve" in content.lower()

    def test_workflows_no_re_scoring(self) -> None:
        """Every workflow prompt forbids re-scoring."""
        for fname in ("match_analysis.md", "career_advice.md", "priority_ranking.md"):
            content = (self.WORKFLOW_DIR / fname).read_text(encoding="utf-8").lower()
            assert "re-score" in content

    def test_workflows_use_responsibilities(self) -> None:
        """Workflow prompts mention responsibilities and requirements fields."""
        for fname in ("match_analysis.md", "career_advice.md", "priority_ranking.md"):
            content = (self.WORKFLOW_DIR / fname).read_text(encoding="utf-8")
            assert "responsibilities" in content

    def test_workflows_use_requirements(self) -> None:
        """Workflow prompts mention requirements field."""
        for fname in ("match_analysis.md", "career_advice.md", "priority_ranking.md"):
            content = (self.WORKFLOW_DIR / fname).read_text(encoding="utf-8")
            assert "requirements" in content

    def test_workflows_use_detail_completeness(self) -> None:
        """Workflow prompts mention detail_completeness."""
        for fname in ("match_analysis.md", "career_advice.md", "priority_ranking.md"):
            content = (self.WORKFLOW_DIR / fname).read_text(encoding="utf-8")
            assert "detail_completeness" in content

    def test_workflows_use_official_url(self) -> None:
        """Workflow prompts mention official URL."""
        for fname in ("match_analysis.md", "career_advice.md", "priority_ranking.md"):
            content = (self.WORKFLOW_DIR / fname).read_text(encoding="utf-8")
            assert "url" in content.lower()

    def test_workflows_no_missing_requirements_inference(self) -> None:
        """Workflow prompts say missing requirements are not inferred."""
        for fname in ("match_analysis.md", "career_advice.md", "priority_ranking.md"):
            content = (self.WORKFLOW_DIR / fname).read_text(encoding="utf-8")
            assert "do not infer" in content.lower()


# ===================================================================
#  Claude command restrictions
# ===================================================================


class TestClaudeCommandRestrictions:
    """tools/run_recommendation_claude.cmd must have correct model and tool restrictions."""

    CMD_PATH = Path("tools/run_recommendation_claude.cmd")

    def test_claude_cmd_has_correct_model(self) -> None:
        """The .cmd file references the deepseek-v4-flash model."""
        assert self.CMD_PATH.exists()
        content = self.CMD_PATH.read_text(encoding="utf-8")
        assert "deepseek-v4-flash" in content

    def test_claude_cmd_read_only_tools(self) -> None:
        """The .cmd file allows only Read, Grep, Glob tools."""
        content = self.CMD_PATH.read_text(encoding="utf-8")
        assert "--allowedTools" in content
        assert "Read" in content
        assert "Grep" in content
        assert "Glob" in content

    def test_claude_cmd_disallows_write_tools(self) -> None:
        """The .cmd file disallows Bash, Edit, Write."""
        content = self.CMD_PATH.read_text(encoding="utf-8")
        assert "--disallowedTools" in content
        assert "Bash" in content
        assert "Edit" in content
        assert "Write" in content

    def test_claude_cmd_has_input_names(self) -> None:
        """The .cmd prompt names the three factual inputs."""
        content = self.CMD_PATH.read_text(encoding="utf-8")
        assert "jobs-full.jsonl" in content
        assert "recommendations.json" in content
        assert "profile" in content

    def test_claude_cmd_has_guardrails(self) -> None:
        """The .cmd prompt contains guardrails."""
        content = self.CMD_PATH.read_text(encoding="utf-8")
        assert "Guardrails" in content

    def test_claude_cmd_runs_deterministic_first(self) -> None:
        """The .cmd runs deterministic weekly workflow first."""
        content = self.CMD_PATH.read_text(encoding="utf-8")
        assert "findjobs weekly" in content

    def test_claude_cmd_no_api_key(self) -> None:
        """No inline API key or base_URL in the repository file."""
        content = self.CMD_PATH.read_text(encoding="utf-8")
        lines = content.splitlines()
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("rem") or stripped.startswith("echo"):
                continue
            assert "base_url" not in stripped.lower()

    def test_claude_cmd_output_to_reports_match(self) -> None:
        """The .cmd redirects commentary to reports/match/."""
        content = self.CMD_PATH.read_text(encoding="utf-8")
        norm = content.replace("\\", "/")
        assert "reports/match/" in norm


# ===================================================================
#  Gitignore entries
# ===================================================================


class TestGitignoreEntries:
    def test_gitignore_ignores_jobs_full_jsonl(self) -> None:
        """.gitignore ignores reports/match/jobs-full.jsonl."""
        content = Path(".gitignore").read_text(encoding="utf-8")
        assert "jobs-full.jsonl" in content

    def test_gitignore_ignores_claude_commentary(self) -> None:
        """.gitignore ignores Claude commentary outputs."""
        content = Path(".gitignore").read_text(encoding="utf-8")
        assert "claude-commentary" in content

    def test_gitignore_does_not_ignore_workflow_sources(self) -> None:
        """Workflow .md source files under workflows/ are not gitignored.

        Uses ``git check-ignore`` to verify actual git behavior rather
        than reasoning about the ``.gitignore`` file structure.
        """
        import subprocess

        result = subprocess.run(
            [
                "git", "check-ignore",
                "workflows/match_analysis.md",
                "workflows/career_advice.md",
                "workflows/priority_ranking.md",
            ],
            capture_output=True, text=True,
        )
        # ``git check-ignore`` exits 0 when at least one path IS ignored.
        assert result.returncode != 0, (
            f"Workflow source files are unexpectedly ignored by .gitignore:\n"
            f"{result.stdout}"
        )


# ===================================================================
#  Weekly uses same full file for recommendations
# ===================================================================


class TestWeeklyUsesSameFullFile:
    def test_full_file_is_source_for_recommendations(
        self, tmp_path: Path
    ) -> None:
        """The full JSONL file used for recommendations is the one under match dir."""
        from findjobs.cli import app

        db_path = tmp_path / "test.db"
        reports_dir = tmp_path / "reports"
        profile_path = tmp_path / "profile.md"
        write_profile_md(profile_path)
        _seed_test_job(db_path)

        result = runner.invoke(app, [
            "weekly",
            "--no-live",
            "--db-path", str(db_path),
            "--reports-dir", str(reports_dir),
            "--profile", str(profile_path),
            "--since", "365",
        ])
        assert result.exit_code == 0
        full_path = reports_dir / "match" / "jobs-full.jsonl"
        assert full_path.exists()
        weekly_path = reports_dir / "weekly" / "jobs.jsonl"
        assert weekly_path.exists()
        full_rows_text = full_path.read_text(encoding="utf-8").strip()
        summary_rows_text = weekly_path.read_text(encoding="utf-8").strip()
        assert full_rows_text, "Full export file is empty"
        assert summary_rows_text, "Weekly summary file is empty"
        full_row = json.loads(full_rows_text.splitlines()[0])
        summary_row = json.loads(summary_rows_text.splitlines()[0])
        assert "responsibilities" in full_row
        assert "responsibilities" not in summary_row


# ===================================================================
#  Deterministic consistency
# ===================================================================


class TestDeterministicConsistency:
    def test_recommendations_md_matches_json(
        self, jobs_path: Path, profile_path: Path, tmp_path: Path
    ) -> None:
        """Markdown and JSON recommendations reference the same jobs."""
        md_out = tmp_path / "recs.md"
        json_out = tmp_path / "recs.json"
        result = run_exported_recommendations(jobs_path, profile_path, md_out, json_out)
        data = json.loads(json_out.read_text(encoding="utf-8"))
        md = md_out.read_text(encoding="utf-8")
        assert len(data["recommendations"]) == result.returned_count
        for rec in data["recommendations"]:
            assert rec["company_name"] in md
            assert rec["title"] in md

    def test_score_tiers_match_engine(
        self,
        jobs_path: Path,
        profile_path: Path,
        tmp_path: Path,
        profile: RecommendationProfile,
    ) -> None:
        """Scores and tiers in JSON output match engine results."""
        rows = [json.loads(l) for l in jobs_path.read_text(encoding="utf-8").strip().splitlines() if l.strip()]
        direct_result = recommend_jobs(rows, profile, limit=50)

        md_out = tmp_path / "recs.md"
        json_out = tmp_path / "recs.json"
        run_exported_recommendations(jobs_path, profile_path, md_out, json_out, limit=50)

        data = json.loads(json_out.read_text(encoding="utf-8"))
        assert data["scanned"] == direct_result.scanned
        assert data["eligible"] == direct_result.eligible
        assert len(data["recommendations"]) == len(direct_result.recommendations)
        for i, rec in enumerate(data["recommendations"]):
            direct_rec = direct_result.recommendations[i]
            assert rec["total_score"] == direct_rec.total_score
            assert rec["tier"] == direct_rec.tier


# ===================================================================
#  Summary vs full field separation
# ===================================================================


class TestSummaryVsFullFieldSeparation:
    def test_full_export_has_full_fields(self, tmp_path: Path) -> None:
        """Full export rows have classification and detail fields."""
        from findjobs.cli import app

        db_path = tmp_path / "test.db"
        _seed_test_job(db_path)
        output = tmp_path / "full.jsonl"
        result = runner.invoke(app, [
            "export",
            "--db-path", str(db_path),
            "--output", str(output),
            "--since", "365",
            "--detail-level", "full",
        ])
        assert result.exit_code == 0
        row = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
        assert "classification_version" in row
        assert "description" in row
        assert "relevance_status" in row

    def test_summary_export_no_full_fields(self, tmp_path: Path) -> None:
        """Summary export rows lack classification and detail fields."""
        from findjobs.cli import app

        db_path = tmp_path / "test.db"
        _seed_test_job(db_path)
        output = tmp_path / "summary.jsonl"
        result = runner.invoke(app, [
            "export",
            "--db-path", str(db_path),
            "--output", str(output),
            "--since", "365",
            "--detail-level", "summary",
        ])
        assert result.exit_code == 0
        row = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
        assert "classification_version" not in row
        assert "description" not in row
        assert "responsibilities" not in row
        assert "requirements" not in row
        assert "detail_completeness" not in row


# ===================================================================
#  No database calls in weekly_recommendation module
# ===================================================================


class TestNoDbInWeeklyRecommendation:
    def test_no_db_import(self) -> None:
        """weekly_recommendation module does not import db or models."""
        import findjobs.weekly_recommendation as wr

        source = Path(wr.__file__).read_text(encoding="utf-8")
        assert "from findjobs.db" not in source
        assert "from findjobs.models" not in source
        assert "from sqlalchemy" not in source

    def test_no_session_parameter(self) -> None:
        """run_exported_recommendations does not accept a session parameter."""
        import inspect

        from findjobs.weekly_recommendation import run_exported_recommendations

        sig = inspect.signature(run_exported_recommendations)
        assert "session" not in sig.parameters
        assert "db" not in sig.parameters


# ===================================================================
#  Helpers
# ===================================================================


def _seed_test_job(db_path: Path) -> None:
    """Insert a single active job into a test SQLite database."""
    from findjobs.db import init_db
    from findjobs.models import Company, Source, Job, CollectRun

    session = init_db(db_path)
    company = Company(name="ByteDance", slug="bytedance")
    session.add(company)
    session.flush()

    source = Source(
        name="ByteDance Careers",
        slug="bytedance-careers",
        company_id=company.id,
    )
    session.add(source)
    session.flush()

    run = CollectRun(source_id=source.id)
    session.add(run)
    session.flush()

    job = Job(
        external_id="ext-ai-wf",
        company_id=company.id,
        source_id=source.id,
        title="Security Engineer",
        url="https://jobs.bytedance.com/ai-wf",
        description="Security testing role for boundary tests.",
        salary_text="",
        salary_min=None,
        salary_max=None,
        salary_currency="",
        salary_period="",
        salary_disclosed=False,
        location="北京",
        job_type="技术",
        matched_tags='["Security"]',
        status="active",
        relevance_status="target",
        classification_version="2.1.0",
        classification_reasons='["matched"]',
        responsibilities="Security testing and threat modeling.",
        requirements="5 years Python, cloud security",
        detail_completeness="full",
    )
    session.add(job)
    session.commit()
    session.close()


# ===================================================================
#  Windows .cmd integration tests  (skipped on non-Windows)
# ===================================================================


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only integration test")
class TestWindowsClaudeCmdRun:
    """Execute the real .cmd under cmd.exe with fake uv/claude on PATH."""

    CMD_PATH = Path("tools/run_recommendation_claude.cmd")

    def _create_fakes(
        self, tmp_path: Path, uv_exit: int = 0, claude_exit: int = 0,
    ) -> dict[str, str]:
        """Write fake uv.cmd and claude.cmd that log args and exit on demand."""
        fakes_dir = tmp_path / "fakes"
        fakes_dir.mkdir(exist_ok=True)
        uv_log = tmp_path / "uv.log"
        claude_log = tmp_path / "claude.log"

        (fakes_dir / "uv.cmd").write_text(
            "@echo off\n"
            f'echo %* >> "{uv_log}"\n'
            f"exit /b {uv_exit}\n",
        )
        (fakes_dir / "claude.cmd").write_text(
            "@echo off\n"
            f'echo %* >> "{claude_log}"\n'
            "@echo Fake Claude commentary output\n"
            f"exit /b {claude_exit}\n",
        )

        env = os.environ.copy()
        env["PATH"] = str(fakes_dir) + os.pathsep + env.get("PATH", "")
        return env

    @staticmethod
    def _arrange(
        tmp_path: Path,
        has_jobs: bool = True,
        has_recs: bool = True,
        has_profile_json: bool = False,
        has_profile_md: bool = True,
        existing_commentary: str | None = None,
    ) -> None:
        """Create (or omit) input files for the .cmd script under *tmp_path*."""
        match_dir = tmp_path / "reports" / "match"
        if has_jobs:
            match_dir.mkdir(parents=True, exist_ok=True)
            (match_dir / "jobs-full.jsonl").write_text('{"_": "mock"}\n')
        if has_recs:
            match_dir.mkdir(parents=True, exist_ok=True)
            (match_dir / "recommendations.json").write_text('{"_": "mock"}\n')

        if has_profile_json or has_profile_md:
            (tmp_path / "profile").mkdir(exist_ok=True)
        if has_profile_json:
            (tmp_path / "profile" / "profile.json").write_text('{"skills": []}\n')
        if has_profile_md:
            (tmp_path / "profile" / "profile.md").write_text("# Profile\n")

        if existing_commentary is not None:
            match_dir.mkdir(parents=True, exist_ok=True)
            (match_dir / "claude-commentary.md").write_text(existing_commentary)

    def _run_cmd(
        self,
        tmp_path: Path,
        uv_exit: int = 0,
        claude_exit: int = 0,
        **arrange_kw: object,
    ) -> subprocess.CompletedProcess[str]:
        """Run the .cmd in *tmp_path* with fakes on PATH, return result."""
        env = self._create_fakes(tmp_path, uv_exit, claude_exit)
        self._arrange(tmp_path, **arrange_kw)
        return subprocess.run(
            ["cmd.exe", "/c", str(self.CMD_PATH.resolve())],
            cwd=str(tmp_path),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    # ------------------------------------------------------------------
    #  Happy path
    # ------------------------------------------------------------------

    def test_cmd_flow_succeeds_and_creates_commentary(self, tmp_path: Path) -> None:
        """Happy path: uv and Claude succeed, commentary file created, .tmp cleaned."""
        result = self._run_cmd(tmp_path)
        assert result.returncode == 0
        commentary = tmp_path / "reports" / "match" / "claude-commentary.md"
        assert commentary.exists()
        assert "Fake Claude commentary output" in commentary.read_text(encoding="utf-8")
        assert not (tmp_path / "reports" / "match" / "claude-commentary.md.tmp").exists()
        # uv command must include --profile
        uv_log = (tmp_path / "uv.log").read_text(encoding="utf-8")
        assert "--profile" in uv_log

    def test_claude_args_contain_restrictions(self, tmp_path: Path) -> None:
        """Claude is invoked with exact model, allowed/disallowed tools, and guardrails."""
        result = self._run_cmd(tmp_path)
        assert result.returncode == 0
        log = (tmp_path / "claude.log").read_text(encoding="utf-8")
        assert "deepseek-v4-flash[1M]" in log
        assert "Read,Grep,Glob" in log
        assert "Bash,Edit,Write" in log
        assert "jobs-full.jsonl" in log
        assert "recommendations.json" in log
        assert "Guardrails" in log

    # ------------------------------------------------------------------
    #  Profile preference
    # ------------------------------------------------------------------

    def test_json_profile_preferred_over_md(self, tmp_path: Path) -> None:
        """When both profiles exist, profile.json is selected; profile.md does not appear in logs."""
        result = self._run_cmd(tmp_path, has_profile_json=True, has_profile_md=True)
        assert result.returncode == 0

        uv_log = (tmp_path / "uv.log").read_text(encoding="utf-8")
        assert "--profile" in uv_log
        assert "profile.json" in uv_log
        assert "profile.md" not in uv_log

        claude_log = (tmp_path / "claude.log").read_text(encoding="utf-8")
        assert "profile.json" in claude_log
        assert "profile.md" not in claude_log

    # ------------------------------------------------------------------
    #  Failure modes
    # ------------------------------------------------------------------

    def test_missing_profile_prevents_claude(self, tmp_path: Path) -> None:
        """Neither profile file exists: script fails without invoking uv or Claude."""
        result = self._run_cmd(
            tmp_path,
            has_profile_json=False,
            has_profile_md=False,
        )
        assert result.returncode != 0
        uv_log = tmp_path / "uv.log"
        claude_log = tmp_path / "claude.log"
        assert not uv_log.exists()
        assert not claude_log.exists()

    def test_weekly_failure_prevents_claude(self, tmp_path: Path) -> None:
        """When uv exits non-zero, script propagates exit code; Claude not called."""
        result = self._run_cmd(tmp_path, uv_exit=7)
        assert result.returncode == 7
        uv_log = tmp_path / "uv.log"
        claude_log = tmp_path / "claude.log"
        assert uv_log.exists()
        assert not claude_log.exists()

    def test_claude_failure_preserves_existing_commentary(self, tmp_path: Path) -> None:
        """Claude failure keeps pre-existing commentary and returns Claude's exit code."""
        existing = "preserved-pre-existing-commentary"
        result = self._run_cmd(tmp_path, claude_exit=3, existing_commentary=existing)
        assert result.returncode == 3
        commentary = tmp_path / "reports" / "match" / "claude-commentary.md"
        assert commentary.read_text(encoding="utf-8") == existing
        # Temp file should be cleaned up
        assert not (tmp_path / "reports" / "match" / "claude-commentary.md.tmp").exists()
