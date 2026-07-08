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


EXPORT_COLUMNS = [
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


def query_jobs(
    session: Session,
    *,
    since_days: int | None = None,
    tag: str | None = None,
    company: str | None = None,
    status: str | None = None,
    salary_disclosed: bool | None = None,
) -> list[dict[str, Any]]:
    """Query jobs from *session* with optional filters, returning flat dicts.

    Each dict contains only database-stored fields (see EXPORT_COLUMNS).
    No salary estimation or inference is performed.
    """
    query = (
        sa.select(
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
        )
        .select_from(Job)
        .join(Company, Job.company_id == Company.id)
        .order_by(Job.last_seen_at.desc())
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
        results.append(d)

    return results


def export_jsonl(jobs: list[dict[str, Any]], output: io.TextIOBase) -> None:
    """Write *jobs* as JSONL (one object per line) to *output*."""
    for job in jobs:
        output.write(json.dumps(job, ensure_ascii=False))
        output.write("\n")


def export_csv(jobs: list[dict[str, Any]], output: io.TextIOBase) -> None:
    """Write *jobs* as CSV to *output* with stable columns."""
    writer = csv.DictWriter(output, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for job in jobs:
        # Flatten matched_tags back to a comma-separated string for CSV
        row = dict(job)
        tags = row.get("matched_tags")
        if isinstance(tags, list):
            row["matched_tags"] = ", ".join(tags)
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
    )

    if output is None:
        buf = io.StringIO()
    else:
        buf = output

    if fmt == "csv":
        export_csv(jobs, buf)
    else:
        export_jsonl(jobs, buf)

    if output is None:
        return buf.getvalue()
    return None
