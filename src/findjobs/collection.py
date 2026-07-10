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

from findjobs.classify import CLASSIFICATION_VERSION, classify_job_detailed
from findjobs.job_types import format_job_type
from findjobs.locations import format_locations
from findjobs.models import CollectRun, Job, JobObservation, _utcnow


DOMAIN_TAGS = frozenset({"AI", "Security", "AI Security"})


@dataclass
class CollectedJob:
    """A job posting collected from a source, ready for persistence.

    Attributes correspond to the ``jobs`` table columns.
    ``matched_tags`` is stored as a JSON-serialized list in the DB.
    ``classification_reasons`` is stored as a JSON-serialized list.
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
    relevance_status: str = "target"
    classification_version: str = ""
    classification_reasons: list[str] = field(default_factory=list)


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
        existing.missing_run_count = 0
        existing.relevance_status = job.relevance_status
        existing.classification_version = job.classification_version
        existing.classification_reasons = json.dumps(
            job.classification_reasons, ensure_ascii=False
        )
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
            relevance_status=job.relevance_status,
            matched_tags=json.dumps(job.matched_tags, ensure_ascii=False),
            classification_version=job.classification_version,
            classification_reasons=json.dumps(
                job.classification_reasons, ensure_ascii=False
            ),
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


def fail_collect_run(session: Session, run: CollectRun, errors: str) -> None:
    """Mark an existing collect run as failed without creating a new row.

    Sets status, finished_at, and the complete error text on the given run.
    """
    run.status = "failed"
    run.finished_at = _utcnow()
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

    # Recompute detailed classification centrally on canonical fields so
    # adapters cannot bypass the contract (requirement 10).
    for job in normalized_jobs:
        detailed = classify_job_detailed(job.title, job.description, job.job_type)
        job.matched_tags = list(detailed.tags)
        job.relevance_status = detailed.relevance_status
        job.classification_version = detailed.version
        job.classification_reasons = list(detailed.reasons)

    # Excluded jobs are filtered out; review jobs persist alongside target.
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


# ---------------------------------------------------------------------------
# Job lifecycle reconciliation
# ---------------------------------------------------------------------------


@dataclass
class ReconcileResult:
    """Result of a lifecycle reconciliation after a successful collect run.

    Attributes:
        action: One of ``"skipped_partial"``, ``"skipped_zero_target"``,
            ``"skipped_mass_drop"``, or ``"reconciled"``.
        total_target: Number of target-relevance jobs tracked for this source.
        seen_target: How many of those were observed in this run.
        made_missing: Active jobs that became missing.
        made_archived: Missing jobs that became archived.
        kept_archived: Archived jobs that remained archived (no counter bump).
        reason: Human-readable explanation when action was skipped.
    """

    action: str = ""
    total_target: int = 0
    seen_target: int = 0
    made_missing: int = 0
    made_archived: int = 0
    kept_archived: int = 0
    reason: str = ""


def reconcile_jobs_after_collect(
    session: Session,
    source_id: int,
    collect_run_id: int,
    is_complete: bool,
) -> ReconcileResult:
    """Reconcile job lifecycle after a successful collect run.

    For sources with ``is_complete=True``, transitions unseen target jobs
    through *active → missing → archived*.  Seen target jobs are kept active
    with ``missing_run_count=0``.  Safety guards prevent reconciliation when
    the run observed zero target jobs or would drop more than 50 % of 10+
    tracked target jobs.

    For partial sources (``is_complete=False``) all state transitions are
    skipped and the result reports ``action="skipped_partial"``.
    """
    if not is_complete:
        return ReconcileResult(
            action="skipped_partial",
            reason="Source is configured as partial collection scope",
        )

    # Derive seen job ids from JobObservation rows for this run.
    seen_rows = (
        session.query(JobObservation.job_id)
        .filter(JobObservation.collect_run_id == collect_run_id)
        .all()
    )
    seen_job_ids = {row[0] for row in seen_rows}

    # Reconcile only target-relevance jobs.
    target_jobs: list[Job] = (
        session.query(Job)
        .filter(Job.source_id == source_id, Job.relevance_status == "target")
        .all()
    )

    if not target_jobs:
        return ReconcileResult(action="reconciled", total_target=0)

    # -- Partition: only active/missing jobs can transition state ---------------
    active_missing_jobs = [j for j in target_jobs if j.status in {"active", "missing"}]

    total_active_missing = len(active_missing_jobs)
    active_missing_ids = {j.id for j in active_missing_jobs}
    seen_active_missing_ids = seen_job_ids & active_missing_ids
    unseen_active_missing_ids = active_missing_ids - seen_job_ids

    # -- Safety guard: zero observed active/missing target jobs -----------------
    if total_active_missing > 0 and len(seen_active_missing_ids) == 0:
        return ReconcileResult(
            action="skipped_zero_target",
            total_target=total_active_missing,
            reason="Successful collect run observed zero active/missing target jobs",
        )

    # -- Safety guard: mass drop (>50 % of >=10 active/missing jobs) ------------
    if (
        total_active_missing >= 10
        and len(unseen_active_missing_ids) / total_active_missing > 0.5
    ):
        return ReconcileResult(
            action="skipped_mass_drop",
            total_target=total_active_missing,
            seen_target=len(seen_active_missing_ids),
            reason=(
                f"{len(unseen_active_missing_ids)}/{total_active_missing} "
                f"active/missing target jobs would become unseen (>50 %)"
            ),
        )

    # -- Reconcile --------------------------------------------------------------
    made_missing = 0
    made_archived = 0
    kept_archived = 0

    for job in target_jobs:
        if job.id in seen_job_ids:
            # Upsert already refreshed the job; ensure consistent state.
            job.status = "active"
            job.missing_run_count = 0
        else:
            if job.status == "active":
                job.status = "missing"
                job.missing_run_count = 1
                made_missing += 1
            elif job.status == "missing":
                job.missing_run_count += 1
                if job.missing_run_count >= 2:
                    job.status = "archived"
                    made_archived += 1
            elif job.status == "archived":
                # Archived jobs stay archived without unbounded counter growth.
                kept_archived += 1

    session.flush()

    return ReconcileResult(
        action="reconciled",
        total_target=total_active_missing,
        seen_target=len(seen_active_missing_ids),
        made_missing=made_missing,
        made_archived=made_archived,
        kept_archived=kept_archived,
    )
