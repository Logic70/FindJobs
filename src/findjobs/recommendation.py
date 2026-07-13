"""Deterministic, evidence-based job recommendation engine."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping
from types import MappingProxyType

from sqlalchemy.orm import Session

from findjobs.exporter import query_jobs
from findjobs.locations import split_locations
from findjobs.profile_import import detect_skills
from findjobs.recommendation_profile import RecommendationProfile

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORTED_TAGS = frozenset({"ai", "security", "ai security"})

_ALGORITHM_CN_RE = re.compile(r"算法")
_ALGORITHM_EN_RE = re.compile(r"algorithm", re.IGNORECASE)

_HUAWEI_SLUG_RE = re.compile(r"huawei", re.IGNORECASE)
_HUAWEI_NAME_RE = re.compile(r"华为")

_EXP_YEARS_RE = re.compile(r"(\d+)\+?\s*(?:years?|year|年|年以上)")

_REMOTE_VALUES = frozenset({"remote", "远程"})

# Domain-inference helpers.
# ASCII terms use ``\b`` word boundaries so ``ai`` does not fire on
# ``email`` and ``mlops`` does not fire on ``htmlops``.
# Chinese terms use safe substring matching (CJK characters do not
# participate in ASCII ``\w``).
_AI_DOMAIN_TERMS: list[str] = [
    "ai", "ml", "llm", "nlp", "mlops",
    "artificial intelligence", "machine learning",
    "deep learning", "computer vision",
    "深度学习", "大模型", "自然语言处理", "神经网络",
]

_SECURITY_DOMAIN_TERMS: list[str] = [
    "security", "cybersecurity", "appsec", "penetration",
    "vulnerability", "threat", "privacy",
    "安全", "渗透", "漏洞", "威胁", "隐私", "网络安全",
]


def _domain_term_match(text: str, term: str) -> bool:
    """Return True when *text* contains *term* as a domain signal.

    ASCII terms are anchored with ``\b`` word boundaries; CJK terms
    use substring matching (safe because CJK ideographs are outside
    the ASCII ``\\w`` set).
    """
    if any("一" <= c <= "鿿" for c in term):
        return term in text
    return bool(
        re.search(rf"\b{re.escape(term)}\b", text, re.IGNORECASE | re.ASCII)
    )

_UNAVAILABLE_COMPLETENESS = frozenset({"responsibilities_only", "combined_only", "missing", ""})

_MAX_DOMAIN = 25.0
_MAX_SKILLS = 30.0
_MAX_REQUIREMENTS = 20.0
_MAX_EXPERIENCE = 15.0
_MAX_LOCATION = 10.0

_HIGH_THRESHOLD = 75.0
_MEDIUM_THRESHOLD = 55.0

# Internal gap-term markers (not real skill names)
_GAP_MISSING_REQS = "[[requirements_unavailable]]"
_GAP_EXPERIENCE = "[[experience_years_unavailable]]"
_GAP_LOCATION = "[[location_no_match]]"


# ---------------------------------------------------------------------------
# Frozen score / recommendation types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoreComponent:
    """Evidence for one scoring dimension.

    Each instance records the numeric score, a human-readable message,
    the source field names from the job row and profile that were consulted,
    the specific terms that matched, and the specific terms that were gaps.
    """

    score: float
    max_score: float
    message: str
    source_fields: tuple[str, ...] = ()
    profile_fields: tuple[str, ...] = ()
    matched_terms: tuple[str, ...] = ()
    gap_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class Recommendation:
    """A scored, evidence-based job recommendation."""

    job_id: int
    company_slug: str
    company_name: str
    title: str
    location: str
    job_type: str
    tags: tuple[str, ...]
    url: str
    salary_text: str
    salary_min: float | None
    salary_max: float | None
    salary_currency: str
    salary_period: str
    salary_disclosed: bool
    responsibilities: str
    requirements: str
    detail_completeness: str
    total_score: float
    tier: str
    domain: ScoreComponent
    skills: ScoreComponent
    requirements_score: ScoreComponent
    experience: ScoreComponent
    location_score: ScoreComponent
    matched_skills: tuple[str, ...]
    gaps: tuple[str, ...]
    application_advice: str


@dataclass(frozen=True)
class RecommendationResult:
    """Top-level result of a recommendation run."""

    scanned: int
    eligible: int
    hard_exclusion_counts: Mapping[str, int]
    recommendations: tuple[Recommendation, ...]
    aggregate_learning_advice: str


# ---------------------------------------------------------------------------
# Profile domain inference
# ---------------------------------------------------------------------------


def infer_profile_domain(profile: RecommendationProfile) -> str | None:
    """Detect AI/Security domain from profile skills, roles, and target_roles.

    Domain terms are matched with a unified helper that applies ``\b`` word
    boundaries to ASCII tokens (so ``email`` does not trigger ``ai``) while
    using safe substring matching for Chinese terms.

    Returns ``"AI"``, ``"Security"``, ``"AI Security"``, or ``None`` when the
    profile carries no explicit domain signal (neutral).
    """
    texts: list[str] = []
    texts.extend(s.lower() for s in profile.skills)
    texts.extend(r.lower() for r in profile.roles)
    texts.extend(r.lower() for r in profile.target_roles)

    def _has_ai(text: str) -> bool:
        return any(_domain_term_match(text, t) for t in _AI_DOMAIN_TERMS)

    def _has_security(text: str) -> bool:
        return any(_domain_term_match(text, t) for t in _SECURITY_DOMAIN_TERMS)

    has_ai = any(_has_ai(text) for text in texts)
    has_security = any(_has_security(text) for text in texts)

    if has_ai and has_security:
        return "AI Security"
    if has_ai:
        return "AI"
    if has_security:
        return "Security"
    return None


# ---------------------------------------------------------------------------
# Hard exclusion helpers
# ---------------------------------------------------------------------------


def _has_algorithm_rejection(title: str, job_type: str) -> bool:
    """Return True when *title* or *job_type* contains Chinese ``算法`` or English ``algorithm``."""
    for text in (title, job_type):
        if _ALGORITHM_CN_RE.search(text) or _ALGORITHM_EN_RE.search(text):
            return True
    return False


def _is_huawei(company_slug: str, company_name: str) -> bool:
    """Return True when company identity matches Huawei regardless of profile."""
    return bool(_HUAWEI_SLUG_RE.search(company_slug) or _HUAWEI_NAME_RE.search(company_name))


def _is_excluded_company(
    company_slug: str, company_name: str, profile: RecommendationProfile
) -> bool:
    """Return True when company matches a profile excluded company name.

    Matching is bi-directional (the exclusion may be a fragment inside the
    company identity, or the company identity may be a fragment inside the
    exclusion entry, e.g. ``"Tencent and its affiliates"`` excludes slug
    ``tencent``).  Entries shorter than two characters are silently skipped
    so blank/tiny values do not match everything.
    """
    identities = tuple(
        value.strip().lower()
        for value in (company_slug, company_name)
        if value and value.strip()
    )

    def meaningful_fragment(value: str) -> bool:
        has_cjk = any("一" <= char <= "鿿" for char in value)
        return len(value) >= (2 if has_cjk else 3)

    for excl in profile.excluded_companies:
        e = excl.strip().lower()
        if not e:
            continue
        for identity in identities:
            if e == identity:
                return True
            if meaningful_fragment(e) and e in identity:
                return True
            if meaningful_fragment(identity) and identity in e:
                return True
    return False


def _has_unsupported_tags(tags: tuple[str, ...]) -> bool:
    """Return True when *tags* are empty or any tag is not AI / Security / AI Security."""
    if not tags:
        return True
    return any(t.lower() not in SUPPORTED_TAGS for t in tags)


def _get_job_domain(tags: tuple[str, ...]) -> tuple[bool, bool]:
    """Return (has_ai, has_security) from matched_tags."""
    lower = frozenset(t.lower() for t in tags)
    has_ai = "ai" in lower or "ai security" in lower
    has_security = "security" in lower or "ai security" in lower
    return has_ai, has_security


# ---------------------------------------------------------------------------
# Experience extraction from requirements text
# ---------------------------------------------------------------------------


def _extract_required_years(text: str) -> float | None:
    """Extract the maximum explicit experience year value from *text*, or ``None``.

    Only recognises patterns like ``N years``, ``N+ years``, ``N年``, and
    ``N年以上``.  No inference or defaulting is performed.
    """
    matches = [int(m) for m in _EXP_YEARS_RE.findall(text)]
    return float(max(matches)) if matches else None


# ---------------------------------------------------------------------------
# Scoring: domain (0-25)
# ---------------------------------------------------------------------------


def _score_domain(
    has_ai: bool,
    has_security: bool,
    profile_domain: str | None,
) -> ScoreComponent:
    """Score domain alignment between job tags and profile domain preference."""
    if profile_domain is None:
        return ScoreComponent(
            score=_MAX_DOMAIN / 2,
            max_score=_MAX_DOMAIN,
            message="No explicit domain signal in profile; neutral score applied.",
            source_fields=("matched_tags",),
            profile_fields=("skills", "roles", "target_roles"),
        )

    profile_parts = profile_domain.split()  # "AI Security" -> ["AI", "Security"]
    job_parts: list[str] = []
    if has_ai:
        job_parts.append("AI")
    if has_security:
        job_parts.append("Security")

    matched = [p for p in profile_parts if p in job_parts]
    gaps = [p for p in profile_parts if p not in job_parts]

    if matched and not gaps:
        score = _MAX_DOMAIN
        msg = f"Profile domain {profile_domain} fully matches job tags."
    elif matched:
        score = _MAX_DOMAIN / 2
        msg = f"Profile domain {profile_domain} partially matches job tags."
    else:
        score = 0.0
        msg = f"Profile domain {profile_domain} does not match job tags."

    return ScoreComponent(
        score=score,
        max_score=_MAX_DOMAIN,
        message=msg,
        source_fields=("matched_tags",),
        profile_fields=("skills", "roles", "target_roles"),
        matched_terms=tuple(matched),
        gap_terms=tuple(gaps),
    )


# ---------------------------------------------------------------------------
# Scoring: skills (0-30)
# ---------------------------------------------------------------------------


def _score_skills(
    title: str,
    responsibilities: str,
    requirements: str,
    profile_skills: tuple[str, ...],
) -> ScoreComponent:
    """Score canonical skill coverage from job text against profile skills."""
    job_text = f"{title} {responsibilities} {requirements}"
    demand_skills = detect_skills(job_text)

    if not demand_skills:
        return ScoreComponent(
            score=_MAX_SKILLS / 2,
            max_score=_MAX_SKILLS,
            message="No recognizable skill demand detected; neutral score applied.",
            source_fields=("title", "responsibilities", "requirements"),
            profile_fields=("skills",),
        )

    # Canonical skills detected from profile skill text
    profile_text = " ".join(profile_skills)
    profile_canonical = detect_skills(profile_text)
    job_text_lower = job_text.lower()
    profile_lower = [s.lower() for s in profile_skills]

    matched: list[str] = []
    for ds in demand_skills:
        if ds in profile_canonical:
            matched.append(ds)
        elif ds.lower() in profile_text.lower():
            matched.append(ds)
        elif any(ds.lower() in pl or pl in ds.lower() for pl in profile_lower):
            if ds not in matched:
                matched.append(ds)
        # Case-insensitive exact profile-skill phrase hit in job text
        elif any(ps.lower() in job_text_lower for ps in profile_skills):
            # A profile phrase appears in job text; check if it's related
            for ps in profile_skills:
                if ps.lower() in job_text_lower and (
                    ds.lower() in ps.lower() or ps.lower() in ds.lower()
                ):
                    matched.append(ds)
                    break

    gaps = [s for s in demand_skills if s not in matched]
    ratio = len(matched) / len(demand_skills) if demand_skills else 0
    score = round(_MAX_SKILLS * ratio, 1)

    return ScoreComponent(
        score=score,
        max_score=_MAX_SKILLS,
        message=f"Matched {len(matched)}/{len(demand_skills)} recognized skills.",
        source_fields=("title", "responsibilities", "requirements"),
        profile_fields=("skills",),
        matched_terms=tuple(matched),
        gap_terms=tuple(gaps),
    )


# ---------------------------------------------------------------------------
# Scoring: requirements (0-20)
# ---------------------------------------------------------------------------


def _score_requirements(
    requirements: str,
    detail_completeness: str,
    profile_skills: tuple[str, ...],
) -> ScoreComponent:
    """Score requirement-skill coverage from stored requirements text."""
    is_unavailable = (
        not requirements.strip()
        or detail_completeness.strip().lower() in _UNAVAILABLE_COMPLETENESS
    )

    if is_unavailable:
        return ScoreComponent(
            score=_MAX_REQUIREMENTS / 2,
            max_score=_MAX_REQUIREMENTS,
            message="Requirements not available; needs verification. Neutral score applied.",
            source_fields=("requirements", "detail_completeness"),
            profile_fields=("skills",),
            gap_terms=(_GAP_MISSING_REQS,),
        )

    req_skills = detect_skills(requirements)

    if not req_skills:
        return ScoreComponent(
            score=_MAX_REQUIREMENTS / 2,
            max_score=_MAX_REQUIREMENTS,
            message="No recognizable requirement skills; neutral score applied.",
            source_fields=("requirements",),
            profile_fields=("skills",),
        )

    profile_lower = [s.lower() for s in profile_skills]
    matched: list[str] = []
    for rs in req_skills:
        if rs.lower() in profile_lower:
            matched.append(rs)
        elif any(rs.lower() in pl or pl in rs.lower() for pl in profile_lower):
            if rs not in matched:
                matched.append(rs)

    gaps = [s for s in req_skills if s not in matched]
    ratio = len(matched) / len(req_skills) if req_skills else 0
    score = round(_MAX_REQUIREMENTS * ratio, 1)

    return ScoreComponent(
        score=score,
        max_score=_MAX_REQUIREMENTS,
        message=f"Covered {len(matched)}/{len(req_skills)} requirement skills.",
        source_fields=("requirements",),
        profile_fields=("skills",),
        matched_terms=tuple(matched),
        gap_terms=tuple(gaps),
    )


# ---------------------------------------------------------------------------
# Scoring: experience (0-15)
# ---------------------------------------------------------------------------


def _score_experience(
    requirements: str,
    profile_years: float | None,
) -> ScoreComponent:
    """Score experience match using explicit year requirements."""
    required_years = _extract_required_years(requirements)

    if required_years is None and profile_years is None:
        return ScoreComponent(
            score=8.0,
            max_score=_MAX_EXPERIENCE,
            message="No experience requirement in job and no profile experience; neutral score.",
            source_fields=("requirements",),
            profile_fields=("experience_years",),
        )

    if required_years is None:
        return ScoreComponent(
            score=8.0,
            max_score=_MAX_EXPERIENCE,
            message="No explicit experience requirement in job; neutral score.",
            source_fields=("requirements",),
            profile_fields=("experience_years",),
        )

    if profile_years is None:
        return ScoreComponent(
            score=8.0,
            max_score=_MAX_EXPERIENCE,
            message="No profile experience years; neutral score.",
            source_fields=("requirements",),
            profile_fields=("experience_years",),
            gap_terms=(_GAP_EXPERIENCE,),
        )

    shortfall = required_years - profile_years
    if shortfall <= 0:
        score = _MAX_EXPERIENCE
        msg = (
            f"Profile experience ({profile_years}y) meets"
            f" requirement ({int(required_years)}y)."
        )
        gap_terms: tuple[str, ...] = ()
    elif shortfall <= 1.0:
        score = 8.0
        msg = (
            f"Profile experience ({profile_years}y) within 1 year of"
            f" requirement ({int(required_years)}y)."
        )
        gap_terms = (f"requires_{int(required_years)}+_years",)
    else:
        score = 0.0
        msg = (
            f"Profile experience ({profile_years}y) below"
            f" requirement ({int(required_years)}y) by >1 year."
        )
        gap_terms = (f"requires_{int(required_years)}+_years",)

    return ScoreComponent(
        score=score,
        max_score=_MAX_EXPERIENCE,
        message=msg,
        source_fields=("requirements",),
        profile_fields=("experience_years",),
        gap_terms=gap_terms,
    )


# ---------------------------------------------------------------------------
# Scoring: location (0-10)
# ---------------------------------------------------------------------------


def _score_location(
    job_location: str,
    profile_cities: tuple[str, ...],
) -> ScoreComponent:
    """Score location match using canonicalized city values."""
    if not job_location.strip():
        return ScoreComponent(
            score=_MAX_LOCATION / 2,
            max_score=_MAX_LOCATION,
            message="Job location not specified; neutral score.",
            source_fields=("location",),
            profile_fields=("target_cities",),
        )

    if not profile_cities:
        return ScoreComponent(
            score=_MAX_LOCATION / 2,
            max_score=_MAX_LOCATION,
            message="Profile target cities not specified; neutral score.",
            source_fields=("location",),
            profile_fields=("target_cities",),
        )

    job_parts = split_locations(job_location)
    if not job_parts:
        return ScoreComponent(
            score=_MAX_LOCATION / 2,
            max_score=_MAX_LOCATION,
            message="Job location could not be parsed; neutral score.",
            source_fields=("location",),
            profile_fields=("target_cities",),
        )

    # Normalise remote values in job parts
    normalized_job: list[str] = []
    for p in job_parts:
        if p.lower() in _REMOTE_VALUES:
            normalized_job.append("remote")
        else:
            normalized_job.append(p.lower())

    profile_lower = {c.lower() for c in profile_cities}

    matched: list[str] = []
    for nj in normalized_job:
        if nj in profile_lower:
            matched.append(nj)

    if matched:
        return ScoreComponent(
            score=_MAX_LOCATION,
            max_score=_MAX_LOCATION,
            message=f"Location matches: {', '.join(sorted(set(matched)))}.",
            source_fields=("location",),
            profile_fields=("target_cities",),
            matched_terms=tuple(sorted(set(matched))),
        )

    return ScoreComponent(
        score=0.0,
        max_score=_MAX_LOCATION,
        message="Location does not match any target city.",
        source_fields=("location",),
        profile_fields=("target_cities",),
        gap_terms=(_GAP_LOCATION,),
    )


# ---------------------------------------------------------------------------
# Tier determination
# ---------------------------------------------------------------------------


def _determine_tier(score: float) -> str:
    if score >= _HIGH_THRESHOLD:
        return "high"
    if score >= _MEDIUM_THRESHOLD:
        return "medium"
    return "exploratory"


# ---------------------------------------------------------------------------
# Application advice
# ---------------------------------------------------------------------------


def _build_advice(
    salary_disclosed: bool,
    matched_skills: tuple[str, ...],
    gaps: tuple[str, ...],
    skills_gap_terms: tuple[str, ...] = (),
) -> str:
    """Generate deterministic, factual application advice."""
    parts: list[str] = []
    if salary_disclosed:
        parts.append(
            "Salary is disclosed; review and negotiate based on disclosed figures."
        )
    else:
        parts.append(
            "Salary not disclosed; confirm compensation during application process."
        )

    if matched_skills:
        parts.append(f"Highlight matched skills: {', '.join(matched_skills)}.")

    # Collect all readable gap terms for advice
    gap_items: list[str] = list(skills_gap_terms)
    for g in gaps:
        if not g.startswith("requires_") and g not in (
            _GAP_MISSING_REQS,
            _GAP_EXPERIENCE,
            _GAP_LOCATION,
        ):
            gap_items.append(g)

    if gap_items:
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for item in gap_items:
            if item.lower() not in seen:
                seen.add(item.lower())
                unique.append(item)
        parts.append(f"Address skill gaps: {', '.join(unique)}.")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Aggregate learning advice
# ---------------------------------------------------------------------------


def _build_aggregate_advice(
    recommendations: tuple[Recommendation, ...],
) -> str:
    """Find recurring explicit skill gaps across recommendations.

    A gap is considered recurring when the same explicit skill gap name
    appears in the ``requirements_score`` gap terms of more than one
    recommendation.
    """
    gap_freq: dict[str, int] = {}
    for rec in recommendations:
        for term in rec.requirements_score.gap_terms:
            if not term.startswith("[["):
                gap_freq[term] = gap_freq.get(term, 0) + 1

    recurring = {k: v for k, v in gap_freq.items() if v > 1}
    if not recurring:
        return "No repeated explicit skill gap was found across recommendations."

    sorted_gaps = sorted(recurring.items(), key=lambda x: (-x[1], x[0]))
    gap_list = [
        f"{g} (appears in {c} recommendations)" for g, c in sorted_gaps
    ]
    return f"Recurring skill gaps to address: {', '.join(gap_list)}."


# ---------------------------------------------------------------------------
# Main recommendation function
# ---------------------------------------------------------------------------

_REQUIRED_ROW_FIELDS = frozenset({
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


def recommend_jobs(
    rows: list[dict[str, Any]],
    profile: RecommendationProfile,
    limit: int = 50,
) -> RecommendationResult:
    """Score and rank job rows against a recommendation profile.

    Args:
        rows:
            Full-export row dictionaries (must include all fields from
            ``FULL_COLUMNS`` that are needed for scoring).
        profile:
            The user's recommendation profile.
        limit:
            Maximum number of recommendations to return (1..1000).

    Returns:
        A ``RecommendationResult`` with scored recommendations.

    Raises:
        ValueError: If *limit* is not a positive integer between 1 and 1000.
        KeyError: If any row is missing required full-detail fields.
    """
    if not isinstance(limit, int) or limit <= 0 or limit > 1000:
        raise ValueError(
            f"limit must be a positive integer between 1 and 1000,"
            f" got {limit!r}"
        )

    scanned = len(rows)
    exclusion_counts: dict[str, int] = {
        "non_active_status": 0,
        "non_target_relevance": 0,
        "unsupported_tags": 0,
        "algorithm_rejection": 0,
        "huawei_exclusion": 0,
        "profile_excluded_company": 0,
        "missing_url": 0,
    }

    eligible_rows: list[dict[str, Any]] = []

    # Text fields that may legitimately be ``None`` in a full-export row;
    # coerce to empty string before scoring to avoid ``AttributeError``.
    _TEXT_FIELDS = frozenset({
        "title", "location", "job_type", "responsibilities", "requirements",
        "salary_text", "salary_currency", "salary_period",
        "company_slug", "company_name", "detail_completeness",
    })

    for raw_row in rows:
        # Work on a shallow copy — never mutate caller-provided dicts.
        row = dict(raw_row)

        # Validate required fields
        missing = _REQUIRED_ROW_FIELDS - set(row.keys())
        if missing:
            raise KeyError(
                f"Row {row.get('id', '?')} is missing required fields:"
                f" {sorted(missing)}"
            )

        # Coerce nullable text fields before hard-exclusion checks and scoring.
        for _f in _TEXT_FIELDS:
            if row.get(_f) is None:
                row[_f] = ""

        # -- hard exclusions (order matches the public contract) --

        # 1. non-active status
        if row.get("status", "") != "active":
            exclusion_counts["non_active_status"] += 1
            continue

        # 2. non-target relevance
        if row.get("relevance_status", "") != "target":
            exclusion_counts["non_target_relevance"] += 1
            continue

        # 3. unsupported domain tags
        tags = tuple(str(t) for t in (row.get("matched_tags") or []))
        if _has_unsupported_tags(tags):
            exclusion_counts["unsupported_tags"] += 1
            continue

        # 4. algorithm rejection in title or job_type
        title = row.get("title", "")
        job_type = row.get("job_type", "")
        if _has_algorithm_rejection(title, job_type):
            exclusion_counts["algorithm_rejection"] += 1
            continue

        # 5. Huawei hard exclusion
        company_slug = row.get("company_slug", "")
        company_name = row.get("company_name", "")
        if _is_huawei(company_slug, company_name):
            exclusion_counts["huawei_exclusion"] += 1
            continue

        # 6. profile excluded company
        if _is_excluded_company(company_slug, company_name, profile):
            exclusion_counts["profile_excluded_company"] += 1
            continue

        # 7. missing/blank official URL
        url = (row.get("url") or "").strip()
        if not url:
            exclusion_counts["missing_url"] += 1
            continue

        eligible_rows.append(row)

    # Infer profile domain once
    profile_domain = infer_profile_domain(profile)
    recommendations: list[Recommendation] = []

    for row in eligible_rows:
        tags = tuple(str(t) for t in (row.get("matched_tags") or []))
        title = row.get("title", "")
        responsibilities = row.get("responsibilities", "")
        requirements = row.get("requirements", "")
        detail_completeness = row.get("detail_completeness", "")
        profile_skills = profile.skills

        # Domain
        has_ai, has_security = _get_job_domain(tags)
        domain_comp = _score_domain(has_ai, has_security, profile_domain)

        # Skills
        skills_comp = _score_skills(
            title, responsibilities, requirements, profile_skills,
        )
        matched_skills = list(skills_comp.matched_terms)

        # Requirements
        req_comp = _score_requirements(
            requirements, detail_completeness, profile_skills,
        )

        # Experience
        exp_comp = _score_experience(requirements, profile.experience_years)

        # Location
        loc_comp = _score_location(
            row.get("location", ""), profile.target_cities,
        )

        total = round(
            domain_comp.score
            + skills_comp.score
            + req_comp.score
            + exp_comp.score
            + loc_comp.score,
            1,
        )
        tier = _determine_tier(total)

        # Build human-readable gaps
        gaps: list[str] = []
        for term in req_comp.gap_terms:
            if term.startswith("[["):
                gaps.append("Requirements not verified; confirmation needed.")
            elif term:
                gaps.append(term)
        for term in exp_comp.gap_terms:
            if term.startswith("[["):
                gaps.append("Experience shortfall: profile years unavailable.")
            else:
                gaps.append(f"Experience: {term.replace('_', ' ')}")
        if loc_comp.gap_terms:
            gaps.append("Location does not match target cities.")

        # Application advice
        advice = _build_advice(
            bool(row.get("salary_disclosed", False)),
            tuple(matched_skills),
            tuple(gaps),
            skills_gap_terms=tuple(skills_comp.gap_terms),
        )

        rec = Recommendation(
            job_id=int(row["id"]),
            company_slug=str(row.get("company_slug", "")),
            company_name=str(row.get("company_name", "")),
            title=title,
            location=str(row.get("location", "")),
            job_type=job_type,
            tags=tags,
            url=str(row.get("url", "")),
            salary_text=str(row.get("salary_text", "")),
            salary_min=row.get("salary_min"),
            salary_max=row.get("salary_max"),
            salary_currency=str(row.get("salary_currency", "")),
            salary_period=str(row.get("salary_period", "")),
            salary_disclosed=bool(row.get("salary_disclosed", False)),
            responsibilities=responsibilities,
            requirements=requirements,
            detail_completeness=detail_completeness,
            total_score=total,
            tier=tier,
            domain=domain_comp,
            skills=skills_comp,
            requirements_score=req_comp,
            experience=exp_comp,
            location_score=loc_comp,
            matched_skills=tuple(matched_skills),
            gaps=tuple(gaps),
            application_advice=advice,
        )
        recommendations.append(rec)

    # Sort: score descending, then job_id descending
    recommendations.sort(key=lambda r: (-r.total_score, -r.job_id))
    recommendations = recommendations[:limit]

    aggregate_advice = _build_aggregate_advice(tuple(recommendations))

    return RecommendationResult(
        scanned=scanned,
        eligible=len(eligible_rows),
        hard_exclusion_counts=MappingProxyType(exclusion_counts),
        recommendations=tuple(recommendations),
        aggregate_learning_advice=aggregate_advice,
    )


# ---------------------------------------------------------------------------
# Session-based convenience wrapper
# ---------------------------------------------------------------------------


def recommend_from_session(
    session: Session,
    profile: RecommendationProfile,
    limit: int = 50,
) -> RecommendationResult:
    """Score jobs from the database against a profile.

    Queries all jobs with ``detail_level="full"`` via ``query_jobs`` and
    scores them.  Does **not** write, flush, or commit to the database.
    """
    rows = query_jobs(session, detail_level="full")
    return recommend_jobs(rows, profile, limit=limit)
