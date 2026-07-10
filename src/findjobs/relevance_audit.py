"""Read-only, deterministic audit of projected job relevance."""

from __future__ import annotations

import json
import random
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy.orm import Session, joinedload

from findjobs.classify import CLASSIFICATION_VERSION, classify_job_detailed


_ALGORITHM_CN = "算法"

_HARD_FUNCTIONAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(产品经理|产品负责人|产品运营|产品战略|策略产品|"
        r"\bproduct\s+(manager|management|owner)\b)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(经营分析|业务分析|商业分析|运营分析|"
        r"\b(business|operations?)\s+analyst\b)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(销售|营销|商务拓展|\bsales\b|\bmarketing\b|"
        r"\bbusiness\s+development\b)",
        re.IGNORECASE,
    ),
    re.compile(r"(项目经理|项目管理|\bproject\s+(manager|management)\b)", re.IGNORECASE),
    re.compile(r"(法务|律师|法律方向|\blegal\b|\blawyer\b|\bcounsel\b)", re.IGNORECASE),
    re.compile(r"(招聘|人才招聘|talent\s+acquisition|recruiter|recruiting)", re.IGNORECASE),
    re.compile(r"(设计师|交互设计|视觉设计|\bdesigner\b|\bui\s+design|\bux\s+design)", re.IGNORECASE),
)

_AMBIGUOUS_FUNCTIONAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(策划|创意规划|战略规划|creative\s+planning|strategic\s+planning)", re.IGNORECASE),
    re.compile(r"(运营|\boperations?\b)", re.IGNORECASE),
    re.compile(r"(审计|\baudit(or)?\b)", re.IGNORECASE),
    re.compile(r"(测试|\btesting\b|\bqa\b)", re.IGNORECASE),
)

_ENGINEERING_OVERRIDE = re.compile(
    r"(工程师|研发|开发|架构师|架构|后端|前端|客户端|服务端|全栈|测试开发|"
    r"\bengineer\b|\bdeveloper\b|\barchitect\b|\bbackend\b|\bfrontend\b|"
    r"\bfull.stack\b|\bFDE\b)",
    re.IGNORECASE,
)


def _matches_functional(title: str) -> bool:
    """Return whether a projected target still looks like a functional role."""
    if any(pattern.search(title) for pattern in _HARD_FUNCTIONAL_PATTERNS):
        return True
    return not _ENGINEERING_OVERRIDE.search(title) and any(
        pattern.search(title) for pattern in _AMBIGUOUS_FUNCTIONAL_PATTERNS
    )


def _is_algorithm_residual(title: str, job_type: str) -> bool:
    return _ALGORITHM_CN in (title or "") or _ALGORITHM_CN in (job_type or "")


def _identity_key(job: Any) -> tuple[int, str, str] | None:
    if job.external_id:
        return job.source_id, "external_id", job.external_id
    if job.url:
        return job.source_id, "url", job.url
    return None


def _seeded_sample(
    rows: list[dict[str, Any]], sample_size: int, seed: int
) -> list[dict[str, Any]]:
    if sample_size <= 0 or not rows:
        return []
    indexes = random.Random(seed).sample(
        range(len(rows)), min(sample_size, len(rows))
    )
    return [rows[index] for index in sorted(indexes)]


