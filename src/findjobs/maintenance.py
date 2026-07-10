"""Maintenance helpers for keeping persisted job facts within project scope.

Provides non-destructive reclassification that updates relevance_status and
matched_tags instead of deleting jobs.  The legacy
:func:`reclassify_and_prune_irrelevant_jobs` function is kept for backward
compatibility and guarantees that zero rows are deleted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from findjobs.classify import (
    CLASSIFICATION_VERSION,
    classify_job_detailed,
)
from findjobs.collection import DOMAIN_TAGS
from findjobs.job_types import format_job_type
from findjobs.locations import format_locations
from findjobs.models import Job


@dataclass(frozen=True)
class RelevancePruneResult:
    """Legacy result type — kept for backward compatibility only."""

    scanned: int
    updated: int
    deleted: int


@dataclass(frozen=True)
class ReclassificationResult:
    """Outcome of a reclassification pass.

    Attributes:
        scanned:        Total number of jobs examined.
        updated:        Number of individual field-level mutations
                        (relevance_status, matched_tags, location, job_type
                        each count separately).
        excluded:       Jobs whose relevance_status changed from ``target`` or
                        ``review`` to ``excluded``.
        restored:       Jobs whose relevance_status changed from ``excluded``
                        back to ``target`` or ``review``.
        moved_to_review: Jobs whose relevance_status changed from ``target``
                        or ``excluded`` to ``review``.
        normalized:     Number of location/job_type field normalizations applied.
        deleted:        Always zero — this operation never removes rows.
        applied:        True when changes were flushed to the database.
    """

    scanned: int
    updated: int
    excluded: int
    restored: int
    moved_to_review: int
    normalized: int
    deleted: int = 0
    applied: bool = False


def _is_relevant(tags: list[str]) -> bool:
    return any(tag in DOMAIN_TAGS for tag in tags)


def reclassify_jobs(
    session: Session,
    apply: bool = False,
) -> ReclassificationResult:
    """Recompute tags and relevance_status for every stored job.

    In *preview* mode (the default) the function computes the exact same
    counts it would return in *apply* mode, but never mutates ORM objects
    or writes to the database.

    In *apply* mode every job has its ``relevance_status``,
    ``matched_tags``, ``location``, and ``job_type`` updated to reflect
    current classifier rules and normalisation logic, and
    ``session.flush()`` is called before returning.

    .. note::

       This function **never** deletes jobs, observations, or user marks.
       The returned ``deleted`` count is always zero.
    """
    scanned = 0
    field_updates = 0
    excluded = 0
    restored = 0
    moved_to_review = 0
    normalized = 0

    for job in session.query(Job).all():
        scanned += 1
        detailed = classify_job_detailed(
            job.title or "",
            job.description or "",
            job.job_type or "",
        )
        target_status = detailed.relevance_status
        encoded_tags = json.dumps(list(detailed.tags), ensure_ascii=False)
        encoded_reasons = json.dumps(list(detailed.reasons), ensure_ascii=False)

        old_status = job.relevance_status or "target"
        old_tags = job.matched_tags or ""
        old_version = job.classification_version or ""
        old_reasons = job.classification_reasons or ""

        # Normalise location / job type (always compute, only mutate on apply).
        norm_loc = format_locations(job.location or "")
        norm_type = format_job_type(job.job_type or "")
        loc_changed = norm_loc != (job.location or "")
        type_changed = norm_type != (job.job_type or "")

        # --- Detect field-level changes ------------------------------------
        status_changed = target_status != old_status
        tags_changed = encoded_tags != old_tags
        version_changed = detailed.version != old_version
        reasons_changed = encoded_reasons != old_reasons

        if status_changed:
            field_updates += 1
        if tags_changed:
            field_updates += 1
        if version_changed:
            field_updates += 1
        if reasons_changed:
            field_updates += 1
        if loc_changed:
            field_updates += 1
            normalized += 1
        if type_changed:
            field_updates += 1
            normalized += 1

        # --- Count status transitions ---------------------------------------
        if status_changed and target_status == "excluded":
            excluded += 1
        elif status_changed and target_status == "review":
            moved_to_review += 1
        elif status_changed and target_status in ("target",):
            restored += 1

        # --- Mutate when applying -----------------------------------------
        if apply:
            job.relevance_status = target_status
            job.matched_tags = encoded_tags
            job.classification_version = detailed.version
            job.classification_reasons = encoded_reasons
            if loc_changed:
                job.location = norm_loc
            if type_changed:
                job.job_type = norm_type

    if apply:
        session.flush()

    return ReclassificationResult(
        scanned=scanned,
        updated=field_updates,
        excluded=excluded,
        restored=restored,
        moved_to_review=moved_to_review,
        normalized=normalized,
        deleted=0,
        applied=apply,
    )


def reclassify_and_prune_irrelevant_jobs(
    session: Session,
) -> RelevancePruneResult:
    """Backward-compatible wrapper around :func:`reclassify_jobs`.

    .. warning::

       Despite the name this function **never** deletes rows.  It calls
       :func:`reclassify_jobs` with ``apply=True`` and returns a legacy
       :class:`RelevancePruneResult` where ``deleted`` is always ``0``.
    """
    result = reclassify_jobs(session, apply=True)
    return RelevancePruneResult(
        scanned=result.scanned,
        updated=result.updated,
        deleted=0,
    )
