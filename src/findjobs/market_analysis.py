"""Deterministic market-demand analysis over full exported job facts."""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import yaml

from findjobs.job_types import split_job_types
from findjobs.keyword_analysis import (
    KeywordDefinition,
    KeywordDocument,
    KeywordRules,
    analyze_keywords,
    default_keyword_rules,
    load_keyword_rules,
)
from findjobs.locations import split_locations
from findjobs.recommendation_profile import (
    RecommendationProfile,
    load_recommendation_profile,
)


class MarketAnalysisError(ValueError):
    """Raised when market-analysis input or taxonomy is invalid."""


@dataclass(frozen=True)
class TaxonomyTerm:
    id: str
    name: str
    category: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class RoleFamily:
    id: str
    name: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class MarketTaxonomy:
    schema_version: int
    taxonomy_version: str
    role_families: tuple[RoleFamily, ...]
    domain_signals: tuple[TaxonomyTerm, ...]
    skills: tuple[TaxonomyTerm, ...]
    traits: tuple[TaxonomyTerm, ...]

    @property
    def domain_signals_by_id(self) -> dict[str, TaxonomyTerm]:
        return {term.id: term for term in self.domain_signals}

    @property
    def skills_by_id(self) -> dict[str, TaxonomyTerm]:
        return {term.id: term for term in self.skills}

    @property
    def traits_by_id(self) -> dict[str, TaxonomyTerm]:
        return {term.id: term for term in self.traits}


@dataclass(frozen=True)
class MarketAnalysisRun:
    jobs_path: Path
    taxonomy_path: Path
    keyword_rules_path: Path | None
    profile_used: bool
    json_output: Path
    analyzed_jobs: int
    requirements_available_jobs: int


@dataclass(frozen=True)
class _AnalyzedJob:
    row: dict[str, Any]
    job_id: str
    company_slug: str
    company_name: str
    role_family_id: str
    role_family_name: str
    locations: tuple[str, ...]
    job_types: tuple[str, ...]
    requirements_available: bool
    responsibilities_available: bool
    requirement_domain_signals: dict[str, str]
    requirement_skills: dict[str, str]
    requirement_traits: dict[str, str]
    work_domain_signals: frozenset[str]
    work_skills: frozenset[str]
    work_traits: frozenset[str]
    required_years: float | None
    education_level: str | None


_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_ALGORITHM_RE = re.compile(r"算法|\balgorithms?\b", re.IGNORECASE | re.ASCII)
_CLAUSE_RE = re.compile(r"[\n\r；;。]+")
_PREFERRED_RE = re.compile(
    r"优先|加分|更佳|者佳|preferred|nice\s+to\s+have|bonus|a\s+plus",
    re.IGNORECASE,
)
_REQUIRED_RE = re.compile(
    r"必须|要求|熟悉|掌握|具备|精通|能够|能力|经验|"
    r"required|must|proficient|familiar|experience",
    re.IGNORECASE,
)
_YEAR_RANGE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*[-~至到]\s*\d+(?:\.\d+)?\s*(?:年|years?)", re.IGNORECASE
)
_YEAR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:\+|年以上|年|years?)", re.IGNORECASE)

_REQUIRED_FULL_FIELDS = frozenset(
    {
        "id",
        "company_slug",
        "company_name",
        "title",
        "location",
        "job_type",
        "status",
        "matched_tags",
        "first_seen_at",
        "published_at",
        "relevance_status",
        "responsibilities",
        "requirements",
        "detail_completeness",
    }
)

_FALLBACK_FAMILIES = {
    "other_ai": "其他AI岗位",
    "other_security": "其他安全岗位",
    "other_target": "其他目标岗位",
}


