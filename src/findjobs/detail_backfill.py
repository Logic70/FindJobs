"""Non-destructive backfill for normalized job details.

Provides :class:`DetailBackfillResult` and
:func:`backfill_job_details` to walk stored ``Job`` rows and apply
canonical responsibility/requirement normalisation without overwriting
explicitly-provided values.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from findjobs.job_details import normalize_job_details
from findjobs.models import Job


@dataclass(frozen=True)
class DetailBackfillResult:
    """Outcome of a job-detail backfill pass.

    Attributes:
        scanned:        Total number of jobs examined.
        updated:        Number of jobs whose ``responsibilities``,
                        ``requirements``, or ``detail_completeness`` would
                        change (row-level, not field-level).
        applied:        True when changes were flushed to the database.
        deleted:        Always zero — this operation never removes rows.
        full:           Projected count of jobs with both responsibilities and
                        requirements.
        responsibilities_only: Projected count of jobs with only
                        responsibilities.
        requirements_only: Projected count of jobs with only requirements.
        combined_only:  Projected count of jobs with a description but no
                        recognised section split.
        missing:        Projected count of jobs with all three detail fields
                        empty.
    """

    scanned: int = 0
    updated: int = 0
    applied: bool = False
    deleted: int = 0
    full: int = 0
    responsibilities_only: int = 0
    requirements_only: int = 0
    combined_only: int = 0
    missing: int = 0


def backfill_job_details(
    session: Session,
    apply: bool = False,
) -> DetailBackfillResult:
    """Backfill normalised responsibilities and requirements for stored jobs.

    Walks all ``Job`` rows in primary-key order and calls
    :func:`~findjobs.job_details.normalize_job_details` with each job's
    existing ``description``, ``responsibilities``, and ``requirements``.

    **Override rule**: existing non-empty ``responsibilities`` and
    ``requirements`` are always kept.  Only missing (empty) fields may be
    inferred from recognised section headings in ``description``.  The
    ``description`` field itself is never modified.

    **Dry-run mode** (the default, ``apply=False``):
    Computes projected counts without mutating ORM objects or writing to the
    database.  No ``flush()`` is called.

    **Apply mode** (``apply=True``):
    Mutates only the three detail fields (``responsibilities``,
    ``requirements``, ``detail_completeness``) and calls ``session.flush()``
    once before returning.  The caller is responsible for ``commit()`` /
    ``rollback()``.

    Returns:
        A :class:`DetailBackfillResult` with scanned, updated, applied,
        deleted=0, and the projected distribution of the five
        ``detail_completeness`` values.
    """
    scanned = 0
    updated = 0

    c_full = 0
    c_resp_only = 0
    c_req_only = 0
    c_combined = 0
    c_missing = 0

    for job in session.query(Job).order_by(Job.id).all():
        scanned += 1

        result = normalize_job_details(
            description=job.description or "",
            responsibilities=job.responsibilities or "",
            requirements=job.requirements or "",
        )

        # -- Projected completeness distribution ---------------------------
        completeness = result.detail_completeness
        if completeness == "full":
            c_full += 1
        elif completeness == "responsibilities_only":
            c_resp_only += 1
        elif completeness == "requirements_only":
            c_req_only += 1
        elif completeness == "combined_only":
            c_combined += 1
        else:
            c_missing += 1

        # -- Detect row-level change ---------------------------------------
        resp_changed = result.responsibilities != (job.responsibilities or "")
        req_changed = result.requirements != (job.requirements or "")
        comp_changed = result.detail_completeness != (job.detail_completeness or "")

        if resp_changed or req_changed or comp_changed:
            updated += 1
            if apply:
                job.responsibilities = result.responsibilities
                job.requirements = result.requirements
                job.detail_completeness = result.detail_completeness

    if apply:
        session.flush()

    return DetailBackfillResult(
        scanned=scanned,
        updated=updated,
        applied=apply,
        deleted=0,
        full=c_full,
        responsibilities_only=c_resp_only,
        requirements_only=c_req_only,
        combined_only=c_combined,
        missing=c_missing,
    )
