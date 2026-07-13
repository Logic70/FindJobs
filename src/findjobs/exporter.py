"""Query and export job facts from the database.

Exported data contains only facts stored in the database — no salary
estimation, no inferred fields.  Output formats: JSONL (default) and CSV.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from findjobs.models import Company, Job


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _days_ago(days: int) -> datetime:
    """Return a naive UTC datetime *days* days before now."""
    return _utcnow() - timedelta(days=days)


SUMMARY_COLUMNS = [
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
    "first_seen_at",
    "last_seen_at",
    "published_at",
]

FULL_COLUMNS = SUMMARY_COLUMNS + [
    "relevance_status",
    "classification_version",
    "classification_reasons",
    "description",
    "responsibilities",
    "requirements",
    "detail_completeness",
]

# Backward-compatible alias — points at the same list as summary.
EXPORT_COLUMNS = SUMMARY_COLUMNS


def _validate_detail_level(detail_level: str) -> None:
    """Raise ``ValueError`` when *detail_level* is not ``"summary"`` or ``"full"``."""
    if detail_level not in ("summary", "full"):
        raise ValueError(
            f"Invalid detail_level: {detail_level!r}. "
            "Must be 'summary' or 'full'."
        )


def query_jobs(
    session: Session,
    *,
    since_days: int | None = None,
    tag: str | None = None,
    company: str | None = None,
    status: str | None = None,
    salary_disclosed: bool | None = None,
    detail_level: str = "summary",
) -> list[dict[str, Any]]:
    """Query jobs from *session* with optional filters, returning flat dicts.

    Each dict contains only database-stored fields (see SUMMARY_COLUMNS /
    FULL_COLUMNS).  No salary estimation or inference is performed.

    Args:
        detail_level: ``"summary"`` (default, excludes long text and
            classification fields) or ``"full"`` (includes all database facts).
    """
    _validate_detail_level(detail_level)

    columns = [
        Job.id,
        Company.slug.label("company_slug"),
        Company.name.label("company_name"),
        Job.title,
        Job.location,
        Job.job_type,
        Job.status,
        Job.salary_text,
        Job.salary_min,
        Job.salary_max,
        Job.salary_currency,
        Job.salary_period,
        Job.salary_disclosed,
        Job.matched_tags,
        Job.url,
        Job.first_seen_at,
        Job.last_seen_at,
        Job.published_at,
    ]

    if detail_level == "full":
        columns.extend(
            [
                Job.relevance_status,
                Job.classification_version,
                Job.classification_reasons,
                Job.description,
                Job.responsibilities,
                Job.requirements,
                Job.detail_completeness,
            ]
        )

    query = (
        sa.select(*columns)
        .select_from(Job)
        .join(Company, Job.company_id == Company.id)
        .order_by(Job.last_seen_at.desc(), Job.id.desc())
    )

    if tag is not None:
        query = query.where(Job.matched_tags.like(f"%{tag}%"))

    if company is not None:
        query = query.where(Company.slug == company)

    if status is not None:
        query = query.where(Job.status == status)

    if salary_disclosed is not None:
        query = query.where(Job.salary_disclosed == salary_disclosed)

    if since_days is not None:
        cutoff = _days_ago(since_days)
        query = query.where(Job.last_seen_at >= cutoff)

    rows = session.execute(query).all()

    results: list[dict[str, Any]] = []
    for row in rows:
        d = row._asdict()
        # Serialise datetime objects as ISO strings
        for key in ("first_seen_at", "last_seen_at", "published_at"):
            val = d.get(key)
            if isinstance(val, datetime):
                d[key] = val.isoformat()
        # Parse matched_tags — stored as JSON-encoded list in the DB,
        # but also support legacy comma-separated text if JSON parsing fails.
        tags_raw = d.get("matched_tags")
        if tags_raw:
            try:
                parsed = json.loads(tags_raw)
                if isinstance(parsed, list):
                    d["matched_tags"] = [str(t) for t in parsed]
                else:
                    d["matched_tags"] = [t.strip() for t in tags_raw.split(",") if t.strip()]
            except (json.JSONDecodeError, TypeError):
                d["matched_tags"] = [t.strip() for t in tags_raw.split(",") if t.strip()]
        else:
            d["matched_tags"] = []

        # Parse classification_reasons (only present in full mode)
        if detail_level == "full":
            reasons_raw = d.get("classification_reasons")
            if reasons_raw:
                try:
                    parsed = json.loads(reasons_raw)
                    if isinstance(parsed, list):
                        d["classification_reasons"] = [str(r) for r in parsed]
                    else:
                        d["classification_reasons"] = [
                            r.strip() for r in reasons_raw.split(",") if r.strip()
                        ]
                except (json.JSONDecodeError, TypeError):
                    d["classification_reasons"] = [
                        r.strip() for r in reasons_raw.split(",") if r.strip()
                    ]
            else:
                d["classification_reasons"] = []

        results.append(d)

    return results


def export_jsonl(jobs: list[dict[str, Any]], output: io.TextIOBase) -> None:
    """Write *jobs* as JSONL (one object per line) to *output*."""
    for job in jobs:
        output.write(json.dumps(job, ensure_ascii=False))
        output.write("\n")


def export_csv(
    jobs: list[dict[str, Any]],
    output: io.TextIOBase,
    *,
    detail_level: str = "summary",
) -> None:
    """Write *jobs* as CSV to *output* with stable columns.

    Args:
        detail_level: ``"summary"`` (default) or ``"full"`` — controls which
            column set is written as the CSV header.
    """
    _validate_detail_level(detail_level)
    columns = FULL_COLUMNS if detail_level == "full" else SUMMARY_COLUMNS
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for job in jobs:
        row = dict(job)
        # Flatten matched_tags back to a comma-separated string for CSV
        tags = row.get("matched_tags")
        if isinstance(tags, list):
            row["matched_tags"] = ", ".join(tags)
        # Flatten classification_reasons for full CSV
        if detail_level == "full":
            reasons = row.get("classification_reasons")
            if isinstance(reasons, list):
                row["classification_reasons"] = ", ".join(reasons)
        writer.writerow(row)


def do_export(
    session: Session,
    *,
    fmt: str = "jsonl",
    output: io.TextIOBase | None = None,
    since_days: int | None = None,
    tag: str | None = None,
    company: str | None = None,
    status: str | None = None,
    salary_disclosed: bool | None = None,
    detail_level: str = "summary",
) -> str | None:
    """Query jobs and write them in the requested format.

    Args:
        session: Open database session.
        fmt: ``"jsonl"`` or ``"csv"``.
        output: Text stream to write to.  If ``None``, returns the output
            as a string.
        since_days: Only jobs seen within this many days.
        tag: Filter by matched tag substring.
        company: Filter by company slug.
        status: Filter by job status.
        salary_disclosed: Filter by salary disclosure.
        detail_level: ``"summary"`` (default) or ``"full"``.

    Returns:
        The output string if *output* is None, otherwise None.
    """
    jobs = query_jobs(
        session,
        since_days=since_days,
        tag=tag,
        company=company,
        status=status,
        salary_disclosed=salary_disclosed,
        detail_level=detail_level,
    )

    if output is None:
        buf = io.StringIO()
    else:
        buf = output

    if fmt == "csv":
        export_csv(jobs, buf, detail_level=detail_level)
    else:
        export_jsonl(jobs, buf)

    if output is None:
        return buf.getvalue()
    return None