def _as_nonempty_string(value: Any, field: str, path: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MarketAnalysisError(f"{path}: {field} must be a non-empty string")
    return value.strip()


def _load_aliases(value: Any, field: str, path: Path) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise MarketAnalysisError(f"{path}: {field}.aliases must be a non-empty list")
    aliases: list[str] = []
    seen: set[str] = set()
    for alias in value:
        text = _as_nonempty_string(alias, f"{field}.aliases", path)
        key = text.casefold()
        if key not in seen:
            seen.add(key)
            aliases.append(text)
    return tuple(aliases)


def _load_terms(
    value: Any,
    section: str,
    path: Path,
    seen_ids: set[str],
) -> tuple[TaxonomyTerm, ...]:
    if not isinstance(value, list):
        raise MarketAnalysisError(f"{path}: {section} must be a list")
    terms: list[TaxonomyTerm] = []
    for index, raw in enumerate(value):
        field = f"{section}[{index}]"
        if not isinstance(raw, dict):
            raise MarketAnalysisError(f"{path}: {field} must be an object")
        term_id = _as_nonempty_string(raw.get("id"), f"{field}.id", path)
        if not _ID_RE.fullmatch(term_id):
            raise MarketAnalysisError(f"{path}: invalid id {term_id!r} in {field}")
        if term_id in seen_ids:
            raise MarketAnalysisError(f"{path}: duplicate term id {term_id!r}")
        seen_ids.add(term_id)
        terms.append(
            TaxonomyTerm(
                id=term_id,
                name=_as_nonempty_string(raw.get("name"), f"{field}.name", path),
                category=_as_nonempty_string(
                    raw.get("category"), f"{field}.category", path
                ),
                aliases=_load_aliases(raw.get("aliases"), field, path),
            )
        )
    return tuple(terms)


def load_market_taxonomy(path: Path) -> MarketTaxonomy:
    """Load and validate a versioned market-analysis taxonomy."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MarketAnalysisError(f"Market taxonomy not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise MarketAnalysisError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise MarketAnalysisError(f"{path}: taxonomy root must be an object")
    if raw.get("schema_version") != 2:
        raise MarketAnalysisError(f"{path}: unsupported schema_version")
    version = _as_nonempty_string(raw.get("taxonomy_version"), "taxonomy_version", path)

    role_families_raw = raw.get("role_families")
    if not isinstance(role_families_raw, list):
        raise MarketAnalysisError(f"{path}: role_families must be a list")
    role_families: list[RoleFamily] = []
    role_ids: set[str] = set()
    for index, item in enumerate(role_families_raw):
        field = f"role_families[{index}]"
        if not isinstance(item, dict):
            raise MarketAnalysisError(f"{path}: {field} must be an object")
        family_id = _as_nonempty_string(item.get("id"), f"{field}.id", path)
        if not _ID_RE.fullmatch(family_id):
            raise MarketAnalysisError(f"{path}: invalid id {family_id!r} in {field}")
        if family_id in role_ids:
            raise MarketAnalysisError(f"{path}: duplicate role family id {family_id!r}")
        role_ids.add(family_id)
        role_families.append(
            RoleFamily(
                id=family_id,
                name=_as_nonempty_string(item.get("name"), f"{field}.name", path),
                aliases=_load_aliases(item.get("aliases"), field, path),
            )
        )

    seen_ids: set[str] = set()
    domain_signals = _load_terms(
        raw.get("domain_signals"), "domain_signals", path, seen_ids
    )
    skills = _load_terms(raw.get("skills"), "skills", path, seen_ids)
    traits = _load_terms(raw.get("traits"), "traits", path, seen_ids)
    return MarketTaxonomy(
        schema_version=2,
        taxonomy_version=version,
        role_families=tuple(role_families),
        domain_signals=domain_signals,
        skills=skills,
        traits=traits,
    )


def _contains_alias(text: str, alias: str) -> bool:
    if not text or not alias:
        return False
    if any("\u3400" <= char <= "\u9fff" for char in alias):
        return alias.casefold() in text.casefold()
    pattern = rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])"
    return re.search(pattern, text, re.IGNORECASE | re.ASCII) is not None


def _matches_term(text: str, term: TaxonomyTerm) -> bool:
    return _contains_alias(text, term.name) or any(
        _contains_alias(text, alias) for alias in term.aliases
    )


def _matching_term_ids(text: str, terms: Iterable[TaxonomyTerm]) -> set[str]:
    return {term.id for term in terms if _matches_term(text, term)}


def _requirement_strengths(text: str, terms: Iterable[TaxonomyTerm]) -> dict[str, str]:
    clauses = [part.strip() for part in _CLAUSE_RE.split(text) if part.strip()]
    result: dict[str, str] = {}
    rank = {"unspecified": 0, "preferred": 1, "required": 2}
    for term in terms:
        strengths: list[str] = []
        for clause in clauses:
            if not _matches_term(clause, term):
                continue
            if _PREFERRED_RE.search(clause):
                strengths.append("preferred")
            elif _REQUIRED_RE.search(clause):
                strengths.append("required")
            else:
                strengths.append("unspecified")
        if strengths:
            result[term.id] = max(strengths, key=rank.__getitem__)
    return result


def _extract_required_years(text: str) -> float | None:
    values = [float(value) for value in _YEAR_RANGE_RE.findall(text)]
    values.extend(float(value) for value in _YEAR_RE.findall(text))
    return max(values) if values else None


def _extract_education(text: str) -> str | None:
    lower = text.casefold()
    levels: list[tuple[int, str]] = []
    patterns = (
        (1, "大专", ("大专", "专科", "associate degree")),
        (2, "本科", ("本科", "学士", "bachelor")),
        (3, "硕士", ("硕士", "master")),
        (4, "博士", ("博士", "phd", "doctorate")),
    )
    for rank, name, aliases in patterns:
        if any(alias in lower for alias in aliases):
            levels.append((rank, name))
    return min(levels)[1] if levels else None


def _classify_role_family(
    row: dict[str, Any], taxonomy: MarketTaxonomy
) -> tuple[str, str]:
    title = str(row.get("title") or "")
    responsibilities = str(row.get("responsibilities") or "")
    for family in taxonomy.role_families:
        if any(_contains_alias(title, alias) for alias in family.aliases):
            return family.id, family.name
    for family in taxonomy.role_families:
        if any(_contains_alias(responsibilities, alias) for alias in family.aliases):
            return family.id, family.name
    tags = {str(tag).casefold() for tag in row.get("matched_tags") or []}
    has_ai = "ai" in tags or "ai security" in tags
    has_security = "security" in tags or "ai security" in tags
    if has_ai and has_security:
        for family in taxonomy.role_families:
            if family.id == "ai_security":
                return family.id, family.name
    if has_ai:
        return "other_ai", _FALLBACK_FAMILIES["other_ai"]
    if has_security:
        return "other_security", _FALLBACK_FAMILIES["other_security"]
    return "other_target", _FALLBACK_FAMILIES["other_target"]


def _validate_row(row: Any, index: int) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise MarketAnalysisError(f"Row {index} must be an object")
    missing = _REQUIRED_FULL_FIELDS - set(row)
    if missing:
        raise MarketAnalysisError(
            f"Row {index} is missing full-export fields: {sorted(missing)}"
        )
    return row


def _prepare_jobs(
    rows: list[dict[str, Any]], taxonomy: MarketTaxonomy
) -> tuple[list[_AnalyzedJob], Counter[str]]:
    excluded: Counter[str] = Counter()
    seen_ids: set[str] = set()
    jobs: list[_AnalyzedJob] = []
    for index, raw in enumerate(rows):
        row = _validate_row(raw, index)
        job_id = str(row.get("id"))
        if job_id in seen_ids:
            excluded["duplicate"] += 1
            continue
        seen_ids.add(job_id)
        if str(row.get("status") or "").casefold() != "active":
            excluded["inactive"] += 1
            continue
        if str(row.get("relevance_status") or "").casefold() != "target":
            excluded["non_target"] += 1
            continue
        if _ALGORITHM_RE.search(
            f"{row.get('title') or ''} {row.get('job_type') or ''}"
        ):
            excluded["algorithm"] += 1
            continue

        requirements = str(row.get("requirements") or "").strip()
        responsibilities = str(row.get("responsibilities") or "").strip()
        family_id, family_name = _classify_role_family(row, taxonomy)
        company_slug = str(row.get("company_slug") or "unknown").strip() or "unknown"
        company_name = (
            str(row.get("company_name") or company_slug).strip() or company_slug
        )
        locations = tuple(split_locations(str(row.get("location") or ""))) or (
            "未标注",
        )
        job_types = tuple(split_job_types(str(row.get("job_type") or ""))) or (
            "未分类",
        )
        jobs.append(
            _AnalyzedJob(
                row=row,
                job_id=job_id,
                company_slug=company_slug,
                company_name=company_name,
                role_family_id=family_id,
                role_family_name=family_name,
                locations=locations,
                job_types=job_types,
                requirements_available=bool(requirements),
                responsibilities_available=bool(responsibilities),
                requirement_domain_signals=_requirement_strengths(
                    requirements, taxonomy.domain_signals
                ),
                requirement_skills=_requirement_strengths(
                    requirements, taxonomy.skills
                ),
                requirement_traits=_requirement_strengths(
                    requirements, taxonomy.traits
                ),
                work_domain_signals=frozenset(
                    _matching_term_ids(responsibilities, taxonomy.domain_signals)
                ),
                work_skills=frozenset(
                    _matching_term_ids(responsibilities, taxonomy.skills)
                ),
                work_traits=frozenset(
                    _matching_term_ids(responsibilities, taxonomy.traits)
                ),
                required_years=_extract_required_years(requirements),
                education_level=_extract_education(requirements),
            )
        )
    jobs.sort(key=lambda job: (job.company_slug, job.job_id))
    return jobs, excluded


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _term_metrics(
    jobs: list[_AnalyzedJob],
    terms: tuple[TaxonomyTerm, ...],
    field: str,
    work_field: str,
    overall_coverage: dict[str, float] | None = None,
    include_zero: bool = True,
) -> list[dict[str, Any]]:
    requirement_jobs = [job for job in jobs if job.requirements_available]
    requirement_companies = {job.company_slug for job in requirement_jobs}
    metrics: list[dict[str, Any]] = []
    for term in terms:
        matching = [job for job in requirement_jobs if term.id in getattr(job, field)]
        companies = {job.company_slug for job in matching}
        strengths = Counter(getattr(job, field)[term.id] for job in matching)
        work_count = sum(term.id in getattr(job, work_field) for job in jobs)
        if not include_zero and not matching and not work_count:
            continue
        coverage = _ratio(len(matching), len(requirement_jobs))
        item: dict[str, Any] = {
            "id": term.id,
            "name": term.name,
            "category": term.category,
            "job_count": len(matching),
            "job_denominator": len(requirement_jobs),
            "job_coverage": coverage,
            "company_count": len(companies),
            "company_denominator": len(requirement_companies),
            "company_coverage": _ratio(len(companies), len(requirement_companies)),
            "required_count": strengths["required"],
            "preferred_count": strengths["preferred"],
            "unspecified_count": strengths["unspecified"],
            "work_content_job_count": work_count,
        }
        if overall_coverage is not None:
            overall = overall_coverage.get(term.id, 0.0)
            item["specificity"] = round(coverage / overall, 4) if overall else 0.0
        metrics.append(item)
    return sorted(
        metrics, key=lambda item: (-item["job_count"], item["name"], item["id"])
    )


def _group_jobs(
    jobs: list[_AnalyzedJob], dimension: str
) -> dict[tuple[str, str], list[_AnalyzedJob]]:
    groups: dict[tuple[str, str], list[_AnalyzedJob]] = defaultdict(list)
    for job in jobs:
        if dimension == "role_family":
            keys = ((job.role_family_id, job.role_family_name),)
        elif dimension == "company":
            keys = ((job.company_slug, job.company_name),)
        elif dimension == "job_type":
            keys = tuple((value, value) for value in job.job_types)
        elif dimension == "location":
            keys = tuple((value, value) for value in job.locations)
        else:
            raise MarketAnalysisError(f"Unsupported group dimension: {dimension}")
        for key in keys:
            groups[key].append(job)
    return groups


def _build_groups(
    jobs: list[_AnalyzedJob],
    taxonomy: MarketTaxonomy,
    overall_skill_coverage: dict[str, float],
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for dimension in ("role_family", "company", "job_type", "location"):
        items: list[dict[str, Any]] = []
        for (key, name), group_jobs in _group_jobs(jobs, dimension).items():
            requirement_count = sum(job.requirements_available for job in group_jobs)
            domain_signals = _term_metrics(
                group_jobs,
                taxonomy.domain_signals,
                "requirement_domain_signals",
                "work_domain_signals",
                include_zero=False,
            )
            skills = _term_metrics(
                group_jobs,
                taxonomy.skills,
                "requirement_skills",
                "work_skills",
                overall_skill_coverage,
                include_zero=False,
            )
            traits = _term_metrics(
                group_jobs,
                taxonomy.traits,
                "requirement_traits",
                "work_traits",
                include_zero=False,
            )
            items.append(
                {
                    "key": key,
                    "name": name,
                    "job_count": len(group_jobs),
                    "requirements_available_jobs": requirement_count,
                    "requirements_coverage": _ratio(requirement_count, len(group_jobs)),
                    "small_sample": requirement_count < 5,
                    "domain_signals": domain_signals,
                    "skills": skills,
                    "traits": traits,
                }
            )
        result[dimension] = sorted(
            items, key=lambda item: (-item["job_count"], item["name"], item["key"])
        )
    return result


def _skill_combinations(
    jobs: list[_AnalyzedJob], taxonomy: MarketTaxonomy
) -> list[dict[str, Any]]:
    counts: Counter[tuple[str, str]] = Counter()
    companies: dict[tuple[str, str], set[str]] = defaultdict(set)
    for job in jobs:
        for pair in itertools.combinations(sorted(job.requirement_skills), 2):
            counts[pair] += 1
            companies[pair].add(job.company_slug)
    names = {term.id: term.name for term in taxonomy.skills}
    denominator = sum(job.requirements_available for job in jobs)
    items = [
        {
            "skill_ids": list(pair),
            "skill_names": [names[pair[0]], names[pair[1]]],
            "job_count": count,
            "job_denominator": denominator,
            "job_coverage": _ratio(count, denominator),
            "company_count": len(companies[pair]),
        }
        for pair, count in counts.items()
        if count >= 2
    ]
    return sorted(
        items,
        key=lambda item: (
            -item["job_count"],
            -item["company_count"],
            item["skill_ids"],
        ),
    )[:100]


def _parse_fact_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _new_jobs_by_window(jobs: list[_AnalyzedJob], as_of: date) -> dict[str, int]:
    dates: list[date | None] = []
    for job in jobs:
        dates.append(
            _parse_fact_date(job.row.get("published_at"))
            or _parse_fact_date(job.row.get("first_seen_at"))
        )
    result: dict[str, int] = {}
    for days in (7, 30, 90):
        start = as_of - timedelta(days=days - 1)
        result[f"{days}_days"] = sum(
            value is not None and start <= value <= as_of for value in dates
        )
    result["unknown_date"] = sum(value is None for value in dates)
    return result


def _experience_band(years: float | None) -> str:
    if years is None:
        return "未明确"
    if years <= 2:
        return "0-2年"
    if years <= 5:
        return "3-5年"
    if years <= 8:
        return "6-8年"
    return "9年以上"


def _profile_skill_ids(
    profile: RecommendationProfile, taxonomy: MarketTaxonomy
) -> set[str]:
    surface = "\n".join(profile.skills)
    return _matching_term_ids(surface, taxonomy.skills)


def _target_role_family_ids(
    profile: RecommendationProfile, taxonomy: MarketTaxonomy
) -> list[str]:
    surface = " ".join((*profile.target_roles, *profile.roles))
    result: list[str] = []
    for family in taxonomy.role_families:
        if any(_contains_alias(surface, alias) for alias in family.aliases):
            result.append(family.id)
    return result


def _company_is_excluded(
    job_group: dict[str, Any], profile: RecommendationProfile
) -> bool:
    identities = (
        str(job_group["key"]).strip().casefold(),
        str(job_group["name"]).strip().casefold(),
    )
    for raw in profile.excluded_companies:
        excluded = raw.strip().casefold()
        if len(excluded) < 2:
            continue
        for identity in identities:
            if excluded == identity or excluded in identity or identity in excluded:
                return True
    return False


def _market_signal_advice(
    skills: list[dict[str, Any]],
    groups: dict[str, list[dict[str, Any]]],
    jobs: list[_AnalyzedJob],
    profile: RecommendationProfile,
    taxonomy: MarketTaxonomy,
) -> dict[str, Any]:
    covered_ids = _profile_skill_ids(profile, taxonomy)
    target_ids = _target_role_family_ids(profile, taxonomy)
    target_groups = [
        group for group in groups["role_family"] if group["key"] in target_ids
    ]

    target_coverage: dict[str, float] = defaultdict(float)
    for role_group in target_groups:
        for item in role_group["skills"]:
            target_coverage[item["id"]] = max(
                target_coverage[item["id"]], item["job_coverage"]
            )

    priorities: list[dict[str, Any]] = []
    for item in skills:
        if item["job_count"] == 0 or item["id"] in covered_ids:
            continue
        relevant = target_coverage[item["id"]] if target_ids else item["job_coverage"]
        if target_ids and relevant == 0:
            level = "探索"
        elif relevant >= 0.3 and item["company_coverage"] >= 0.3:
            level = "高"
        elif relevant >= 0.15 and item["company_count"] >= 2:
            level = "中"
        else:
            level = "低"
        priorities.append(
            {
                "skill_id": item["id"],
                "skill_name": item["name"],
                "priority": level,
                "market_job_coverage": item["job_coverage"],
                "company_coverage": item["company_coverage"],
                "target_role_coverage": round(relevant, 4),
                "job_count": item["job_count"],
                "company_count": item["company_count"],
                "evidence": (
                    f"{item['job_count']}/{item['job_denominator']} 个有明确要求的岗位，"
                    f"覆盖 {item['company_count']}/{item['company_denominator']} 家公司"
                ),
            }
        )
    priority_order = {"高": 0, "中": 1, "低": 2, "探索": 3}
    priorities.sort(
        key=lambda item: (
            priority_order[item["priority"]],
            -item["target_role_coverage"],
            -item["company_coverage"],
            item["skill_name"],
        )
    )

    def role_advice(group: dict[str, Any]) -> dict[str, Any]:
        top = [item for item in group["skills"] if item["job_count"] > 0][:5]
        top_ids = [item["id"] for item in top]
        covered = [item["id"] for item in top if item["id"] in covered_ids]
        missing = [item["id"] for item in top if item["id"] not in covered_ids]
        ratio = _ratio(len(covered), len(top_ids))
        if ratio >= 0.6:
            action = "优先投递"
        elif ratio >= 0.2:
            action = "补强后投递"
        else:
            action = "探索方向"
        return {
            "role_family_id": group["key"],
            "role_family_name": group["name"],
            "job_count": group["job_count"],
            "requirements_available_jobs": group["requirements_available_jobs"],
            "target_role": group["key"] in target_ids,
            "top_market_skill_ids": top_ids,
            "covered_skill_ids": covered,
            "missing_skill_ids": missing,
            "top_skill_coverage": ratio,
            "action": action,
            "boundary": "仅表示画像关键词对该方向高频技能信号的覆盖，不替代单岗位推荐。",
        }

    role_directions = [
        role_advice(group)
        for group in groups["role_family"]
        if group["requirements_available_jobs"] > 0
    ]
    role_directions.sort(
        key=lambda item: (
            not item["target_role"],
            -item["top_skill_coverage"],
            -item["job_count"],
            item["role_family_name"],
        )
    )

    company_directions = [
        role_advice(
            {
                **group,
                "key": group["key"],
                "name": group["name"],
            }
        )
        for group in groups["company"]
        if group["requirements_available_jobs"] > 0
        and not _company_is_excluded(group, profile)
    ]
    for item in company_directions:
        item["company_slug"] = item.pop("role_family_id")
        item["company_name"] = item.pop("role_family_name")
        item.pop("target_role", None)
        item["action"] = {
            "优先投递": "重点关注",
            "补强后投递": "选择性关注",
            "探索方向": "探索关注",
        }[item["action"]]
        item["boundary"] = "公司内岗位差异较大，必须回到单岗位推荐核对。"
    company_directions.sort(
        key=lambda item: (
            -item["top_skill_coverage"],
            -item["job_count"],
            item["company_name"],
        )
    )

    city_counts = {
        group["key"]: group["job_count"]
        for group in groups["location"]
        if group["key"] in profile.target_cities
    }
    resume_evidence = [
        {
            "skill_id": item["id"],
            "skill_name": item["name"],
            "job_count": item["job_count"],
            "job_denominator": item["job_denominator"],
            "job_coverage": item["job_coverage"],
            "company_count": item["company_count"],
            "company_denominator": item["company_denominator"],
            "suggestion": "仅在真实具备时，用项目任务、个人行动和可验证结果证明该能力。",
        }
        for item in skills
        if item["id"] in covered_ids and item["job_count"] > 0
    ][:10]
    experience_alignment: dict[str, Any] | None = None
    if profile.experience_years is not None:
        known = [job for job in jobs if job.required_years is not None]
        experience_alignment = {
            "profile_experience_years": profile.experience_years,
            "within_profile_experience_jobs": sum(
                job.required_years <= profile.experience_years for job in known
            ),
            "above_profile_experience_jobs": sum(
                job.required_years > profile.experience_years for job in known
            ),
            "unknown_required_experience_jobs": sum(
                job.required_years is None for job in jobs
            ),
        }
    return {
        "covered_skill_ids": sorted(covered_ids),
        "target_role_family_ids": target_ids,
        "target_city_job_counts": city_counts,
        "experience_alignment": experience_alignment,
        "resume_evidence": resume_evidence,
        "learning_priorities": priorities[:20],
        "role_directions": role_directions,
        "company_directions": company_directions[:15],
        "advice_boundary": (
            "建议只基于市场统计和隐私安全画像；实际投递顺序仍以单岗位确定性推荐为准。"
        ),
    }


def _input_fingerprint(rows: list[dict[str, Any]]) -> str:
    encoded = json.dumps(
        rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def analyze_market(
    rows: list[dict[str, Any]],
    taxonomy: MarketTaxonomy,
    *,
    profile: RecommendationProfile | None = None,
    keyword_rules: KeywordRules | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Analyze full exported rows without database, network, or AI access."""
    actual_date = as_of or date.today()
    jobs, excluded = _prepare_jobs(rows, taxonomy)
    requirements_available = sum(job.requirements_available for job in jobs)
    responsibilities_available = sum(job.responsibilities_available for job in jobs)
    companies = {job.company_slug for job in jobs}
    cities = {location for job in jobs for location in job.locations}
    completeness = Counter(
        str(job.row.get("detail_completeness") or "missing") for job in jobs
    )

    domain_signals = _term_metrics(
        jobs,
        taxonomy.domain_signals,
        "requirement_domain_signals",
        "work_domain_signals",
    )
    skills = _term_metrics(
        jobs,
        taxonomy.skills,
        "requirement_skills",
        "work_skills",
    )
    traits = _term_metrics(
        jobs,
        taxonomy.traits,
        "requirement_traits",
        "work_traits",
    )
    overall_skill_coverage = {item["id"]: item["job_coverage"] for item in skills}
    groups = _build_groups(jobs, taxonomy, overall_skill_coverage)
    keyword_definitions = (
        *(
            KeywordDefinition(
                id=term.id,
                name=term.name,
                kind="skill",
                category=term.category,
                aliases=term.aliases,
            )
            for term in taxonomy.skills
        ),
        *(
            KeywordDefinition(
                id=term.id,
                name=term.name,
                kind="domain_signal",
                category=term.category,
                aliases=term.aliases,
            )
            for term in taxonomy.domain_signals
        ),
    )
    keyword_documents = [
        KeywordDocument(
            job_id=job.job_id,
            title=str(job.row.get("title") or ""),
            company_key=job.company_slug,
            company_name=job.company_name,
            role_family_key=job.role_family_id,
            role_family_name=job.role_family_name,
            locations=job.locations,
            requirements=str(job.row.get("requirements") or "").strip(),
            responsibilities=str(job.row.get("responsibilities") or "").strip(),
            requirement_skill_ids=frozenset(job.requirement_skills),
            requirement_domain_signal_ids=frozenset(job.requirement_domain_signals),
            work_skill_ids=job.work_skills,
            work_domain_signal_ids=job.work_domain_signals,
        )
        for job in jobs
    ]
    keyword_analysis = analyze_keywords(
        keyword_documents,
        keyword_definitions,
        keyword_rules or default_keyword_rules(),
    )

    experience = Counter(_experience_band(job.required_years) for job in jobs)
    education = Counter(job.education_level or "未明确" for job in jobs)
    result: dict[str, Any] = {
        "schema_version": 3,
        "taxonomy_version": taxonomy.taxonomy_version,
        "as_of": actual_date.isoformat(),
        "input_fingerprint": _input_fingerprint(rows),
        "sample": {
            "input_jobs": len(rows),
            "analyzed_jobs": len(jobs),
            "selection": "status=active, relevance_status=target, non-algorithm, unique id",
            "excluded": {
                "duplicate": excluded["duplicate"],
                "inactive": excluded["inactive"],
                "non_target": excluded["non_target"],
                "algorithm": excluded["algorithm"],
            },
        },
        "quality": {
            "company_count": len(companies),
            "city_count": len(cities),
            "responsibilities_available_jobs": responsibilities_available,
            "requirements_available_jobs": requirements_available,
            "requirements_unknown_jobs": len(jobs) - requirements_available,
            "requirements_coverage": _ratio(requirements_available, len(jobs)),
            "detail_completeness": dict(sorted(completeness.items())),
        },
        "new_jobs_by_window": _new_jobs_by_window(jobs, actual_date),
        "experience_distribution": dict(sorted(experience.items())),
        "education_distribution": dict(sorted(education.items())),
        "domain_signals": domain_signals,
        "skills": skills,
        "traits": traits,
        "groups": groups,
        "skill_combinations": _skill_combinations(jobs, taxonomy),
        "keyword_analysis": keyword_analysis,
        "personal_advice": None,
        "fact_boundary": (
            "Requirements statistics use explicit requirements text only. "
            "Broad domain mentions are not skills. Missing requirements remain unknown; "
            "responsibilities are reported separately."
        ),
    }
    if profile is not None:
        result["personal_advice"] = _market_signal_advice(
            skills, groups, jobs, profile, taxonomy
        )
    return result


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise MarketAnalysisError(f"Full job export not found: {path}")
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise MarketAnalysisError(
                    f"Invalid JSONL at {path}:{line_number}: {exc}"
                ) from exc
            if not isinstance(value, dict):
                raise MarketAnalysisError(
                    f"Invalid JSONL at {path}:{line_number}: expected object"
                )
            rows.append(value)
    return rows


def _exclusive_temp(parent: Path, tag: str) -> Path:
    descriptor, raw_path = tempfile.mkstemp(
        dir=str(parent), prefix=f".market_{tag}_", suffix=".tmp"
    )
    os.close(descriptor)
    return Path(raw_path)


def write_market_output(
    *,
    json_output: Path,
    json_content: str,
) -> None:
    """Atomically replace the single JSON market report."""
    json_output.parent.mkdir(parents=True, exist_ok=True)
    stage_json = _exclusive_temp(json_output.parent, "json")
    try:
        stage_json.write_text(json_content, encoding="utf-8")
        stage_json.replace(json_output)
    finally:
        stage_json.unlink(missing_ok=True)


def run_market_analysis(
    *,
    jobs_path: Path,
    taxonomy_path: Path,
    keyword_rules_path: Path | None = None,
    json_output: Path,
    profile_path: Path | None = None,
    as_of: date | None = None,
) -> MarketAnalysisRun:
    """Load facts, analyze them, and atomically publish a JSON report."""
    rows = _load_jsonl(jobs_path)
    taxonomy = load_market_taxonomy(taxonomy_path)
    keyword_rules = (
        load_keyword_rules(keyword_rules_path)
        if keyword_rules_path is not None
        else default_keyword_rules()
    )
    profile: RecommendationProfile | None = None
    if profile_path is not None and profile_path.exists():
        profile = load_recommendation_profile(profile_path)
    result = analyze_market(
        rows,
        taxonomy,
        profile=profile,
        keyword_rules=keyword_rules,
        as_of=as_of,
    )
    json_content = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    write_market_output(
        json_output=json_output,
        json_content=json_content,
    )
    return MarketAnalysisRun(
        jobs_path=jobs_path,
        taxonomy_path=taxonomy_path,
        keyword_rules_path=keyword_rules_path,
        profile_used=profile is not None,
        json_output=json_output,
        analyzed_jobs=result["sample"]["analyzed_jobs"],
        requirements_available_jobs=result["quality"]["requirements_available_jobs"],
    )
