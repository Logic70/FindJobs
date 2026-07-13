"""Deterministic recommendation reports from a full exported fact file.

This module implements the weekly recommendation pipeline: load a full-export
JSONL file, load a recommendation profile, score deterministically using
``recommend_jobs``, and render both Markdown and JSON reports.

No database queries, no AI calls, no salary estimation.  Summary-only exports
(reports/weekly/jobs.jsonl) are rejected — only full-format rows (containing
``responsibilities``, ``requirements``, ``detail_completeness``, etc.) pass
validation.

Outputs are written as a rollback-safe pair: both complete contents are staged
in each destination's own parent directory, and pre-existing content is backed
up to same-directory temp files before replacement.  If either replacement
fails both pre-existing destinations are restored atomically via
``Path.replace`` from those backups.  All temporary files and newly-created
empty ancestor directories are cleaned up.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from findjobs.analysis import load_jsonl
from findjobs.recommendation import recommend_jobs
from findjobs.recommendation_output import render_to_markdown, serialize_to_json
from findjobs.recommendation_profile import load_recommendation_profile

# ---------------------------------------------------------------------------
# Full-export required field set
# ---------------------------------------------------------------------------

_REQUIRED_FULL_FIELDS = frozenset({
    "id",
    "company_slug",
    "company_name",
    "title",
    "location",
    "job_type",
    "status",
    "salary_text",
    "salary_min",
    "salary_max",
    "salary_currency",
    "salary_period",
    "salary_disclosed",
    "matched_tags",
    "url",
    "responsibilities",
    "requirements",
    "detail_completeness",
    "relevance_status",
})

# ---------------------------------------------------------------------------
# Frozen result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecommendationOutput:
    """Paths and counts produced by ``run_exported_recommendations``."""

    jobs_path: Path
    profile_path: Path
    markdown_output: Path
    json_output: Path
    total_scanned: int
    total_eligible: int
    returned_count: int


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_full_rows(rows: list[dict[str, Any]]) -> None:
    """Reject rows that are missing full-export required fields.

    Args:
        rows: List of job row dicts to validate.

    Raises:
        ValueError: If any row is missing required full-export fields.
    """
    for i, row in enumerate(rows):
        missing = _REQUIRED_FULL_FIELDS - set(row.keys())
        if missing:
            row_id = row.get("id", "?")
            raise ValueError(
                f"Row {i} (id={row_id}) is missing required full-export fields: "
                f"{sorted(missing)}. "
                "Use detail_level='full' when exporting jobs."
            )


# ---------------------------------------------------------------------------
# Helpers for temp-file generation and directory tracking
# ---------------------------------------------------------------------------


def _create_exclusive_temp(parent: Path, tag: str) -> Path:
    """Atomically create an empty temp file inside *parent*.

    Uses ``tempfile.mkstemp`` (O_EXCL) so the returned path is guaranteed
    not to have existed before the call.  The caller owns the returned
    path and is responsible for its cleanup.
    """
    fd, path = tempfile.mkstemp(
        dir=str(parent),
        prefix=f".rec_{tag}_",
        suffix="",
    )
    os.close(fd)
    return Path(path)


def _collect_existing_ancestors(roots: set[Path]) -> set[Path]:
    """Return every existing ancestor (and root) of any path in *roots*.

    Traverses from the filesystem root inward so the first non-existing
    ancestor acts as a sentinel — everything closer to the root than it
    is guaranteed not to exist.
    """
    existing: set[Path] = set()
    for root in roots:
        for parent in reversed(list(root.parents)):
            if parent.exists():
                existing.add(parent)
            else:
                break
        if root.exists():
            existing.add(root)
    return existing


def _new_ancestors_since(
    roots: set[Path], pre_existing: set[Path]
) -> list[Path]:
    """Return ancestors of *roots* (including roots) created since snapshot.

    Returns a list sorted deepest-first so callers can ``rmdir`` in order.
    """
    created: set[Path] = set()
    for root in roots:
        if root not in pre_existing:
            created.add(root)
        for parent in root.parents:
            if parent in pre_existing:
                break
            created.add(parent)
    return sorted(created, key=lambda p: len(p.parts), reverse=True)


def _try_rmdir(path: Path) -> None:
    """Remove *path* if it exists, is a directory, and is empty.

    Never raises.
    """
    try:
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Rollback-safe paired write
# ---------------------------------------------------------------------------


def _write_paired_outputs(
    markdown_output: Path,
    json_output: Path,
    markdown_content: str,
    json_content: str,
) -> None:
    """Write Markdown then JSON with filesystem-safe rollback.

    Design
    ------
    Each destination is staged in its own parent directory (avoiding EXDEV
    across filesystems).  Pre-existing content is backed up to same-directory
    temp files so rollback can use atomic ``Path.replace`` (rename syscall)
    rather than a truncation-prone ``write_bytes``.

    Phases
    ------
    A. Create parent directories and snapshot new ancestors.
    B. Back up pre-existing destination files to same-directory temp files.
    C. Stage new contents to same-directory temp files.
    D. Replace markdown destination (first replacement).
    E. Replace json destination (second replacement).
    F. On any failure in D–E, restore both destinations from backups.

    After success or complete rollback all temp/backup files are removed
    and any newly-created empty ancestor directories are cleaned up
    deepest-first.  Backups whose restoration failed are *not* deleted so
    manual recovery remains possible.
    """
    # ---- Reject identical destinations. ----
    if markdown_output.resolve() == json_output.resolve():
        raise ValueError(
            "markdown_output and json_output must resolve to different paths: "
            f"{markdown_output} == {json_output}"
        )

    # ---- Snapshot pre-existing content (no filesystem modification). ----
    md_pre_existed = markdown_output.exists()
    md_pre_bytes = markdown_output.read_bytes() if md_pre_existed else None
    json_pre_existed = json_output.exists()
    json_pre_bytes = json_output.read_bytes() if json_pre_existed else None

    # ---- Phase A: Snapshot ancestor state, then create parent dirs. ----
    md_parent = markdown_output.parent
    json_parent = json_output.parent
    roots = {md_parent, json_parent}
    pre_existing_dirs = _collect_existing_ancestors(roots)

    temp_files: set[Path] = set()
    new_ancestors: list[Path] = []
    # Backups whose ``Path.replace`` restore failed — must not be deleted.
    keep_backups: set[Path] = set()

    try:
        # Create both parent trees inside the protected lifecycle so that
        # if the second ``mkdir`` fails the new ancestors from the first
        # are still cleaned up.
        md_parent.mkdir(parents=True, exist_ok=True)
        new_ancestors = _new_ancestors_since(roots, pre_existing_dirs)

        json_parent.mkdir(parents=True, exist_ok=True)
        new_ancestors = _new_ancestors_since(roots, pre_existing_dirs)

        # ---- Phase B: Back up pre-existing files (exclusive creation). ----
        bak_md: Path | None = None
        bak_json: Path | None = None
        if md_pre_existed:
            bak_md = _create_exclusive_temp(md_parent, "bak")
            temp_files.add(bak_md)
            bak_md.write_bytes(md_pre_bytes)
        if json_pre_existed:
            bak_json = _create_exclusive_temp(json_parent, "bak")
            temp_files.add(bak_json)
            bak_json.write_bytes(json_pre_bytes)

        # ---- Phase C: Stage new contents (exclusive creation). ----
        stage_md = _create_exclusive_temp(md_parent, "stage")
        temp_files.add(stage_md)
        stage_md.write_text(markdown_content, encoding="utf-8")

        stage_json = _create_exclusive_temp(json_parent, "stage")
        temp_files.add(stage_json)
        stage_json.write_text(json_content, encoding="utf-8")

        # ---- Phase D: Replace markdown (first destination). ----
        stage_md.replace(markdown_output)

        # ---- Phase E: Replace json (second destination). ----
        try:
            stage_json.replace(json_output)
        except Exception as exc:
            # ---- Phase F: Rollback — attempt BOTH restores. ----
            restore_errors: list[str] = []

            # First restore (markdown).
            try:
                _atomic_restore(
                    markdown_output,
                    bak_md if md_pre_existed else None,
                )
            except Exception as e:
                restore_errors.append(
                    f"failed to restore {markdown_output}: {e}"
                )
                if md_pre_existed and bak_md is not None:
                    keep_backups.add(bak_md)

            # Second restore (json) — attempted even if first failed.
            try:
                _atomic_restore(
                    json_output,
                    bak_json if json_pre_existed else None,
                )
            except Exception as e:
                restore_errors.append(
                    f"failed to restore {json_output}: {e}"
                )
                if json_pre_existed and bak_json is not None:
                    keep_backups.add(bak_json)

            if restore_errors:
                raise RuntimeError(
                    "Rollback incomplete: " + "; ".join(restore_errors)
                ) from exc
            raise  # re-raise the original replacement failure
    finally:
        # Remove temp/backup files except those whose restore failed.
        for p in temp_files:
            if p not in keep_backups:
                p.unlink(missing_ok=True)
        # Remove newly-created empty ancestor directories deepest-first.
        for p in new_ancestors:
            _try_rmdir(p)


def _atomic_restore(dest: Path, backup: Path | None) -> None:
    """Restore *dest* from *backup* using ``Path.replace``, or remove it."""
    if backup is not None:
        backup.replace(dest)
    else:
        try:
            dest.unlink()
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_exported_recommendations(
    jobs_path: Path,
    profile_path: Path,
    markdown_output: Path,
    json_output: Path,
    limit: int = 50,
) -> RecommendationOutput:
    """Load a full-export JSONL, score deterministically, render both outputs.

    The pipeline is:
        1.  Load rows from *jobs_path* (JSONL, one object per line).
        2.  Validate that every row is a full-format row (rejects summary
            exports with a clear ``ValueError``).
        3.  Load the recommendation profile from *profile_path*.
        4.  Run the deterministic ``recommend_jobs`` engine (no AI, no DB).
        5.  Render Markdown and JSON outputs in memory.
        6.  Write both outputs with rollback safety (if the second write
            fails, the first is restored).

    Args:
        jobs_path:
            Path to a full-export JSONL file (produced with
            ``detail_level="full"``).
        profile_path:
            Path to a profile file (``.json`` or ``.md``).
        markdown_output:
            Destination path for the Markdown recommendation report.
        json_output:
            Destination path for the JSON recommendation report.
        limit:
            Maximum number of recommendations to return (1..1000, passed
            through to ``recommend_jobs``).

    Returns:
        A ``RecommendationOutput`` with resolved paths and counts.

    Raises:
        ValueError: If *markdown_output* and *json_output* resolve to the
            same path.  If any row is missing required full-format fields.
        FileNotFoundError: If *jobs_path* or *profile_path* does not exist.
        OSError: On write failure (the pair is fully rolled back).

    Side effects:
        Creates *markdown_output* and *json_output* as a rollback-safe pair.
        Never accesses the database.  Never calls an AI service.  Never
        estimates salary.
    """
    if not jobs_path.exists():
        raise FileNotFoundError(f"Jobs file not found: {jobs_path}")
    if not profile_path.exists():
        raise FileNotFoundError(f"Profile file not found: {profile_path}")

    rows = load_jsonl(jobs_path)
    validate_full_rows(rows)
    profile = load_recommendation_profile(profile_path)
    result = recommend_jobs(rows, profile, limit=limit)

    markdown_content = render_to_markdown(result)
    json_content = serialize_to_json(result)

    _write_paired_outputs(markdown_output, json_output, markdown_content, json_content)

    return RecommendationOutput(
        jobs_path=jobs_path,
        profile_path=profile_path,
        markdown_output=markdown_output,
        json_output=json_output,
        total_scanned=result.scanned,
        total_eligible=result.eligible,
        returned_count=len(result.recommendations),
    )
