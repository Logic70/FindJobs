"""Core collection persistence: dataclass, upsert, run management.

Defines :class:`CollectedJob` as the canonical representation of a job
before it is persisted, and provides functions to upsert jobs, manage
collect runs, and batch-process collected jobs into the database.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from findjobs.job_types import format_job_type
from findjobs.locations import format_locations
from findjobs.models import CollectRun, Job, JobObservation, _utcnow


DOMAIN_TAGS = frozenset({"AI", "Security", "AI Security"})


@dataclass
class CollectedJob:
    """A job posting collected from a source, ready for persistence.

    Attributes correspond to the ``jobs`` table columns.
    ``matched_tags`` is stored as a JSON-serialized list in the DB.
    """

    external_id: str = ""
    title: str = ""
    url: str = ""
    description: str = ""
    salary_text: str = ""
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_currency: str = "CNY"
    salary_period: str = "monthly"
    salary_disclosed: bool = False
    location: str = ""
    job_type: str = ""
    published_at: Optional[datetime] = None
    matched_tags: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Upsert helpers
# ---------------------------------------------------------------------------


def _find_existing_job(
    session: Session, source_id: int, job: CollectedJob
) -> Job | None:
    """Locate an existing job row using the upsert uniqueness hierarchy.

    1. ``source_id`` + ``external_id`` (when external_id is non-empty).
    2. ``source_id`` + ``url``          (when url is non-empty).
    3. ``source_id`` + ``title`` + ``location``.
    """
    if job.external_id:
        existing: Job | None = (
            session.query(Job)
            .filter(
                Job.source_id == source_id,
                Job.external_id == job.external_id,
            )
            .first()
        )
        if existing is not None:
            return existing

    if job.url:
        existing = (
            session.query(Job)
            .filter(Job.source_id == source_id, Job.url == job.url)
            .first()
        )
        if existing is not None:
            return existing

    existing = (
        session.query(Job)
        .filter(
            Job.source_id == source_id,
            Job.title == job.title,
            Job.location == job.location,
        )
        .first()
    )
    return existing


def _job_identities(job: CollectedJob) -> list[tuple[str, str]]:
    """Return in-source identity keys used for same-batch deduplication."""
    keys: list[tuple[str, str]] = []
    if job.external_id:
        keys.append(("external_id", job.external_id))
    if job.url:
        keys.append(("url", job.url))
    if job.title or job.location:
        keys.append(("title_location", f"{job.title}\0{job.location}"))
    return keys


def normalize_collected_job(job: CollectedJob) -> CollectedJob:
    """Normalize fields that drive filtering, display, and fallback identity."""
    return replace(
        job,
        location=format_locations(job.location),
        job_type=format_job_type(job.job_type),
    )


def _deduplicate_collected_jobs(
    collected_jobs: list[CollectedJob],
) -> list[CollectedJob]:
    """Remove duplicate jobs within a single collector result.

    Official APIs sometimes repeat the same posting across pages or business
    units.  The database upsert already prevents duplicate rows; this keeps run
    statistics and observations aligned with unique job identities.
    """
    seen: set[tuple[str, str]] = set()
    unique_jobs: list[CollectedJob] = []
    for job in collected_jobs:
        keys = _job_identities(job)
        if keys and any(key in seen for key in keys):
            continue
        seen.update(keys)
        unique_jobs.append(job)
    return unique_jobs


def is_domain_relevant(job: CollectedJob) -> bool:
    """Return True when a job belongs to the supported AI/security scope."""
    return any(tag in DOMAIN_TAGS for tag in job.matched_tags)


def filter_domain_relevant_jobs(
    collected_jobs: list[CollectedJob],
) -> list[CollectedJob]:
    """Remove jobs outside the AI/security scope before persistence."""
    return [job for job in collected_jobs if is_domain_relevant(job)]


def upsert_job(
    session: Session,
    source_id: int,
    company_id: int,
    collect_run_id: int,
    job: CollectedJob,
) -> Job:
    """Insert or update a single :class:`CollectedJob` in the database.

    Returns the persisted :class:`Job` ORM instance.
    Always creates a :class:`JobObservation` to record the sighting.
    """
    job = normalize_collected_job(job)
    existing = _find_existing_job(session, source_id, job)
    now = _utcnow()

    if existing is not None:
        # Refresh mutable fields on repeat sighting.
        existing.last_seen_at = now
        existing.status = "active"
        existing.title = job.title
        existing.url = job.url or existing.url
        existing.description = job.description
        existing.salary_text = job.salary_text
        existing.salary_min = job.salary_min
        existing.salary_max = job.salary_max
        existing.salary_currency = job.salary_currency
        existing.salary_period = job.salary_period
        existing.salary_disclosed = job.salary_disclosed
        existing.location = job.location
        existing.job_type = job.job_type
        if job.published_at is not None:
            existing.published_at = job.published_at
        existing.matched_tags = json.dumps(job.matched_tags, ensure_ascii=False)
        db_job: Job = existing
    else:
        db_job = Job(
            source_id=source_id,
            company_id=company_id,
            external_id=job.external_id,
            title=job.title,
            url=job.url,
            description=job.description,
            salary_text=job.salary_text,
            salary_min=job.salary_min,
            salary_max=job.salary_max,
            salary_currency=job.salary_currency,
            salary_period=job.salary_period,
            salary_disclosed=job.salary_disclosed,
            location=job.location,
            job_type=job.job_type,
            published_at=job.published_at,
            first_seen_at=now,
            last_seen_at=now,
            status="active",
            matched_tags=json.dumps(job.matched_tags, ensure_ascii=False),
        )
        session.add(db_job)

    session.flush()

    observation = JobObservation(
        job_id=db_job.id,
        collect_run_id=collect_run_id,
        seen_at=now,
        raw_payload=None,
    )
    session.add(observation)

    return db_job


# ---------------------------------------------------------------------------
# CollectRun lifecycle
# ---------------------------------------------------------------------------


def create_collect_run(session: Session, source_id: int) -> CollectRun:
    """Create a new collect run record with status ``"running"``."""
    run = CollectRun(
        source_id=source_id,
        status="running",
        started_at=_utcnow(),
    )
    session.add(run)
    session.flush()
    return run


def complete_collect_run(
    session: Session,
    run: CollectRun,
    jobs_found: int,
    jobs_new: int,
    errors: str = "",
) -> None:
    """Mark a collect run as completed with summary counts."""
    run.status = "completed"
    run.finished_at = _utcnow()
    run.jobs_found = jobs_found
    run.jobs_new = jobs_new
    run.errors = errors


# ---------------------------------------------------------------------------
# Batch upsert
# ---------------------------------------------------------------------------


def collect_jobs(
    session: Session,
    source_id: int,
    company_id: int,
    collect_run_id: int,
    collected_jobs: list[CollectedJob],
) -> tuple[int, int]:
    """Upsert a batch of collected jobs and return ``(total, new)`` counts.

    Creates a :class:`JobObservation` for each job via :func:`upsert_job`.
    """
    normalized_jobs = [normalize_collected_job(job) for job in collected_jobs]
    unique_collected_jobs = filter_domain_relevant_jobs(
        _deduplicate_collected_jobs(normalized_jobs)
    )
    new_count = 0
    for cj in unique_collected_jobs:
        existing = _find_existing_job(session, source_id, cj)
        if existing is None:
            new_count += 1
        upsert_job(session, source_id, company_id, collect_run_id, cj)
    return len(unique_collected_jobs), new_count