def _decode_list(value: str | None) -> list[str]:
    try:
        decoded = json.loads(value or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return decoded if isinstance(decoded, list) else []


@dataclass(frozen=True)
class AuditReport:
    scanned: int = 0
    projected_target: int = 0
    projected_review: int = 0
    projected_excluded: int = 0
    projected_tags: dict[str, dict[str, int]] = field(default_factory=dict)
    drift_count: int = 0
    algorithm_residual_count: int = 0
    suspicious_target_count: int = 0
    duplicate_identity_groups: int = 0
    reason_code_counts: dict[str, int] = field(default_factory=dict)
    sample_target: list[dict[str, Any]] = field(default_factory=list)
    sample_review: list[dict[str, Any]] = field(default_factory=list)
    sample_excluded: list[dict[str, Any]] = field(default_factory=list)
    projected_review_rows: list[dict[str, Any]] = field(default_factory=list)


def audit_report_to_dict(report: AuditReport) -> dict[str, Any]:
    return asdict(report)


def _audit_row(job: Any, company: str, detailed: Any) -> dict[str, Any]:
    return {
        "id": job.id,
        "company": company,
        "company_id": job.company_id,
        "source_id": job.source_id,
        "title": job.title or "",
        "location": job.location or "",
        "job_type": job.job_type or "",
        "url": job.url or "",
        "projected_status": detailed.relevance_status,
        "projected_tags": list(detailed.tags),
        "projected_reasons": list(detailed.reasons),
        "projected_version": detailed.version,
    }


def _review_export_row(job: Any, company: str, detailed: Any) -> dict[str, Any]:
    row = _audit_row(job, company, detailed)
    row.update(
        {
            "description": job.description or "",
            "responsibilities": job.responsibilities or "",
            "requirements": job.requirements or "",
        }
    )
    return row


def run_audit(
    session: Session,
    *,
    sample_size: int = 10,
    seed: int = 20260710,
) -> AuditReport:
    """Project classifications without mutating or committing any ORM state."""
    from findjobs.models import Job

    jobs = (
        session.query(Job)
        .options(joinedload(Job.company))
        .order_by(Job.id)
        .all()
    )
    statuses: Counter[str] = Counter()
    tag_counts: dict[str, Counter[str]] = {
        "target": Counter(),
        "review": Counter(),
        "excluded": Counter(),
    }
    reason_counts: Counter[str] = Counter()
    identity_counts: Counter[tuple[int, str, str]] = Counter()
    rows_by_status: dict[str, list[dict[str, Any]]] = {
        "target": [],
        "review": [],
        "excluded": [],
    }
    review_rows: list[dict[str, Any]] = []
    drift_count = 0
    algorithm_residual_count = 0
    suspicious_target_count = 0

    for job in jobs:
        detailed = classify_job_detailed(
            job.title or "", job.description or "", job.job_type or ""
        )
        status = detailed.relevance_status
        company = job.company.name if job.company is not None else ""
        audit_row = _audit_row(job, company, detailed)
        rows_by_status[status].append(audit_row)
        statuses[status] += 1
        tag_counts[status].update(detailed.tags)
        reason_counts.update(detailed.reasons)

        identity = _identity_key(job)
        if identity is not None:
            identity_counts[identity] += 1

        if status != "excluded" and _is_algorithm_residual(
            job.title or "", job.job_type or ""
        ):
            algorithm_residual_count += 1
        if status == "target" and _matches_functional(job.title or ""):
            suspicious_target_count += 1
        if status == "review":
            review_rows.append(_review_export_row(job, company, detailed))

        if (
            (job.relevance_status or "target") != status
            or set(_decode_list(job.matched_tags)) != set(detailed.tags)
            or set(_decode_list(job.classification_reasons)) != set(detailed.reasons)
            or (job.classification_version or "") != CLASSIFICATION_VERSION
        ):
            drift_count += 1

    duplicate_groups = sum(count > 1 for count in identity_counts.values())
    projected_tags = {
        status: dict(sorted(counts.items())) for status, counts in tag_counts.items()
    }

    return AuditReport(
        scanned=len(jobs),
        projected_target=statuses["target"],
        projected_review=statuses["review"],
        projected_excluded=statuses["excluded"],
        projected_tags=projected_tags,
        drift_count=drift_count,
        algorithm_residual_count=algorithm_residual_count,
        suspicious_target_count=suspicious_target_count,
        duplicate_identity_groups=duplicate_groups,
        reason_code_counts=dict(sorted(reason_counts.items())),
        sample_target=_seeded_sample(rows_by_status["target"], sample_size, seed),
        sample_review=_seeded_sample(rows_by_status["review"], sample_size, seed),
        sample_excluded=_seeded_sample(rows_by_status["excluded"], sample_size, seed),
        projected_review_rows=review_rows,
    )
