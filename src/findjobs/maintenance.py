"""Maintenance helpers for keeping persisted job facts within project scope."""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from findjobs.classify import classify_job
from findjobs.collection import DOMAIN_TAGS
from findjobs.job_types import format_job_type
from findjobs.locations import format_locations
from findjobs.models import Job, JobObservation, UserMark


@dataclass(frozen=True)
class RelevancePruneResult:
    scanned: int
    updated: int
    deleted: int


def _is_relevant(tags: list[str]) -> bool:
    return any(tag in DOMAIN_TAGS for tag in tags)


def reclassify_and_prune_irrelevant_jobs(
    session: Session,
) -> RelevancePruneResult:
    """Recompute tags and remove jobs outside the AI/security scope."""
    scanned = 0
    updated = 0
    deleted = 0

    for job in list(session.query(Job).all()):
        scanned += 1
        tags = classify_job(job.title or "", job.description or "", job.job_type or "")
        if not _is_relevant(tags):
            session.query(UserMark).filter(UserMark.job_id == job.id).delete()
            session.query(JobObservation).filter(
                JobObservation.job_id == job.id
            ).delete()
            session.delete(job)
            deleted += 1
            continue

        encoded = json.dumps(tags, ensure_ascii=False)
        if job.matched_tags != encoded:
            job.matched_tags = encoded
            updated += 1
        normalized_location = format_locations(job.location or "")
        normalized_job_type = format_job_type(job.job_type or "")
        if job.location != normalized_location:
            job.location = normalized_location
            updated += 1
        if job.job_type != normalized_job_type:
            job.job_type = normalized_job_type
            updated += 1

    session.flush()
    return RelevancePruneResult(scanned=scanned, updated=updated, deleted=deleted)
